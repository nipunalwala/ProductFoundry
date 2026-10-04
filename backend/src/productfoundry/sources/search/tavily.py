"""Tavily Search API (https://docs.tavily.com). One request costs one credit at basic depth."""

from collections.abc import Sequence

import httpx

from productfoundry.sources import SourceError
from productfoundry.sources.search import SearchResult

ENDPOINT = "https://api.tavily.com/search"

# Tavily's `country` takes a country name. Regions not listed here search without it.
_COUNTRIES = {
    "AE": "united arab emirates",
    "AU": "australia",
    "BD": "bangladesh",
    "BR": "brazil",
    "CA": "canada",
    "DE": "germany",
    "ES": "spain",
    "FR": "france",
    "GB": "united kingdom",
    "ID": "indonesia",
    "IN": "india",
    "IT": "italy",
    "JP": "japan",
    "MX": "mexico",
    "NG": "nigeria",
    "NL": "netherlands",
    "PK": "pakistan",
    "SG": "singapore",
    "US": "united states",
    "ZA": "south africa",
}
_LIMIT_STATUSES = {429, 432, 433}


class TavilySearch:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=30)
        self.requests = 0

    def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        region: str | None = None,
        domains: Sequence[str] = (),
    ) -> list[SearchResult]:
        body: dict[str, object] = {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        if domains:
            body["include_domains"] = list(domains)
        if region in _COUNTRIES:
            body["country"] = _COUNTRIES[region]
        self.requests += 1
        try:
            response = self._client.post(
                ENDPOINT, json=body, headers={"Authorization": f"Bearer {self._api_key}"}
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"Tavily request failed: {type(exc).__name__}") from None
        if response.status_code == 401:
            raise SourceError("Tavily rejected the API key (TAVILY_API_KEY)")
        if response.status_code in _LIMIT_STATUSES:
            raise SourceError(f"Tavily limit reached (HTTP {response.status_code})")
        if response.status_code != 200:
            raise SourceError(f"Tavily search failed (HTTP {response.status_code})")
        return [
            SearchResult(item.get("title", ""), item["url"], item.get("content", ""))
            for item in response.json().get("results", [])
            if item.get("url")
        ]
