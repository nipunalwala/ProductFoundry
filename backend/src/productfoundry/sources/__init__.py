"""Adapters for outside data. Only `sources/<name>` knows that source's URLs and payloads."""

from dataclasses import dataclass
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


class AppLookup(Protocol):
    def find(self, name: str, region: str) -> StoreApp | None:
        """The store's app with this name in this region, or None."""
        ...
