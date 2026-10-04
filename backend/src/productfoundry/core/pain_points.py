"""Stage 3 output: the ranked pain-point report. Quotes are review ids, never text."""

from typing import Literal

from pydantic import Field, NonNegativeFloat, NonNegativeInt, PositiveInt, model_validator

from productfoundry.core.base import NonEmptyStr, Schema
from productfoundry.core.ids import ClusterId, ProductId, ReviewId


class PainPoint(Schema):
    cluster_id: ClusterId
    rank: PositiveInt
    label: NonEmptyStr
    description: NonEmptyStr
    severity: int = Field(ge=1, le=5)
    severity_reason: NonEmptyStr
    review_count: PositiveInt
    negative_share: float = Field(ge=0, le=1)
    product_ids: list[ProductId] = Field(min_length=1)
    quote_review_ids: list[ReviewId] = Field(min_length=3, max_length=5)
    score: NonNegativeFloat

    @model_validator(mode="after")
    def _check(self) -> "PainPoint":
        if len(set(self.quote_review_ids)) != len(self.quote_review_ids):
            raise ValueError("quote_review_ids must not repeat")
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("product_ids must not repeat")
        if len(self.quote_review_ids) > self.review_count:
            raise ValueError("a cluster cannot quote more reviews than it holds")
        return self


class JunkCluster(Schema):
    cluster_id: ClusterId
    review_count: PositiveInt
    reason: NonEmptyStr


class LanguageCounts(Schema):
    english: NonNegativeInt
    hinglish: NonNegativeInt
    not_analysed: NonNegativeInt


class PainPointReport(Schema):
    schema_version: Literal[1] = 1
    pain_points: list[PainPoint]
    junk_clusters: list[JunkCluster] = []
    ranking_formula: NonEmptyStr
    language_counts: LanguageCounts

    @model_validator(mode="after")
    def _check(self) -> "PainPointReport":
        if [p.rank for p in self.pain_points] != list(range(1, len(self.pain_points) + 1)):
            raise ValueError("pain points must be ordered by rank: 1, 2, 3, ...")
        ids = [p.cluster_id for p in self.pain_points] + [j.cluster_id for j in self.junk_clusters]
        if len(set(ids)) != len(ids):
            raise ValueError("a cluster appears once, as a pain point or as junk")
        return self
