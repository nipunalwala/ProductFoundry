"""What a product has shipped, from the places it says so in public.

- GitHub releases, through GitHub's REST API (open-source competitors).
- Release-note feeds (RSS or Atom), after checking robots.txt.
- Public changelog pages, read as text after checking robots.txt.
- The "What's new" note of the current version in the App Store (Apple's iTunes
  Lookup API) and on Google Play (the app's details page, which robots.txt allows).

Every adapter returns `RawRelease`s. No author is kept.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from productfoundry.sources import SourceError
from productfoundry.sources.pricing import PageFetcher
from productfoundry.sources.robots import USER_AGENT, Robots

MAX_ITEMS = 30  # newest releases kept per source and fetch


@dataclass(frozen=True)
class RawRelease:
    source_item_id: str
    title: str
    body: str = ""
    version: str | None = None
    released_at: datetime | None = None
    url: str | None = None


class ReleaseSource(Protocol):
    def fetch(self, target: str) -> list[RawRelease]:
        """The newest releases of `target`, newest first."""
        ...


def _get(client: httpx.Client, url: str, what: str, **kwargs: Any) -> httpx.Response:
    try:
        response = client.get(url, **kwargs)
    except httpx.HTTPError as exc:
        raise SourceError(f"{what} failed: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise SourceError(f"{what} failed (HTTP {response.status_code})")
    return response


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


# GitHub


class GitHubReleases:
    """Published releases of a public repository. Drafts are never listed without a token."""

    API = "https://api.github.com/repos/{repo}/releases"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=30)

    def fetch(self, target: str) -> list[RawRelease]:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", target):
            raise SourceError(f"a GitHub source is written owner/repo, not {target!r}")
        response = _get(
            self._client,
            self.API.format(repo=target),
            f"GitHub releases of {target}",
            params={"per_page": MAX_ITEMS},
            headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
        )
        return [
            RawRelease(
                source_item_id=f"{target}#{release['id']}",
                title=release.get("name") or release["tag_name"],
                body=release.get("body") or "",
                version=release["tag_name"],
                released_at=_iso(release.get("published_at")),
                url=release.get("html_url"),
            )
            for release in response.json()
            if not release.get("draft")
        ]


# Feeds

_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<br\s*/?>|</(?:p|li|div|h[1-6])>", re.IGNORECASE)
_SPACE = re.compile(r"[ \t]+")


def _plain(markup: str | None) -> str:
    """Markup as text: tags removed, entities decoded, blank lines collapsed."""
    from html import unescape

    text = unescape(_TAG.sub(" ", _BREAK.sub("\n", markup or "")))
    lines = (_SPACE.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def parse_feed(xml: str) -> list[RawRelease]:
    """The entries of an RSS 2.0 or Atom feed, in feed order."""
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException

    try:
        root = ElementTree.fromstring(xml)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise SourceError(f"the feed is not valid XML: {type(exc).__name__}") from None
    releases = []
    for entry in root.iter(f"{_ATOM}entry"):
        link = next(
            (
                element.get("href")
                for element in entry.findall(f"{_ATOM}link")
                if element.get("rel", "alternate") == "alternate"
            ),
            None,
        )
        title = _plain(entry.findtext(f"{_ATOM}title"))
        identity = entry.findtext(f"{_ATOM}id") or link or title
        if not title or not identity:
            continue
        body = entry.findtext(f"{_ATOM}content") or entry.findtext(f"{_ATOM}summary")
        when = entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated")
        releases.append(
            RawRelease(identity.strip(), title, _plain(body), released_at=_iso(when), url=link)
        )
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip() or None
        title = _plain(item.findtext("title"))
        identity = item.findtext("guid") or link or title
        if not title or not identity:
            continue
        when = None
        if item.findtext("pubDate"):
            try:
                when = _aware(parsedate_to_datetime(item.findtext("pubDate").strip()))
            except (TypeError, ValueError):
                when = None
        body = item.findtext(_CONTENT) or item.findtext("description")
        releases.append(
            RawRelease(identity.strip(), title, _plain(body), released_at=when, url=link)
        )
    return releases[:MAX_ITEMS]


class FeedReleases:
    def __init__(self, robots: Robots, client: httpx.Client | None = None) -> None:
        self._robots = robots
        self._client = client or httpx.Client(timeout=30, follow_redirects=True)

    def fetch(self, target: str) -> list[RawRelease]:
        self._robots.require(target)
        response = _get(
            self._client, target, f"the feed {target}", headers={"User-Agent": USER_AGENT}
        )
        return parse_feed(response.text)


# Changelog pages

_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = re.compile(
    rf"(?P<iso>\d{{4}}-\d{{2}}-\d{{2}})"
    rf"|(?P<mdy>(?P<m1>{_MONTH})\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y1>\d{{4}}))"
    rf"|(?P<dmy>(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m2>{_MONTH}),?\s+(?P<y2>\d{{4}}))",
    re.IGNORECASE,
)
_VERSION = re.compile(r"\bv?(\d+\.\d+(?:\.\d+)*)\b")
_HEADING_CHARS = 80  # a line longer than this is prose that mentions a date, not a heading


def _date_in(line: str) -> datetime | None:
    match = _DATE.search(line)
    if not match:
        return None
    try:
        if match["iso"]:
            return _aware(datetime.fromisoformat(match["iso"]))
        month = (match["m1"] or match["m2"]).lower()[:3]
        day, year = int(match["d1"] or match["d2"]), int(match["y1"] or match["y2"])
        return datetime(year, _MONTHS.index(month) + 1, day, tzinfo=UTC)
    except ValueError:
        return None


def split_changelog(text: str, url: str) -> list[RawRelease]:
    """A changelog page's text as release items. Pure.

    A short line holding a date starts an item; the lines up to the next such
    line are its body. A page with no dated heading gives no items: guessing
    where one release ends would invent releases.
    """
    releases: list[RawRelease] = []
    current: dict[str, Any] | None = None

    def close() -> None:
        if current is None:
            return
        body = "\n".join(current["body"]).strip()
        title = current["title"] or (body.splitlines()[0] if body else current["heading"])
        version = _VERSION.search(current["heading"]) or _VERSION.search(title)
        releases.append(
            RawRelease(
                source_item_id=f"{url}#{current['when']:%Y-%m-%d}#{len(releases)}",
                title=title[:200],
                body=body,
                version=version.group(1) if version else None,
                released_at=current["when"],
                url=url,
            )
        )

    for line in (raw.strip() for raw in text.splitlines()):
        if not line:
            continue
        when = _date_in(line) if len(line) <= _HEADING_CHARS else None
        if when is not None:
            close()
            rest = _DATE.sub("", line).strip(" -–—:|·")
            current = {"heading": line, "when": when, "title": rest, "body": []}
        elif current is not None:
            current["body"].append(line)
    close()
    # Items on the same day are told apart by their position, so the ids stay stable only
    # while the page keeps its order; dates make them stable enough to deduplicate weekly.
    return releases[:MAX_ITEMS]


class PageReleases:
    def __init__(self, fetcher: PageFetcher, robots: Robots) -> None:
        self._fetcher = fetcher
        self._robots = robots

    def fetch(self, target: str) -> list[RawRelease]:
        self._robots.require(target)
        return split_changelog(self._fetcher.fetch(target), target)


# Stores: the current version's note


class AppStoreNotes:
    LOOKUP = "https://itunes.apple.com/lookup"

    def __init__(self, region: str = "us", client: httpx.Client | None = None) -> None:
        self._region = region.lower()
        self._client = client or httpx.Client(timeout=30)

    def fetch(self, target: str) -> list[RawRelease]:
        response = _get(
            self._client,
            self.LOOKUP,
            f"App Store lookup of {target}",
            params={"id": target, "country": self._region},
        )
        releases = []
        for app in response.json().get("results", []):
            notes, version = (app.get("releaseNotes") or "").strip(), app.get("version")
            if not notes or not version:
                continue
            releases.append(
                RawRelease(
                    source_item_id=f"{target}#{version}",
                    title=f"Version {version}",
                    body=notes,
                    version=version,
                    released_at=_iso(app.get("currentVersionReleaseDate")),
                    url=(app.get("trackViewUrl") or "").split("?")[0] or None,
                )
            )
        return releases


FetchPlayApp = Callable[[str, str], dict[str, Any] | None]


class GooglePlayNotes:
    def __init__(self, robots: Robots, fetch_app: FetchPlayApp | None = None, region: str = "us"):
        from productfoundry.sources.google_play import _fetch_app

        self._robots = robots
        self._fetch_app = fetch_app or _fetch_app
        self._region = region.lower()

    def fetch(self, target: str) -> list[RawRelease]:
        from productfoundry.sources.google_play import DETAILS

        self._robots.require(DETAILS.format(app_id=target, country=self._region))
        app = self._fetch_app(target, self._region)
        notes = _plain((app or {}).get("recentChanges"))
        if not app or not notes:
            return []
        updated = app.get("updated")
        when = datetime.fromtimestamp(updated, UTC) if isinstance(updated, int | float) else None
        version = app.get("version") or None
        # Play gives no id for a note: the day it was updated tells two notes apart.
        stamp = f"{when:%Y-%m-%d}" if when else (version or "current")
        return [
            RawRelease(
                source_item_id=f"{target}#{stamp}",
                title=f"Version {version}" if version else "What's new",
                body=notes,
                version=version,
                released_at=when,
                url=f"https://play.google.com/store/apps/details?id={target}",
            )
        ]


class FakeReleases:
    """Serves prepared releases by target and records what was asked for."""

    def __init__(self, releases: dict[str, list[RawRelease]]) -> None:
        self._releases = releases
        self.fetched: list[str] = []

    def fetch(self, target: str) -> list[RawRelease]:
        self.fetched.append(target)
        if target not in self._releases:
            raise SourceError(f"cannot read {target}")
        return list(self._releases[target])
