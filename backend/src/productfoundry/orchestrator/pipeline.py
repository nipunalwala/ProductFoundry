"""The ordered stages of a run and where it pauses for approval."""

from dataclasses import dataclass

from pydantic import BaseModel

from productfoundry.core.acceptance import AcceptanceCriteria
from productfoundry.core.competitors import CompetitorList
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.prd import Prd
from productfoundry.core.reviews import ReviewSet
from productfoundry.core.roadmap import Roadmap
from productfoundry.core.tasks import TaskPlan

# The run stops for approval after these stage numbers (ARCHITECTURE.md section 1).
CHECKPOINT_STAGES: frozenset[int] = frozenset({1, 3, 6})


@dataclass(frozen=True)
class StageSpec:
    number: int
    key: str
    output_model: type[BaseModel]
    pass_through: bool = False  # nothing for the user to decide yet, so no pause

    @property
    def checkpoint(self) -> bool:
        return self.number in CHECKPOINT_STAGES and not self.pass_through


PIPELINE: tuple[StageSpec, ...] = (
    StageSpec(1, "s1_competitors", CompetitorList),
    StageSpec(2, "s2_reviews", ReviewSet),
    StageSpec(3, "s3_pain_points", PainPointReport),
    StageSpec(4, "s4_prd", Prd),
    StageSpec(5, "s5_tasks", TaskPlan),
    # Keeps PRD order until RICE is built (phase 24); its checkpoint is skipped until then.
    StageSpec(6, "s6_roadmap", Roadmap, pass_through=True),
    StageSpec(7, "s7_acceptance", AcceptanceCriteria),
)
