"""The grounding check: every requirement cites evidence that exists in this run."""

from productfoundry.core.competitors import CompetitorList
from productfoundry.core.errors import StageOutputInvalid
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.prd import Prd, market_gaps


def grounding_problems(prd: Prd, report: PainPointReport, competitors: CompetitorList) -> list[str]:
    """Every citation in the PRD that the run's approved pain points and competitors do not back.

    A requirement may cite the cluster of an approved pain point, or a market
    gap, which for now is a competitor fact from stage 1.
    """
    clusters = {point.cluster_id for point in report.pain_points}
    gaps = {gap.id: gap for gap in market_gaps(competitors)}
    problems = []
    for requirement in prd.requirements:
        unknown = [e for e in requirement.evidence if e not in clusters and e not in gaps]
        if unknown:
            problems.append(
                f"{requirement.id} cites evidence that is not in this run: {', '.join(unknown)}"
            )
    for gap in prd.market_gaps:
        if gaps.get(gap.id) != gap:
            problems.append(f"market gap {gap.id} is not a competitor fact of this run")
    return problems


def check_prd(prd: Prd, report: PainPointReport, competitors: CompetitorList) -> None:
    problems = grounding_problems(prd, report, competitors)
    if problems:
        raise StageOutputInvalid("the PRD is not grounded: " + "; ".join(problems))
