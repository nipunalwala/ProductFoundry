from datetime import UTC, datetime

from sqlalchemy import select

from productfoundry.core.errors import RunAlreadyExists, RunNotFound
from productfoundry.orchestrator.state import RunRecord, StageRecord
from productfoundry.storage.db import Sessions
from productfoundry.storage.models import RunRow, StageOutputRow

_RUN_FIELDS = ("seed", "status", "error", "pause_reason", "created_at", "updated_at")
_STAGE_FIELDS = (
    "status",
    "schema_version",
    "output",
    "edited_output",
    "error",
    "started_at",
    "finished_at",
    "approved_at",
)


class PostgresRunStore:
    """A `RunStore` on the `runs` and `stage_outputs` tables."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    def create(self, run: RunRecord) -> None:
        with self._sessions.begin() as session:
            if session.get(RunRow, run.id) is not None:
                raise RunAlreadyExists(f"run {run.id} already exists")
            row = RunRow(id=run.id)
            _write(run, row)
            session.add(row)

    def get(self, run_id: str) -> RunRecord:
        with self._sessions() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise RunNotFound(f"run {run_id} not found")
            return _read(row)

    def save(self, run: RunRecord) -> None:
        with self._sessions.begin() as session:
            row = session.get(RunRow, run.id, with_for_update=True)
            if row is None:
                raise RunNotFound(f"run {run.id} not found")
            _write(run, row)

    def list_runs(self) -> list[RunRecord]:
        with self._sessions() as session:
            rows = session.scalars(select(RunRow).order_by(RunRow.created_at, RunRow.id))
            return [_read(row) for row in rows]


def _write(run: RunRecord, row: RunRow) -> None:
    row.input = run.input.model_dump(mode="json")
    for field in _RUN_FIELDS:
        setattr(row, field, getattr(run, field))
    row.status = run.status.value

    existing = {stage.key: stage for stage in row.stages}
    stages = []
    for position, record in enumerate(run.stages):
        stage = existing.get(record.key) or StageOutputRow(key=record.key)
        stage.position = position
        for field in _STAGE_FIELDS:
            setattr(stage, field, getattr(record, field))
        stage.status = record.status.value
        stages.append(stage)
    row.stages = stages


def _read(row: RunRow) -> RunRecord:
    return RunRecord(
        id=row.id,
        input=row.input,
        stages=[
            StageRecord(
                key=stage.key,
                **{field: _utc(getattr(stage, field)) for field in _STAGE_FIELDS},
            )
            for stage in row.stages
        ],
        **{field: _utc(getattr(row, field)) for field in _RUN_FIELDS},
    )


def _utc(value: object) -> object:
    return value.astimezone(UTC) if isinstance(value, datetime) else value
