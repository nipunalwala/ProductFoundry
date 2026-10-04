"""Stage 4 output: the PRD. Every requirement cites pain-point clusters or market gaps by id."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from productfoundry.core.base import NonEmptyStr, Schema
from productfoundry.core.competitors import CompetitorList
from productfoundry.core.ids import EvidenceId, GapId, ProductId, RequirementId, market_gap_id


class Priority(StrEnum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"


class MarketGap(Schema):
    """A fact about a competitor that a requirement can cite.

    Until the market panel exists (phase 21), the only such fact is what stage 1
    recorded about the competitor.
    """

    id: GapId
    product_id: ProductId
    product_name: NonEmptyStr
    fact: NonEmptyStr


def market_gaps(competitors: CompetitorList) -> list[MarketGap]:
    """The citable competitor facts of a run, one per competitor, with stable ids."""
    return [
        MarketGap(
            id=market_gap_id(competitor.id),
            product_id=competitor.id,
            product_name=competitor.name,
            fact=f"{competitor.positioning} Target users: {competitor.target_users}",
        )
        for competitor in competitors.competitors
    ]


class Requirement(Schema):
    id: RequirementId
    statement: NonEmptyStr
    priority: Priority  # a hint; the roadmap stage decides the order
    evidence: list[EvidenceId] = Field(min_length=1)  # pain-point cluster ids and market gap ids

    @model_validator(mode="after")
    def _check(self) -> "Requirement":
        if len(set(self.evidence)) != len(self.evidence):
            raise ValueError("evidence must not repeat")
        return self


class Prd(Schema):
    schema_version: Literal[1] = 1
    problem: NonEmptyStr
    users: NonEmptyStr
    goals: list[NonEmptyStr] = Field(min_length=1)
    non_goals: list[NonEmptyStr] = []
    requirements: list[Requirement] = Field(min_length=1)
    success_metrics: list[NonEmptyStr] = Field(min_length=1)
    market_gaps: list[MarketGap] = []  # the gaps the requirements cite

    @model_validator(mode="after")
    def _check(self) -> "Prd":
        ids = [requirement.id for requirement in self.requirements]
        if len(set(ids)) != len(ids):
            raise ValueError("requirement ids must be unique")
        gaps = [gap.id for gap in self.market_gaps]
        if len(set(gaps)) != len(gaps):
            raise ValueError("market gap ids must be unique")
        cited = {e for r in self.requirements for e in r.evidence if e.startswith("gap_")}
        if cited != set(gaps):
            raise ValueError("market_gaps must hold exactly the gaps the requirements cite")
        return self
