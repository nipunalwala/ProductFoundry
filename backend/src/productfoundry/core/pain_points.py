"""Stage 3 output: the ranked pain-point report. Quotes are review ids, never text."""

from typing import Literal

from pydantic import Field, NonNegativeFloat, NonNegativeInt, PositiveInt, model_validator

from productfoundry.core.base import NonEmptyStr, Schema
from productfoundry.core.ids import ClusterId, ProductId, ReviewId

RANKING_FORMULA = (
    "score = review_count x (0.5 + 0.5 x negative_share) x severity / 5, highest first; "
    "equal scores are ordered by review_count"
)


def pain_point_score(review_count: int, negative_share: float, severity: int) -> float:
    """The ranking score. Size counts most, a fully negative cluster counts double
    a merely mixed one, and severity (1 to 5) scales the result."""
    return round(review_count * (0.5 + 0.5 * negative_share) * severity / 5, 2)


class PainPoint(Schema):
    cluster_id: ClusterId
    # Clusters the user merged into this one at the checkpoint. Their reviews are counted here.
    merged_cluster_ids: list[ClusterId] = []
    rank: PositiveInt
    label: NonEmptyStr
    description: NonEmptyStr
    severity: int = Field(ge=1, le=5)
    severity_reason: NonEmptyStr
    review_count: PositiveInt
    negative_share: float = Field(ge=0, le=1)
    product_ids: list[ProductId] = Field(min_length=1)
    quote_review_ids: list[ReviewId] = Field(min_length=3, max_length=5)
    # English translation of a Hinglish quote, by review id. The review text is never replaced.
    quote_glosses: dict[ReviewId, NonEmptyStr] = {}
    score: NonNegativeFloat

    @property
    def cluster_ids(self) -> list[str]:
        return [self.cluster_id, *self.merged_cluster_ids]

    @model_validator(mode="after")
    def _check(self) -> "PainPoint":
        if len(set(self.quote_review_ids)) != len(self.quote_review_ids):
            raise ValueError("quote_review_ids must not repeat")
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("product_ids must not repeat")
        if len(self.quote_review_ids) > self.review_count:
            raise ValueError("a cluster cannot quote more reviews than it holds")
        if len(set(self.cluster_ids)) != len(self.cluster_ids):
            raise ValueError("merged_cluster_ids must not repeat or include cluster_id")
        if not set(self.quote_glosses) <= set(self.quote_review_ids):
            raise ValueError("quote_glosses may only translate reviews in quote_review_ids")
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
    schema_version: Literal[2] = 2
    pain_points: list[PainPoint]
    junk_clusters: list[JunkCluster] = []
    ranking_formula: NonEmptyStr
    language_counts: LanguageCounts  # every stored review of the run's products
    clustered_reviews: NonNegativeInt  # negative and mixed reviews that were given to clustering
    noise_reviews: NonNegativeInt  # of those, the ones that fitted no cluster

    @model_validator(mode="after")
    def _check(self) -> "PainPointReport":
        if [p.rank for p in self.pain_points] != list(range(1, len(self.pain_points) + 1)):
            raise ValueError("pain points must be ordered by rank: 1, 2, 3, ...")
        ids = [cluster_id for p in self.pain_points for cluster_id in p.cluster_ids]
        ids += [j.cluster_id for j in self.junk_clusters]
        if len(set(ids)) != len(ids):
            raise ValueError("a cluster appears once, as a pain point or as junk")
        in_clusters = sum(p.review_count for p in self.pain_points)
        in_clusters += sum(j.review_count for j in self.junk_clusters)
        if in_clusters + self.noise_reviews > self.clustered_reviews:
            raise ValueError("clusters and noise hold more reviews than were clustered")
        return self
