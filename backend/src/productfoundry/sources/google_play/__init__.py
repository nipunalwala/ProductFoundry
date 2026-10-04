"""Google Play.

Play's robots.txt disallows `/store/search`, so an app is found by name through
the web search API (restricted to play.google.com) and only its public details
page, which robots.txt allows, is read.
"""

import re
from collections.abc import Callable
from typing import Any

from productfoundry.core.names import same_product
from productfoundry.sources import StoreApp
from productfoundry.sources.robots import Robots
from productfoundry.sources.search import SearchProvider

DETAILS = "https://play.google.com/store/apps/details?id={app_id}&hl=en&gl={country}"
_APP_ID = re.compile(r"play\.google\.com/store/apps/details\?(?:[^#\s]*&)?id=([A-Za-z0-9_.]+)")
_DESCRIPTION_LIMIT = 500
_MAX_PAGES = 2  # details pages read per lookup

FetchApp = Callable[[str, str], dict[str, Any] | None]


def _fetch_app(app_id: str, country: str) -> dict[str, Any] | None:
    from google_play_scraper import app
    from google_play_scraper.exceptions import GooglePlayScraperException

    try:
        return app(app_id, lang="en", country=country)
    except GooglePlayScraperException:
        return None


class GooglePlayLookup:
    def __init__(
        self, search: SearchProvider, robots: Robots, fetch_app: FetchApp = _fetch_app
    ) -> None:
        self._search = search
        self._robots = robots
        self._fetch_app = fetch_app

    def find(self, name: str, region: str) -> StoreApp | None:
        results = self._search.search(
            f"{name} app", max_results=5, region=region, domains=["play.google.com"]
        )
        app_ids = list(dict.fromkeys(m.group(1) for r in results if (m := _APP_ID.search(r.url))))
        country = region.lower()
        for app_id in app_ids[:_MAX_PAGES]:
            url = DETAILS.format(app_id=app_id, country=country)
            self._robots.require(url)
            data = self._fetch_app(app_id, country)
            if data and same_product(name, data.get("title", "")):
                return StoreApp(
                    store_id=app_id,
                    name=data["title"],
                    developer=data.get("developer") or "",
                    url=f"https://play.google.com/store/apps/details?id={app_id}",
                    description=(data.get("summary") or data.get("description") or "")[
                        :_DESCRIPTION_LIMIT
                    ],
                )
        return None
