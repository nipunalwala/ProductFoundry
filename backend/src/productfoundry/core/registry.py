"""Every versioned schema, by name. Used to export JSON Schema."""

from pydantic import BaseModel

from productfoundry.core.acceptance import AcceptanceCriteria
from productfoundry.core.competitors import CompetitorList
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.prd import Prd
from productfoundry.core.pricing import PricingSnapshot
from productfoundry.core.reviews import ReviewSet
from productfoundry.core.roadmap import Roadmap
from productfoundry.core.run_input import RunInput
from productfoundry.core.tasks import TaskPlan
from productfoundry.core.traction import TractionScore

SCHEMAS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        RunInput,
        CompetitorList,
        ReviewSet,
        PainPointReport,
        Prd,
        TaskPlan,
        Roadmap,
        AcceptanceCriteria,
        PricingSnapshot,
        TractionScore,
    )
}
