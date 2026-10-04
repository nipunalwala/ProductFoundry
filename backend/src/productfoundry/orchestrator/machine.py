"""The run state machine. It is the only code that reads or writes run state."""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ValidationError

from productfoundry.core.base import schema_version_of
from productfoundry.core.clock import utcnow
from productfoundry.core.errors import InvalidTransition, QuotaExhausted, StageOutputInvalid
from productfoundry.core.ids import new_id
from productfoundry.core.run_input import RunInput
from productfoundry.orchestrator.pipeline import PIPELINE, StageSpec
from productfoundry.orchestrator.protocols import RunStore, Services, Stage
from productfoundry.orchestrator.state import RunRecord, RunStatus, StageRecord, StageStatus


class Orchestrator:
    def __init__(
        self,
        store: RunStore,
        stages: Mapping[str, Stage],
        *,
        pipeline: Sequence[StageSpec] = PIPELINE,
        services: Services | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        missing = [spec.key for spec in pipeline if spec.key not in stages]
        if missing:
            raise ValueError(f"no stage registered for: {', '.join(missing)}")
        self._store = store
        self._stages = stages
        self._pipeline = tuple(pipeline)
        self._services = services or Services()
        self._clock = clock

    @property
    def stage_keys(self) -> list[str]:
        return [spec.key for spec in self._pipeline]

    def create_run(self, run_input: RunInput, *, seed: int = 0) -> RunRecord:
        now = self._clock()
        run = RunRecord(
            id=new_id("run_"),
            input=run_input,
            seed=seed,
            stages=[StageRecord(key=spec.key) for spec in self._pipeline],
            created_at=now,
            updated_at=now,
        )
        self._store.create(run)
        return run

    def resume(self, run_id: str, *, from_stage: str | None = None) -> RunRecord:
        """Run stages until a checkpoint, a pause, a failure or the end.

        With `from_stage`, that stage is run again and every stage after it is
        invalidated. Without it, a failed run retries its failed stage.
        """
        run = self._store.get(run_id)
        self._add_new_stages(run)
        if from_stage is not None:
            self._invalidate_from(run, from_stage)
        elif run.status is RunStatus.AWAITING_APPROVAL:
            raise InvalidTransition(f"run {run.id} is waiting for approval; approve it first")
        elif run.status is RunStatus.COMPLETED:
            raise InvalidTransition(f"run {run.id} is completed; name a stage to run it again")
        return self._execute(run)

    def approve(self, run_id: str, edited_output: Mapping[str, Any] | None = None) -> RunRecord:
        """Approve the stage at the checkpoint, optionally replacing its output.

        The edit is validated like a stage output. The original output is kept.
        This does not continue the run; call `resume` afterwards.
        """
        run = self._store.get(run_id)
        if run.status is not RunStatus.AWAITING_APPROVAL:
            raise InvalidTransition(f"run {run.id} is {run.status}, not awaiting approval")
        spec = next(
            s for s in self._pipeline if run.stage(s.key).status is StageStatus.AWAITING_APPROVAL
        )
        record = run.stage(spec.key)
        if edited_output is not None:
            try:
                edited = _validate(spec, edited_output)
            except ValidationError as exc:
                raise StageOutputInvalid(
                    f"edited {spec.output_model.__name__} is invalid: {_summarise(exc)}"
                ) from exc
            record.edited_output = edited.model_dump(mode="json")
        record.status = StageStatus.COMPLETED
        record.approved_at = self._clock()
        run.status = RunStatus.RUNNING
        self._save(run)
        return run

    def _add_new_stages(self, run: RunRecord) -> None:
        """A run started before a stage was built gets that stage, pending, at the end."""
        known = {record.key for record in run.stages}
        run.stages += [StageRecord(key=s.key) for s in self._pipeline if s.key not in known]

    def _invalidate_from(self, run: RunRecord, stage_key: str) -> None:
        keys = [spec.key for spec in self._pipeline]
        if stage_key not in keys:
            raise InvalidTransition(f"unknown stage {stage_key!r}; stages are {', '.join(keys)}")
        index = keys.index(stage_key)
        unfinished = [k for k in keys[:index] if run.stage(k).status is not StageStatus.COMPLETED]
        if unfinished:
            raise InvalidTransition(
                f"cannot run {stage_key} before {', '.join(unfinished)} is completed"
            )
        run.stages[index:] = [StageRecord(key=key) for key in keys[index:]]

    def _execute(self, run: RunRecord) -> RunRecord:
        run.error = None
        run.pause_reason = None
        for spec in self._pipeline:
            record = run.stage(spec.key)
            if record.status is StageStatus.COMPLETED:
                continue
            if record.status is StageStatus.AWAITING_APPROVAL:
                run.status = RunStatus.AWAITING_APPROVAL
                self._save(run)
                return run
            self._run_stage(run, spec, record)
            if record.status is not StageStatus.COMPLETED:
                return run
        run.status = RunStatus.COMPLETED
        self._save(run)
        return run

    def _run_stage(self, run: RunRecord, spec: StageSpec, record: StageRecord) -> None:
        record.status = StageStatus.RUNNING
        record.error = None
        record.started_at = self._clock()
        record.finished_at = None
        run.status = RunStatus.RUNNING
        self._save(run)

        try:
            # The stage gets copies: nothing it does to them can reach run state.
            raw = self._stages[spec.key](
                run.input.model_copy(deep=True),
                self._earlier_outputs(run, spec),
                self._services.for_run(run.id, run.seed),
            )
            output = _validate(spec, raw)
        except QuotaExhausted as exc:
            record.status = StageStatus.PENDING
            record.started_at = None
            run.status = RunStatus.PAUSED_QUOTA
            run.pause_reason = str(exc) or "every LLM provider is out of quota"
        except ValidationError as exc:
            self._fail(run, spec, record, f"output failed its schema: {_summarise(exc)}")
        except Exception as exc:
            self._fail(run, spec, record, f"{type(exc).__name__}: {exc}")
        else:
            record.output = output.model_dump(mode="json")
            record.schema_version = schema_version_of(spec.output_model)
            record.finished_at = self._clock()
            if spec.checkpoint:
                record.status = StageStatus.AWAITING_APPROVAL
                run.status = RunStatus.AWAITING_APPROVAL
            else:
                record.status = StageStatus.COMPLETED
        self._save(run)

    def _fail(self, run: RunRecord, spec: StageSpec, record: StageRecord, error: str) -> None:
        record.status = StageStatus.FAILED
        record.error = error
        record.finished_at = self._clock()
        run.status = RunStatus.FAILED
        run.error = f"{spec.key}: {error}"

    def _earlier_outputs(self, run: RunRecord, spec: StageSpec) -> Mapping[str, BaseModel]:
        outputs: dict[str, BaseModel] = {}
        for earlier in self._pipeline:
            if earlier.key == spec.key:
                break
            outputs[earlier.key] = earlier.output_model.model_validate(
                run.stage(earlier.key).effective_output
            )
        return MappingProxyType(outputs)

    def _save(self, run: RunRecord) -> None:
        run.updated_at = self._clock()
        self._store.save(run)


def _validate(spec: StageSpec, raw: object) -> BaseModel:
    """Validate from plain data, so a model built without validation is still checked."""
    if isinstance(raw, BaseModel):
        raw = raw.model_dump(mode="json")
    return spec.output_model.model_validate(raw)


def _summarise(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()
    )
