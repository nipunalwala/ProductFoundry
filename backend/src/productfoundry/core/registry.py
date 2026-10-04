"""Every versioned schema, by name. Used to export JSON Schema."""

from pydantic import BaseModel

from productfoundry.core.competitors import CompetitorList
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.reviews import ReviewSet
from productfoundry.core.run_input import RunInput

SCHEMAS: dict[str, type[BaseModel]] = {
    model.__name__: model for model in (RunInput, CompetitorList, ReviewSet, PainPointReport)
}
