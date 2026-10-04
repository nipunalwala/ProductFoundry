from collections.abc import Mapping, Sequence

from productfoundry.sources.search import SearchResult


class FakeSearch:
    """Answers from a dict of query -> results, and remembers what was asked."""

    def __init__(self, results: Mapping[str, Sequence[SearchResult]]) -> None:
        self._results = results
        self.queries: list[tuple[str, tuple[str, ...]]] = []

    def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        region: str | None = None,
        domains: Sequence[str] = (),
    ) -> list[SearchResult]:
        self.queries.append((query, tuple(domains)))
        return list(self._results.get(query, ()))[:max_results]
