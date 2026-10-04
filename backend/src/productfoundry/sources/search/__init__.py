"""Web search for competitor discovery, through an official API only."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        region: str | None = None,
        domains: Sequence[str] = (),
    ) -> list[SearchResult]:
        """Results for the query. `region` is an ISO country code; `domains` restricts hosts."""
        ...
