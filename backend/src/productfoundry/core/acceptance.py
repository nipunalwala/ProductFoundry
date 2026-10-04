"""Stage 7 output: Given/When/Then acceptance criteria for every task."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from productfoundry.core.base import NonEmptyStr, Schema
from productfoundry.core.ids import TaskId


class CriterionKind(StrEnum):
    HAPPY_PATH = "happy_path"
    EDGE_CASE = "edge_case"
    FAILURE_STATE = "failure_state"


class Criterion(Schema):
    kind: CriterionKind
    given: NonEmptyStr
    when: NonEmptyStr
    then: NonEmptyStr


def kinds_problem(kinds: list[CriterionKind]) -> str | None:
    """Why a task's criteria are not enough, or None: one happy path, one edge or failure."""
    if CriterionKind.HAPPY_PATH not in kinds:
        return "needs at least one happy_path criterion"
    if not {CriterionKind.EDGE_CASE, CriterionKind.FAILURE_STATE}.intersection(kinds):
        return "needs at least one edge_case or failure_state criterion"
    return None


class TaskCriteria(Schema):
    task_id: TaskId
    criteria: list[Criterion] = Field(min_length=2)

    @model_validator(mode="after")
    def _check(self) -> "TaskCriteria":
        problem = kinds_problem([criterion.kind for criterion in self.criteria])
        if problem:
            raise ValueError(f"{self.task_id} {problem}")
        return self


class AcceptanceCriteria(Schema):
    schema_version: Literal[1] = 1
    tasks: list[TaskCriteria] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "AcceptanceCriteria":
        ids = [task.task_id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("each task appears once")
        return self
