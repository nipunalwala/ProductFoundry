"""Stage 4: the approved pain points and the competitor list become a PRD that cites them."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from productfoundry.core.base import NonEmptyStr
from productfoundry.core.competitors import CompetitorList
from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.prd import MarketGap, Prd, Priority, Requirement, market_gaps
from productfoundry.core.run_input import RunInput
from productfoundry.orchestrator.protocols import Services
from productfoundry.stages.s4_prd.validation import check_prd

TASK = "prd"
PROMPT = (Path(__file__).parent.parent / "prompts" / "prd_v1.md").read_text(encoding="utf-8")


class RequirementDraft(BaseModel):
    statement: NonEmptyStr
    priority: Priority
    evidence: list[str] = Field(min_length=1)


class PrdDraft(BaseModel):
    """What the LLM returns. The stage gives the requirements their ids."""

    problem: NonEmptyStr
    users: NonEmptyStr
    goals: list[NonEmptyStr] = Field(min_length=1)
    non_goals: list[NonEmptyStr] = []
    requirements: list[RequirementDraft] = Field(min_length=1)
    success_metrics: list[NonEmptyStr] = Field(min_length=1)


def draft_schema(evidence_ids: Sequence[str]) -> type[PrdDraft]:
    """`PrdDraft` whose requirements may only cite the evidence of this run.

    A requirement citing an id that does not exist fails validation, so the
    gateway retries and then falls back to the next provider.
    """
    known = frozenset(evidence_ids)

    class GroundedPrdDraft(PrdDraft):
        @model_validator(mode="after")
        def _grounded(self) -> "GroundedPrdDraft":
            for number, requirement in enumerate(self.requirements, start=1):
                unknown = [e for e in requirement.evidence if e not in known]
                if unknown:
                    raise ValueError(
                        f"requirement {number} cites evidence that does not exist: {unknown}"
                    )
            return self

    GroundedPrdDraft.__name__ = PrdDraft.__name__
    return GroundedPrdDraft


def _collect_market_gaps(
    competitors: CompetitorList,
    report: PainPointReport,
    services: Services,
) -> dict[str, MarketGap]:
    """Collect all market gaps available at PRD generation time.

    Includes:
    - Competitor-fact gaps (from stage 1)
    - Pricing gaps (from pricing alerts store)
    - Switching gaps (from pain point report)

    Changelog gaps are not included because they require the PRD for matching.
    """
    gaps = {}

    # 1. Competitor-fact gaps (existing)
    for gap in market_gaps(competitors):
        gaps[gap.id] = gap

    # 2. Pricing gaps from alerts
    if services.pricing_alerts:
        # Get alerts for the run's products
        product_ids = [c.id for c in competitors.competitors]
        for product_id in product_ids:
            alerts = services.pricing_alerts.list(product_id)
            for alert in alerts:
                for change in alert.changes:
                    if change.kind in ("price_increased", "plan_removed", "limits_changed"):
                        gap_id = (
                            f"gap_pricing_{alert.product_id}_{change.kind}_{change.plan}_"
                            f"{alert.detected_at.strftime('%Y%m%d')}"
                        )
                        fact_parts = [f"{change.plan}:"]
                        if change.kind == "price_increased":
                            fact_parts.append(
                                f"price rose from {change.old_amount} to {change.new_amount} "
                                f"{change.currency}/{change.period}"
                            )
                        elif change.kind == "plan_removed":
                            fact_parts.append("plan was removed")
                        else:
                            fact_parts.append(
                                f"limits changed from [{'; '.join(change.old_limits)}] "
                                f"to [{'; '.join(change.new_limits)}]"
                            )
                        gaps[gap_id] = MarketGap(
                            id=gap_id,
                            product_id=alert.product_id,
                            product_name=alert.product_name,
                            fact=" ".join(fact_parts),
                        )

    # 3. Switching gaps from pain point report
    for row in report.switching_table:
        if row.from_product and row.to_product:
            gap_id = f"gap_switching_{row.from_product}_{row.to_product}_{row.count}"
            # Use the from_product as the product_id (approximate)
            gaps[gap_id] = MarketGap(
                id=gap_id,
                product_id=row.from_product,  # may not match exact product_id
                product_name=row.from_product,
                fact=f"Users switching from {row.from_product} to {row.to_product}: "
                f"{row.count} reviews cite {', '.join(row.reasons[:3])}",
            )

    return gaps


def prd_stage(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> Prd:
    if services.llm is None:
        raise ProductFoundryError("PRD generation needs the LLM gateway")
    competitors: CompetitorList = earlier_outputs["s1_competitors"]
    report: PainPointReport = earlier_outputs["s3_pain_points"]
    names = {competitor.id: competitor.name for competitor in competitors.competitors}
    gaps = _collect_market_gaps(competitors, report, services)

    payload = {
        "run_input": run_input.model_dump(mode="json", exclude={"schema_version"}),
        "pain_points": [
            {
                "id": point.cluster_id,
                "label": point.label,
                "description": point.description,
                "severity": point.severity,
                "review_count": point.review_count,
                "negative_share": round(point.negative_share, 2),
                "products": [names.get(product, product) for product in point.product_ids],
            }
            for point in report.pain_points
        ],
        "market_gaps": [
            {"id": gap.id, "product": gap.product_name, "fact": gap.fact} for gap in gaps.values()
        ],
    }
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    evidence_ids = [point.cluster_id for point in report.pain_points] + list(gaps)
    draft = services.llm.complete(TASK, messages, draft_schema(evidence_ids))

    requirements = [
        Requirement(
            id=f"req_{number:03d}",
            statement=requirement.statement,
            priority=requirement.priority,
            evidence=list(dict.fromkeys(requirement.evidence)),
        )
        for number, requirement in enumerate(draft.requirements, start=1)
    ]
    cited: list[MarketGap] = [
        gap for gap in gaps.values() if any(gap.id in r.evidence for r in requirements)
    ]
    prd = Prd(
        problem=draft.problem,
        users=draft.users,
        goals=draft.goals,
        non_goals=draft.non_goals,
        requirements=requirements,
        success_metrics=draft.success_metrics,
        market_gaps=cited,
    )

    # Validate with pricing alerts and changelog alerts (if available)
    pricing_alerts_list = []
    if services.pricing_alerts:
        product_ids = [c.id for c in competitors.competitors]
        for product_id in product_ids:
            pricing_alerts_list.extend(services.pricing_alerts.list(product_id))

    changelog_alerts_list = []
    if services.changelog:
        # Note: changelog alerts need the PRD to be matched, so they're not available yet
        # They will be validated in the market panel and gate 2
        pass

    check_prd(prd, report, competitors, pricing_alerts_list, changelog_alerts_list)
    return prd
