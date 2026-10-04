"""Stage 2 output: review counts. The reviews themselves are database rows."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, StringConstraints, model_validator

from productfoundry.core.base import NonEmptyStr, Schema
from productfoundry.core.ids import ProductId


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
