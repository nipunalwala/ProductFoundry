"""Stage 6: the roadmap. A pass-through that keeps PRD order until RICE is built (phase 24)."""

from collections.abc import Mapping

from pydantic import BaseModel

from productfoundry.core.roadmap import Roadmap, prd_order
from productfoundry.core.run_input import RunInput
from productfoundry.orchestrator.protocols import Services


def roadmap_stage(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> Roadmap:
    """No LLM and no outside request: the order is the PRD's."""
    return prd_order(earlier_outputs["s4_prd"], earlier_outputs["s5_tasks"])
