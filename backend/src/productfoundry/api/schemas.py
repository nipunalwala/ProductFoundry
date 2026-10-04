"""What the API sends and receives. These shapes are the frontend's contract (openapi.json)."""

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from productfoundry.core.run_input import RunInput
from productfoundry.orchestrator import PIPELINE, RunRecord, RunStatus, StageStatus

_SPECS = {spec.key: spec for spec in PIPELINE}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRun(_Body):
    input: RunInput
    seed: int = 0


class CompetitorEdits(_Body):
    remove: list[str] = []  # names or product ids
    add: list[dict[str, Any]] = []  # competitors, each with at least a name


class PainPointEdits(_Body):
    """Pain points are named by their rank in the stage's output, or by cluster id."""

    rename: dict[str, str] = {}
    merge: list[list[str]] = []  # each group is folded into its first pain point
    drop: list[str] = []
    rank: list[str] = []  # these first, in this order; the rest follow by score


class Approve(_Body):
    """Approve the checkpoint as it is, with a whole edited output, or with edits."""

    edited_output: dict[str, Any] | None = None
    competitors: CompetitorEdits | None = None
    pain_points: PainPointEdits | None = None


class Resume(_Body):
    from_stage: str | None = None  # run this stage again; later stages are invalidated


class StageView(BaseModel):
    key: str
    number: int | None
    status: StageStatus
    checkpoint: bool
    has_output: bool
    edited: bool
    schema_version: int | None
    error: str | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    approved_at: AwareDatetime | None


class RunSummary(BaseModel):
    id: str
    status: RunStatus
    idea: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class RunView(RunSummary):
    input: RunInput
    seed: int
    error: str | None
    # Set when status is paused_quota: the run will continue, it has not failed.
    pause_reason: str | None
    awaiting_approval: str | None  # the stage whose checkpoint is open
    stages: list[StageView]


class StageOutputView(BaseModel):
    key: str
    status: StageStatus
    schema_version: int | None
    edited: bool
    output: dict[str, Any] | None  # what later stages see: the user's edit when there is one
    original_output: dict[str, Any] | None  # what the stage returned


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class Problem(BaseModel):
    detail: str


def run_summary(run: RunRecord) -> RunSummary:
    return RunSummary(
        id=run.id,
        status=run.status,
        idea=run.input.idea,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def run_view(run: RunRecord) -> RunView:
    stages = []
    for record in run.stages:
        spec = _SPECS.get(record.key)
        stages.append(
            StageView(
                key=record.key,
                number=spec.number if spec else None,
                status=record.status,
                checkpoint=bool(spec and spec.checkpoint),
                has_output=record.output is not None,
                edited=record.edited_output is not None,
                schema_version=record.schema_version,
                error=record.error,
                started_at=record.started_at,
                finished_at=record.finished_at,
                approved_at=record.approved_at,
            )
        )
    awaiting = next((r.key for r in run.stages if r.status is StageStatus.AWAITING_APPROVAL), None)
    return RunView(
        **run_summary(run).model_dump(),
        input=run.input,
        seed=run.seed,
        error=run.error,
        pause_reason=run.pause_reason,
        awaiting_approval=awaiting,
        stages=stages,
    )
