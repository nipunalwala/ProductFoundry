"""App Store reviews from Apple's customer-reviews RSS feed (JSON form).

Apple's robots.txt disallows the feed's path. Reading it is the stated exception
of ARCHITECTURE.md section 8 (decision D8), under its conditions: paged with a
pause, capped, incremental, and no author stored. The feed holds at most the
500 most recent reviews of a country's store.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from productfoundry.sources import RawReview, SourceError

FEED = (
    "https://itunes.apple.com/{country}/rss/customerreviews/"
    "page={page}/id={app_id}/sortby=mostrecent/json"
)
REVIEWS_URL = "https://apps.apple.com/{country}/app/id{app_id}?see-all=reviews"
LAST_PAGE = 10


class AppStoreReviews:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        pause_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(timeout=30)
        self._pause = pause_seconds
        self._sleep = sleep
        self.requests = 0

    def fetch(
        self, store_id: str, region: str, *, since: datetime | None, limit: int
    ) -> list[RawReview]:
        country = region.lower()
        found: dict[str, RawReview] = {}
        for page in range(1, LAST_PAGE + 1):
            if self.requests:
                self._sleep(self._pause)
            self.requests += 1
            entries = self._page(FEED.format(country=country, page=page, app_id=store_id))
            fresh = [review for e in entries if (review := _parse(e, country, store_id, since))]
            for review in fresh:
                found.setdefault(review.source_review_id, review)
            # Newest first: a page with an older review means the rest is known already.
            if not entries or len(fresh) < len(entries) or len(found) >= limit:
                break
        return list(found.values())[:limit]

    def _page(self, url: str) -> list[dict[str, Any]]:
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise SourceError(f"App Store reviews failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise SourceError(f"App Store reviews failed (HTTP {response.status_code})")
        entries = response.json().get("feed", {}).get("entry", [])
        entries = [entries] if isinstance(entries, dict) else entries
        return [entry for entry in entries if "im:rating" in entry]  # skip the app's own entry


def _label(entry: dict[str, Any], key: str) -> str:
    return (entry.get(key) or {}).get("label", "")


def _parse(
    entry: dict[str, Any], country: str, app_id: str, since: datetime | None
) -> RawReview | None:
    """A review without its author. None for one that is not newer than `since`."""
    reviewed_at = datetime.fromisoformat(_label(entry, "updated")).astimezone(UTC)
    if since is not None and reviewed_at <= since:
        return None
    title, body = _label(entry, "title").strip(), _label(entry, "content").strip()
    # The title is part of what the reviewer wrote; keep it unless the body repeats it.
    text = body if not title or body.casefold().startswith(title.casefold()) else f"{title}. {body}"
    rating = _label(entry, "im:rating")
    return RawReview(
        source_review_id=_label(entry, "id"),
        text=text,
        reviewed_at=reviewed_at,
        rating=int(rating) if rating.isdigit() else None,
        url=REVIEWS_URL.format(country=country, app_id=app_id),
    )
