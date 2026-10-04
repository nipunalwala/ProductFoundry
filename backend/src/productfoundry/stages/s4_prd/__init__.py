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


def prd_stage(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> Prd:
    if services.llm is None:
        raise ProductFoundryError("PRD generation needs the LLM gateway")
    competitors: CompetitorList = earlier_outputs["s1_competitors"]
    report: PainPointReport = earlier_outputs["s3_pain_points"]
    names = {competitor.id: competitor.name for competitor in competitors.competitors}
    gaps = {gap.id: gap for gap in market_gaps(competitors)}

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
    check_prd(prd, report, competitors)
    return prd
