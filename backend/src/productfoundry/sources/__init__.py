"""Adapters for outside data. Only `sources/<name>` knows that source's URLs and payloads."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from productfoundry.core.errors import ProductFoundryError


class SourceError(ProductFoundryError):
    """A source refused, failed or is not allowed to be fetched."""


@dataclass(frozen=True)
class StoreApp:
    """An app as a store lists it."""

    store_id: str
    name: str
    developer: str
    url: str
    description: str = ""


@dataclass(frozen=True)
class RawReview:
    """A review as fetched. The author is dropped by the adapter and never gets this far."""

    source_review_id: str
    text: str
    reviewed_at: datetime
    rating: int | None = None
    url: str | None = None


class ReviewSource(Protocol):
    def fetch(
        self, store_id: str, region: str, *, since: datetime | None, limit: int
    ) -> list[RawReview]:
        """Up to `limit` of the newest reviews, only those newer than `since` when given."""
        ...


class AppLookup(Protocol):
    def find(self, name: str, region: str) -> StoreApp | None:
        """The store's app with this name in this region, or None."""
        ...
