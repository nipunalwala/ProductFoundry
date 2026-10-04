"""robots.txt checks. A disallowed page is skipped and reported, never worked around."""

from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from productfoundry.sources import SourceError

USER_AGENT = "ProductFoundry"


def _fetch(url: str) -> str:
    response = httpx.get(url, timeout=15, follow_redirects=True)
    if response.status_code == 404:
        return ""  # no robots.txt: everything is allowed
    if response.status_code != 200:
        raise SourceError(f"cannot read {url} (HTTP {response.status_code})")
    return response.text


class Robots:
    def __init__(self, fetch: Callable[[str], str] = _fetch) -> None:
        self._fetch = fetch
        self._parsers: dict[str, RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._parsers:
            parser = RobotFileParser()
            parser.parse(self._fetch(f"{origin}/robots.txt").splitlines())
            self._parsers[origin] = parser
        return self._parsers[origin].can_fetch(USER_AGENT, url)

    def require(self, url: str) -> None:
        if not self.allowed(url):
            raise SourceError(f"robots.txt disallows fetching {url}")
