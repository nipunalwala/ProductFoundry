"""Run state: the record the orchestrator owns and a `RunStore` persists."""

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict

from productfoundry.core.ids import RunId
from productfoundry.core.run_input import RunInput


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED_QUOTA = "paused_quota"
    FAILED = "failed"
    COMPLETED = "completed"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"
    COMPLETED = "completed"


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    status: StageStatus = StageStatus.PENDING
    schema_version: int | None = None
    output: dict[str, Any] | None = None  # what the stage returned, validated
    edited_output: dict[str, Any] | None = None  # the user's version from a checkpoint
    error: str | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    approved_at: AwareDatetime | None = None

    @property
    def effective_output(self) -> dict[str, Any] | None:
        """What later stages see: the user's edit when there is one."""
        return self.edited_output if self.edited_output is not None else self.output


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: RunId
    input: RunInput
    seed: int
    status: RunStatus = RunStatus.PENDING
    stages: list[StageRecord]
    error: str | None = None
    pause_reason: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    def stage(self, key: str) -> StageRecord:
        for record in self.stages:
            if record.key == key:
                return record
        raise KeyError(key)
