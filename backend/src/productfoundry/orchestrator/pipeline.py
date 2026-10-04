"""The ordered stages of a run and where it pauses for approval."""

from dataclasses import dataclass

from pydantic import BaseModel

from productfoundry.core.competitors import CompetitorList
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.prd import Prd
from productfoundry.core.reviews import ReviewSet

# The run stops for approval after these stage numbers (ARCHITECTURE.md section 1).
CHECKPOINT_STAGES: frozenset[int] = frozenset({1, 3, 6})


@dataclass(frozen=True)
class StageSpec:
    number: int
    key: str
    output_model: type[BaseModel]

    @property
    def checkpoint(self) -> bool:
        return self.number in CHECKPOINT_STAGES


PIPELINE: tuple[StageSpec, ...] = (
    StageSpec(1, "s1_competitors", CompetitorList),
    StageSpec(2, "s2_reviews", ReviewSet),
    StageSpec(3, "s3_pain_points", PainPointReport),
    StageSpec(4, "s4_prd", Prd),
)
