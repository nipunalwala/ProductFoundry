"""Stage 2 output: review counts. The reviews themselves are database rows."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, Field, NonNegativeInt, StringConstraints, model_validator

from productfoundry.core.base import NonEmptyStr, Schema, Url
from productfoundry.core.competitors import Competitor
from productfoundry.core.ids import ProductId, ReviewId


class ReviewSourceName(StrEnum):
    GOOGLE_PLAY = "google_play"
    APP_STORE = "app_store"
    REDDIT = "reddit"
    PRODUCT_HUNT = "product_hunt"


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    MIXED = "mixed"
    NEGATIVE = "negative"


# `en`, `hinglish`, or the detected ISO 639 code (ARCHITECTURE.md section 6.1).
Language = Annotated[str, StringConstraints(pattern=r"^(hinglish|[a-z]{2,3})$")]
ANALYSED_LANGUAGES: frozenset[str] = frozenset({"en", "hinglish"})


class Review(Schema):
    """A stored review. It has no field for a username or a profile link, and takes none."""

    id: ReviewId
    product_id: ProductId
    source: ReviewSourceName
    source_review_id: NonEmptyStr
    url: Url | None = None
    reviewed_at: AwareDatetime  # the source's date
    rating: int | None = Field(default=None, ge=1, le=5)
    language: Language | None = None
    sentiment: Sentiment | None = None
    text: NonEmptyStr


class ReviewStore(Protocol):
    """Where reviews live between runs. Stage 2 writes here; later stages read."""

    def ensure_product(self, competitor: Competitor) -> str:
        """Store the product if it is new. Returns the id its reviews are kept under,
        which is an earlier id when the same store app was already known."""
        ...

    def latest_reviewed_at(self, product_id: str, source: ReviewSourceName) -> datetime | None: ...

    def for_product(self, product_id: str) -> list["Review"]: ...

    def upsert(self, reviews: Iterable["Review"]) -> int:
        """Store reviews, one per (source, source review id). Returns how many were new."""
        ...

    def set_analysis(self, analysis: Mapping[str, tuple[str, Sentiment | None]]) -> None:
        """Set (language, sentiment) by review id."""
        ...

    def without_embedding(self, product_ids: Sequence[str], model: str) -> list["Review"]:
        """Analysed reviews of these products that have no vector from this model."""
        ...

    def set_embeddings(self, vectors: Mapping[str, Sequence[float]], model: str) -> None: ...

    def with_embeddings(
        self, product_ids: Sequence[str], model: str
    ) -> list[tuple["Review", list[float]]]:
        """Reviews of these products with their vector from this model."""
        ...


class ProductReviewCounts(Schema):
    product_id: ProductId
    total: NonNegativeInt
    by_source: dict[ReviewSourceName, NonNegativeInt]
    by_language: dict[Language, NonNegativeInt]
    by_sentiment: dict[Sentiment, NonNegativeInt]  # analysed reviews only
    not_analysed: NonNegativeInt = 0

    @model_validator(mode="after")
    def _check(self) -> "ProductReviewCounts":
        if sum(self.by_source.values()) != self.total:
            raise ValueError("by_source must add up to total")
        if sum(self.by_language.values()) != self.total:
            raise ValueError("by_language must add up to total")
        other = sum(n for lang, n in self.by_language.items() if lang not in ANALYSED_LANGUAGES)
        if other != self.not_analysed:
            raise ValueError("not_analysed must equal the reviews outside English and Hinglish")
        if sum(self.by_sentiment.values()) != self.total - self.not_analysed:
            raise ValueError("by_sentiment must add up to the analysed reviews")
        return self


class ReviewSet(Schema):
    schema_version: Literal[1] = 1
    products: list[ProductReviewCounts] = Field(min_length=1)
    dropped: dict[NonEmptyStr, NonNegativeInt] = {}  # cleaning step -> reviews dropped

    @model_validator(mode="after")
    def _check(self) -> "ReviewSet":
        ids = [p.product_id for p in self.products]
        if len(set(ids)) != len(ids):
            raise ValueError("each product appears once")
        return self

    @property
    def total_reviews(self) -> int:
        return sum(p.total for p in self.products)
