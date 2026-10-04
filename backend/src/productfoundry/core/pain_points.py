"""Stage 3 output: the ranked pain-point report. Quotes are review ids, never text."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    model_validator,
)

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


Month = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]


class TrendSettings(Schema):
    """How trends were computed. Kept in the report so an edit recomputes them the same way."""

    months: PositiveInt  # length of the series, ending at the month of the latest review
    window_months: PositiveInt  # growth compares the last window with the one before
    min_month_reviews: PositiveInt  # a month with fewer reviews in total is marked, not plotted
    min_window_reviews: PositiveInt  # a window with fewer reviews in total gives no growth
    rising_threshold: float = Field(gt=0)  # relative growth above this is flagged as rising


class TrendPoint(Schema):
    month: Month
    reviews: NonNegativeInt  # reviews of this pain point in the month
    total_reviews: NonNegativeInt  # all reviews of the run's products in the month
    enough: bool  # false: too few reviews that month for its share to mean anything

    @model_validator(mode="after")
    def _check(self) -> "TrendPoint":
        if self.reviews > self.total_reviews:
            raise ValueError("a pain point cannot have more reviews in a month than there were")
        return self


class Trend(Schema):
    """The pain point's monthly share of all reviews, and whether it is growing.

    Shares, not raw counts: a release that doubles the reviews doubles every count.
    """

    months: list[TrendPoint]
    recent_share: float | None = Field(default=None, ge=0, le=1)  # over the last window
    previous_share: float | None = Field(default=None, ge=0, le=1)  # over the window before
    # (recent - previous) / previous. None when a window is too thin or previous is zero.
    growth: float | None = None
    rising: bool = False


class SwitchingIntent(StrEnum):
    """What a review says about switching, seen from the product it reviews."""

    LEAVING = "leaving"  # the reviewer is leaving this product
    SWITCHED_TO = "switched_to"  # the reviewer came to this product from another
    SWITCHED_FROM = "switched_from"  # the reviewer has left this product for another
    CONSIDERING = "considering"  # the reviewer is thinking of leaving


class SwitchingReview(Schema):
    review_id: ReviewId
    intent: SwitchingIntent
    # The other product, in the review's own words. None when the review names none.
    other_product: NonEmptyStr | None = None
    reason: NonEmptyStr | None = None  # the LLM's short summary of why


class SwitchingRow(Schema):
    """One line of the switching table: reviews that moved, or may move, the same way."""

    from_product: NonEmptyStr | None  # None: the review does not say where from
    to_product: NonEmptyStr | None  # None: the review does not say where to
    count: PositiveInt
    reasons: list[NonEmptyStr] = []  # the most common reasons first
    review_ids: list[ReviewId] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "SwitchingRow":
        if self.count != len(self.review_ids) or len(set(self.review_ids)) != self.count:
            raise ValueError("count must equal the number of distinct review_ids")
        return self


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
    trend: Trend
    # Reviews of this pain point that talk about switching products.
    switching_review_ids: list[ReviewId] = []

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
        if len(set(self.switching_review_ids)) != len(self.switching_review_ids):
            raise ValueError("switching_review_ids must not repeat")
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
    schema_version: Literal[3] = 3
    pain_points: list[PainPoint]
    junk_clusters: list[JunkCluster] = []
    ranking_formula: NonEmptyStr
    language_counts: LanguageCounts  # every stored review of the run's products
    clustered_reviews: NonNegativeInt  # negative and mixed reviews that were given to clustering
    noise_reviews: NonNegativeInt  # of those, the ones that fitted no cluster
    trend_settings: TrendSettings
    switching_reviews: list[SwitchingReview] = []  # every review classed as about switching
    switching_table: list[SwitchingRow] = []  # those reviews by direction, most common first

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
        switching = [review.review_id for review in self.switching_reviews]
        if len(set(switching)) != len(switching):
            raise ValueError("a review appears once in switching_reviews")
        known = set(switching)
        for point in self.pain_points:
            if not known.issuperset(point.switching_review_ids):
                raise ValueError(
                    f"{point.cluster_id} lists switching reviews that are not in switching_reviews"
                )
        for row in self.switching_table:
            if not known.issuperset(row.review_ids):
                raise ValueError("the switching table cites reviews not in switching_reviews")
        return self
