"""Public pricing pages, read as the text a visitor sees.

robots.txt is checked before every fetch. A page that disallows fetching is not
fetched: the caller gets a `SourceError` to report, never a workaround.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Protocol

from productfoundry.core.clock import utcnow
from productfoundry.sources import SourceError
from productfoundry.sources.robots import Robots

PAGE_TIMEOUT_MS = 30_000
SETTLE_MS = 1_500  # prices are often filled in by a script after the page loads


class PageFetcher(Protocol):
    def fetch(self, url: str) -> str:
        """The visible text of the rendered page."""
        ...


@dataclass(frozen=True)
class PricingPage:
    url: str
    text: str
    fetched_at: datetime

    @property
    def text_hash(self) -> str:
        return sha256(self.text.encode()).hexdigest()


def read_page(
    url: str, fetcher: PageFetcher, robots: Robots, now: Callable[[], datetime] = utcnow
) -> PricingPage:
    """The page's text, when robots.txt allows fetching it and it has any."""
    robots.require(url)
    # Lines are trimmed and empty ones dropped, so that layout noise does not change the hash.
    lines = (line.strip() for line in fetcher.fetch(url).splitlines())
    text = "\n".join(line for line in lines if line)
    if not text:
        raise SourceError(f"{url} rendered no text")
    return PricingPage(url=url, text=text, fetched_at=now())


class PlaywrightFetcher:
    """A headless browser, because pricing pages are often rendered by scripts.

    `channel` names an installed browser ("msedge", "chrome"); without it
    Playwright's own Chromium is used, which `playwright install chromium` provides.
    """

    def __init__(self, channel: str | None = None) -> None:
        self._channel = channel

    def fetch(self, url: str) -> str:
        from playwright.sync_api import Error, sync_playwright

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=self._channel, headless=True)
                try:
                    page = browser.new_page()
                    response = page.goto(
                        url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS
                    )
                    if response is not None and response.status >= 400:
                        raise SourceError(f"cannot read {url} (HTTP {response.status})")
                    page.wait_for_timeout(SETTLE_MS)
                    return page.inner_text("body")
                finally:
                    browser.close()
        except Error as exc:
            raise SourceError(f"cannot read {url}: {str(exc).splitlines()[0]}") from exc


class FakeFetcher:
    """Serves saved page texts by URL and records what was asked for."""

    def __init__(self, pages: Mapping[str, str]) -> None:
        self._pages = dict(pages)
        self.fetched: list[str] = []

    def fetch(self, url: str) -> str:
        self.fetched.append(url)
        if url not in self._pages:
            raise SourceError(f"cannot read {url} (HTTP 404)")
        return self._pages[url]
