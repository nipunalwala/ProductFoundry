"""Stage 6 output: the roadmap.

Until RICE prioritization is built (phase 24) the roadmap is a pass-through: the
PRD's requirements in the PRD's order, each with the tasks that serve it.
"""

from typing import Literal

from pydantic import Field, PositiveInt, model_validator

from productfoundry.core.base import Schema
from productfoundry.core.ids import RequirementId, TaskId
from productfoundry.core.prd import Prd
from productfoundry.core.tasks import TaskPlan


class RoadmapItem(Schema):
    rank: PositiveInt
    requirement_id: RequirementId
    task_ids: list[TaskId] = Field(min_length=1)


class Roadmap(Schema):
    schema_version: Literal[1] = 1
    ordering: Literal["prd_order"] = "prd_order"  # how the items were ranked
    items: list[RoadmapItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "Roadmap":
        if [item.rank for item in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("items must be ordered by rank: 1, 2, 3, ...")
        ids = [item.requirement_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("each requirement appears once")
        return self


def prd_order(prd: Prd, plan: TaskPlan) -> Roadmap:
    """The requirements in PRD order, each with its tasks in plan order."""
    return Roadmap(
        items=[
            RoadmapItem(
                rank=rank,
                requirement_id=requirement.id,
                task_ids=[t.id for t in plan.tasks if requirement.id in t.requirement_ids],
            )
            for rank, requirement in enumerate(prd.requirements, start=1)
        ]
    )
