"""Signals of a product's traction, read from public places.

- Google Trends, through the unofficial `pytrends` library: weekly search
  interest over the past year. The values are relative (0 to 100 within the
  query), never search counts.
- Store figures: Google Play's install range and rating count (the app's details
  page, which robots.txt allows) and the App Store's rating count (Apple's
  iTunes Lookup API).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from productfoundry.sources import SourceError
from productfoundry.sources.robots import Robots


class SearchInterest(Protocol):
    def weekly(self, term: str, region: str | None) -> list[tuple[str, int]]:
        """(week start "YYYY-MM-DD", relative interest 0-100) over the past 12 months,
        oldest first. `region` is a country code, or None for worldwide."""
        ...


class GoogleTrends:
    def weekly(self, term: str, region: str | None) -> list[tuple[str, int]]:
        from pytrends.exceptions import ResponseError
        from pytrends.request import TrendReq
        from requests.exceptions import RequestException

        try:
            client = TrendReq(hl="en-US", tz=0, timeout=(10, 30))
            client.build_payload([term], timeframe="today 12-m", geo=(region or "").upper())
            frame = client.interest_over_time()
        except (ResponseError, RequestException) as exc:
            raise SourceError(f"Google Trends failed: {type(exc).__name__}") from None
        if frame.empty or term not in frame:
            return []
        # The last row is the running week when `isPartial` is set: half a week is not a week.
        if "isPartial" in frame:
            frame = frame[~frame["isPartial"].astype(bool)]
        return [(f"{week:%Y-%m-%d}", int(value)) for week, value in frame[term].items()]


class FakeSearchInterest:
    def __init__(self, series: dict[str, list[tuple[str, int]]]) -> None:
        self._series = series
        self.asked: list[tuple[str, str | None]] = []

    def weekly(self, term: str, region: str | None) -> list[tuple[str, int]]:
        self.asked.append((term, region))
        if term not in self._series:
            raise SourceError("Google Trends failed: ResponseError")
        return list(self._series[term])


@dataclass(frozen=True)
class StoreFigures:
    """What a store shows about an app's size. None: the store does not say."""

    store: str  # "Google Play" or "App Store"
    min_installs: int | None = None
    rating_count: int | None = None


class StoreStats(Protocol):
    def figures(self, store_id: str) -> StoreFigures | None: ...


FetchPlayApp = Callable[[str, str], dict[str, Any] | None]


class GooglePlayStats:
    def __init__(self, robots: Robots, fetch_app: FetchPlayApp | None = None, region: str = "us"):
        from productfoundry.sources.google_play import _fetch_app

        self._robots = robots
        self._fetch_app = fetch_app or _fetch_app
        self._region = region.lower()

    def figures(self, store_id: str) -> StoreFigures | None:
        from productfoundry.sources.google_play import DETAILS

        self._robots.require(DETAILS.format(app_id=store_id, country=self._region))
        app = self._fetch_app(store_id, self._region)
        if not app:
            return None
        installs = app.get("minInstalls")
        return StoreFigures(
            store="Google Play",
            min_installs=installs if isinstance(installs, int) else None,
            rating_count=app.get("ratings") if isinstance(app.get("ratings"), int) else None,
        )


class AppStoreStats:
    LOOKUP = "https://itunes.apple.com/lookup"

    def __init__(self, region: str = "us", client: httpx.Client | None = None) -> None:
        self._region = region.lower()
        self._client = client or httpx.Client(timeout=30)

    def figures(self, store_id: str) -> StoreFigures | None:
        try:
            response = self._client.get(
                self.LOOKUP, params={"id": store_id, "country": self._region}
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"App Store lookup failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise SourceError(f"App Store lookup failed (HTTP {response.status_code})")
        results = response.json().get("results", [])
        if not results:
            return None
        count = results[0].get("userRatingCount")
        return StoreFigures(
            store="App Store", rating_count=count if isinstance(count, int) else None
        )
