"""Stage 5 output: epics and tasks. Every task names the requirements it serves."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from productfoundry.core.base import NonEmptyStr, Schema
from productfoundry.core.ids import EpicId, RequirementId, TaskId


class Effort(StrEnum):
    """A fixed scale for one developer. Anything larger than XL is split into tasks."""

    XS = "XS"  # up to half a day
    S = "S"  # about a day
    M = "M"  # two or three days
    L = "L"  # about a week
    XL = "XL"  # about two weeks


def dependency_order(depends_on: Mapping[str, Sequence[str]]) -> list[str]:
    """The keys ordered so that each comes after everything it depends on.

    Keys keep their given order wherever the dependencies allow it. Raises
    `ValueError` naming the tasks involved when a dependency is unknown, points
    at itself, or closes a cycle.
    """
    for key, needed in depends_on.items():
        unknown = [other for other in needed if other not in depends_on]
        if unknown:
            raise ValueError(f"{key} depends on tasks that do not exist: {', '.join(unknown)}")
        if key in needed:
            raise ValueError(f"{key} depends on itself")
    ordered: list[str] = []
    placed: set[str] = set()
    remaining = list(depends_on)
    while remaining:
        ready = [key for key in remaining if all(other in placed for other in depends_on[key])]
        if not ready:
            raise ValueError(f"dependencies form a cycle among: {', '.join(remaining)}")
        ordered.append(ready[0])
        placed.add(ready[0])
        remaining.remove(ready[0])
    return ordered


class Task(Schema):
    id: TaskId
    title: NonEmptyStr
    description: NonEmptyStr
    requirement_ids: list[RequirementId] = Field(min_length=1)
    depends_on: list[TaskId] = []
    effort: Effort

    @model_validator(mode="after")
    def _check(self) -> "Task":
        if len(set(self.requirement_ids)) != len(self.requirement_ids):
            raise ValueError("requirement_ids must not repeat")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on must not repeat")
        return self


class Epic(Schema):
    id: EpicId
    title: NonEmptyStr
    description: NonEmptyStr
    tasks: list[Task] = Field(min_length=1)


class TaskPlan(Schema):
    schema_version: Literal[1] = 1
    epics: list[Epic] = Field(min_length=1)

    @property
    def tasks(self) -> list[Task]:
        return [task for epic in self.epics for task in epic.tasks]

    def in_dependency_order(self) -> list[Task]:
        """Every task after the tasks it depends on, otherwise in plan order."""
        tasks = {task.id: task for task in self.tasks}
        order = dependency_order({task.id: task.depends_on for task in tasks.values()})
        return [tasks[task_id] for task_id in order]

    @model_validator(mode="after")
    def _check(self) -> "TaskPlan":
        epics = [epic.id for epic in self.epics]
        if len(set(epics)) != len(epics):
            raise ValueError("epic ids must be unique")
        ids = [task.id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task ids must be unique")
        dependency_order({task.id: task.depends_on for task in self.tasks})
        return self
