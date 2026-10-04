"""Apple App Store. Lookup uses Apple's documented iTunes Search API."""

import httpx

from productfoundry.core.names import same_product
from productfoundry.sources import SourceError, StoreApp

SEARCH = "https://itunes.apple.com/search"
_DESCRIPTION_LIMIT = 500


class AppStoreLookup:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=30)

    def find(self, name: str, region: str) -> StoreApp | None:
        params = {"term": name, "entity": "software", "country": region.lower(), "limit": 5}
        try:
            response = self._client.get(SEARCH, params=params)
        except httpx.HTTPError as exc:
            raise SourceError(f"App Store search failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise SourceError(f"App Store search failed (HTTP {response.status_code})")
        for item in response.json().get("results", []):
            if same_product(name, item.get("trackName", "")):
                return StoreApp(
                    store_id=str(item["trackId"]),
                    name=item["trackName"],
                    developer=item.get("sellerName", ""),
                    url=item["trackViewUrl"].split("?")[0],
                    description=item.get("description", "")[:_DESCRIPTION_LIMIT],
                )
        return None
