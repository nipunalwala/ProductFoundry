"""Google Play reviews through the scraper library.

Play's robots.txt disallows the paths the library uses for reviews. Fetching
them is the stated exception of ARCHITECTURE.md section 8 (decision D8), under
its conditions: paged with a pause, capped, incremental, and no author stored.
"""

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from productfoundry.sources import RawReview, SourceError

PAGE_SIZE = 100
REVIEW_URL = "https://play.google.com/store/apps/details?id={app_id}&reviewId={review_id}"

# (app_id, lang, country, count, continuation_token) -> (reviews, next_token)
FetchPage = Callable[[str, str, str, int, Any], tuple[list[dict[str, Any]], Any]]


def _fetch_page(
    app_id: str, lang: str, country: str, count: int, token: Any
) -> tuple[list[dict[str, Any]], Any]:
    from google_play_scraper import Sort, reviews

    return reviews(
        app_id, lang=lang, country=country, sort=Sort.NEWEST, count=count, continuation_token=token
    )


class GooglePlayReviews:
    def __init__(
        self,
        fetch_page: FetchPage = _fetch_page,
        *,
        languages: Sequence[str] = ("en", "hi"),
        pause_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetch_page = fetch_page
        # Play files each review under one interface language. Hinglish reviews
        # sit under both English and Hindi, so both are read.
        self._languages = tuple(languages)
        self._pause = pause_seconds
        self._sleep = sleep
        self.requests = 0

    def fetch(
        self, store_id: str, region: str, *, since: datetime | None, limit: int
    ) -> list[RawReview]:
        found: dict[str, RawReview] = {}
        per_language = max(limit // len(self._languages), 1)
        for language in self._languages:
            taken = 0
            token = None
            while taken < per_language:
                if self.requests:
                    self._sleep(self._pause)
                self.requests += 1
                try:
                    page, token = self._fetch_page(
                        store_id,
                        language,
                        region.lower(),
                        min(PAGE_SIZE, per_language - taken),
                        token,
                    )
                except Exception as exc:
                    raise SourceError(
                        f"Google Play reviews for {store_id} failed: {type(exc).__name__}"
                    ) from None
                fresh = [review for item in page if (review := _parse(store_id, item, since))]
                for review in fresh:
                    found.setdefault(review.source_review_id, review)
                taken += len(page)
                # Newest first: a page with an older review means the rest is known already.
                if not page or len(fresh) < len(page) or _exhausted(token):
                    break
        newest_first = sorted(found.values(), key=lambda review: review.reviewed_at, reverse=True)
        return newest_first[:limit]


def _exhausted(token: Any) -> bool:
    return token is None or getattr(token, "token", token) is None


def _parse(app_id: str, item: dict[str, Any], since: datetime | None) -> RawReview | None:
    """A review without its author. None for one that is not newer than `since`."""
    at = item["at"]
    reviewed_at = (at if at.tzinfo else at.astimezone()).astimezone(UTC)
    if since is not None and reviewed_at <= since:
        return None
    return RawReview(
        source_review_id=item["reviewId"],
        text=item.get("content") or "",
        reviewed_at=reviewed_at,
        rating=item.get("score") or None,
        url=REVIEW_URL.format(app_id=app_id, review_id=item["reviewId"]),
    )
