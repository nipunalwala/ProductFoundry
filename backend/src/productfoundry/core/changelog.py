"""What competitors ship: release items, their match to a run's pain points and
requirements, and the alerts that follow.

A match is evidence too: it names the release item and the clusters or
requirements it touches by id, and every id must exist.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal, Protocol

from pydantic import Field, StringConstraints, model_validator

from productfoundry.core.base import NonEmptyStr, Schema, Url
from productfoundry.core.ids import ClusterId, ProductId, RequirementId
from productfoundry.core.pain_points import PainPointReport, Trend, TrendSettings
from productfoundry.core.prd import Prd

ReleaseId = Annotated[str, StringConstraints(pattern=r"^rel_[A-Za-z0-9]+$")]
BODY_CHARS = 2000  # a longer release note is cut before it is stored


class ChangelogSourceKind(StrEnum):
    GITHUB = "github"  # releases of a public repository: target is "owner/repo"
    FEED = "feed"  # an RSS or Atom release-note feed: target is its URL
    PAGE = "page"  # a public changelog page: target is its URL
    APP_STORE = "app_store"  # the "What's New" note: target is the track id
    GOOGLE_PLAY = "google_play"  # the "What's new" note: target is the package name


def release_id(source: str, source_item_id: str) -> str:
    """The same release from the same source always gets the same id."""
    digest = sha256(f"{source}\x00{source_item_id}".encode()).hexdigest()
    return f"rel_{digest[:20]}"


class ChangelogSource(Schema):
    """Where a product's releases are read from. Tracked sources are read weekly."""

    product_id: ProductId
    kind: ChangelogSourceKind
    target: NonEmptyStr


class ChangelogItem(Schema):
    id: ReleaseId
    product_id: ProductId
    source: ChangelogSourceKind
    title: NonEmptyStr
    body: str = Field(default="", max_length=BODY_CHARS)
    version: str | None = None
    released_at: datetime | None = None  # None: the source gives no date
    url: Url | None = None
    fetched_at: datetime


class ReleaseKind(StrEnum):
    FIX = "fix"  # repairs or removes a problem users had
    FEATURE = "feature"  # adds something users could not do before
    OTHER = "other"  # maintenance, wording, anything that changes nothing for a user


class ChangelogMatch(Schema):
    """What one release item means for one run. Empty lists mean it matches nothing."""

    item_id: ReleaseId
    kind: ReleaseKind
    cluster_ids: list[ClusterId] = []  # pain points of the run the item addresses
    requirement_ids: list[RequirementId] = []  # PRD requirements the item delivers
    reason: NonEmptyStr

    @model_validator(mode="after")
    def _check(self) -> "ChangelogMatch":
        for ids in (self.cluster_ids, self.requirement_ids):
            if len(set(ids)) != len(ids):
                raise ValueError("a match names each id once")
        return self


def match_problems(
    matches: Sequence[ChangelogMatch],
    items: Mapping[str, ChangelogItem],
    report: PainPointReport,
    prd: Prd,
) -> list[str]:
    """Every citation in `matches` that does not exist: the release item, a pain-point
    cluster of the run's approved report, or a requirement of its PRD."""
    clusters = {point.cluster_id for point in report.pain_points}
    requirements = {requirement.id for requirement in prd.requirements}
    problems = []
    for match in matches:
        if match.item_id not in items:
            problems.append(f"{match.item_id} is not a stored release item")
        problems.extend(
            f"{match.item_id} cites {cluster}, which is not a pain point of this run"
            for cluster in match.cluster_ids
            if cluster not in clusters
        )
        problems.extend(
            f"{match.item_id} cites {requirement}, which is not a requirement of this run"
            for requirement in match.requirement_ids
            if requirement not in requirements
        )
    return problems


class FollowUp(Schema):
    """A pain point's share of all reviews before and after a release."""

    months: int  # months compared on each side of the release month
    share_before: float | None = None
    share_after: float | None = None  # None: too few reviews since the release to say
    change: float | None = None  # (after - before) / before


def follow_up(trend: Trend, released_at: datetime | None, settings: TrendSettings) -> FollowUp:
    """Did the complaint fade after the release? Pure: read from the trend's monthly counts.

    The release month itself is left out, since it holds reviews from both sides.
    A side with fewer reviews than the trend's window minimum gives no share.
    """
    window = settings.window_months
    if released_at is None:
        return FollowUp(months=window)
    month = f"{released_at:%Y-%m}"
    before = [point for point in trend.months if point.month < month][-window:]
    after = [point for point in trend.months if point.month > month][:window]

    def share(points) -> float | None:
        total = sum(point.total_reviews for point in points)
        if total < settings.min_window_reviews:
            return None
        return round(sum(point.reviews for point in points) / total, 4)

    earlier, later = share(before), share(after)
    change = None
    if earlier and later is not None:
        change = round((later - earlier) / earlier, 4)
    return FollowUp(months=window, share_before=earlier, share_after=later, change=change)


class AlertKind(StrEnum):
    SHIPPED_FIX = "shipped_fix"  # a competitor shipped something for a pain point we target
    UNPLANNED_FEATURE = "unplanned_feature"  # a new feature nothing in our PRD covers


class ChangelogAlert(Schema):
    kind: AlertKind
    item: ChangelogItem
    product_name: NonEmptyStr
    cluster_ids: list[ClusterId] = []
    pain_point_labels: list[NonEmptyStr] = []
    requirement_ids: list[RequirementId] = []
    reason: NonEmptyStr
    # For shipped_fix: each cited pain point's trend around the release, by cluster id.
    follow_ups: dict[str, FollowUp] = {}

    @model_validator(mode="after")
    def _check(self) -> "ChangelogAlert":
        if self.kind is AlertKind.SHIPPED_FIX and not self.cluster_ids:
            raise ValueError("a shipped_fix alert cites the pain point that was fixed")
        if self.kind is AlertKind.UNPLANNED_FEATURE and (self.cluster_ids or self.requirement_ids):
            raise ValueError("an unplanned_feature alert matches nothing in the run")
        return self


class ChangelogAlerts(Schema):
    schema_version: Literal[1] = 1
    run_id: NonEmptyStr
    alerts: list[ChangelogAlert]
    items_matched: int  # release items the run has been compared with


def build_alerts(
    matches: Sequence[ChangelogMatch],
    items: Mapping[str, ChangelogItem],
    product_names: Mapping[str, str],
    report: PainPointReport,
) -> list[ChangelogAlert]:
    """The two alerts, newest release first. Pure.

    - `shipped_fix`: the item addresses at least one pain point of the run.
    - `unplanned_feature`: the item is a new feature that matches no pain point
      and no requirement.
    An item that only delivers one of our requirements, or that changes nothing
    for a user, raises no alert.
    """
    points = {point.cluster_id: point for point in report.pain_points}
    alerts = []
    for match in matches:
        item = items[match.item_id]
        name = product_names.get(item.product_id, item.product_id)
        if match.cluster_ids:
            alerts.append(
                ChangelogAlert(
                    kind=AlertKind.SHIPPED_FIX,
                    item=item,
                    product_name=name,
                    cluster_ids=match.cluster_ids,
                    pain_point_labels=[points[c].label for c in match.cluster_ids],
                    requirement_ids=match.requirement_ids,
                    reason=match.reason,
                    follow_ups={
                        cluster: follow_up(
                            points[cluster].trend, item.released_at, report.trend_settings
                        )
                        for cluster in match.cluster_ids
                    },
                )
            )
        elif match.kind is ReleaseKind.FEATURE and not match.requirement_ids:
            alerts.append(
                ChangelogAlert(
                    kind=AlertKind.UNPLANNED_FEATURE,
                    item=item,
                    product_name=name,
                    reason=match.reason,
                )
            )
    alerts.sort(
        key=lambda alert: (alert.item.released_at or alert.item.fetched_at, alert.item.id),
        reverse=True,
    )
    return alerts


class ChangelogStore(Protocol):
    def track(self, source: ChangelogSource, product_name: str) -> None: ...

    def sources(self) -> list[ChangelogSource]: ...

    def product_names(self) -> dict[str, str]: ...

    def add_items(self, items: Sequence[ChangelogItem]) -> int:
        """Store the items that are new. Returns how many were."""
        ...

    def items(self, product_ids: Sequence[str]) -> list[ChangelogItem]:
        """The products' release items, newest first."""
        ...

    def save_matches(self, run_id: str, matches: Sequence[ChangelogMatch]) -> None: ...

    def matches(self, run_id: str) -> list[ChangelogMatch]: ...
