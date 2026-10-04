"""Is a product growing, flat or declining? A label from public signals, with its reasons.

Every signal is weak on its own, so the label is never better than "medium"
confidence, and it always says which signals it used and which were missing.
The scoring is pure: observations in, a `TractionScore` out.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import Field

from productfoundry.core.base import NonEmptyStr, Schema
from productfoundry.core.ids import ProductId


class SignalKind(StrEnum):
    SEARCH_INTEREST = "search_interest"  # Google Trends: relative values, 0 to 100
    DOWNLOADS = "downloads"  # Google Play's install range, by its lower bound
    RATING_COUNT = "rating_count"  # how many ratings the stores show, in total
    REVIEW_VOLUME = "review_volume"  # stored reviews written per month


SCORED = tuple(SignalKind)


class Direction(StrEnum):
    GROWING = "growing"
    FLAT = "flat"
    DECLINING = "declining"


class Observation(Schema):
    """One stored row of `traction_signals`: what a source showed at one moment."""

    product_id: ProductId
    signal: SignalKind
    value: float  # the search-interest change, the install bound, the rating count
    source: NonEmptyStr  # where it was read: "Google Trends", "Google Play", ...
    observed_at: datetime
    detail: dict[str, Any] = {}  # the series or the raw figures behind the value


class Reading(Schema):
    """What one signal says, and on what basis."""

    signal: SignalKind
    direction: Direction
    change: float | None = None  # relative change behind the direction, when there is one
    basis: NonEmptyStr
    source: NonEmptyStr
    observed_at: datetime


class Missing(Schema):
    signal: SignalKind
    reason: NonEmptyStr


class TractionScore(Schema):
    schema_version: Literal[1] = 1
    product_id: ProductId
    label: Direction | None  # None: not enough data to say
    confidence: Literal["medium", "low"] | None  # never "high": every signal is indirect
    readings: list[Reading]
    missing: list[Missing]
    sources: list[NonEmptyStr]  # every source a reading came from
    note: NonEmptyStr


class TractionSettings(Schema):
    change_threshold: float = Field(default=0.15, gt=0)  # a smaller relative change is flat
    interest_weeks: int = Field(default=13, gt=1)  # each side of the search-interest comparison
    min_interest_mean: float = Field(default=5.0, gt=0)  # below this the series is noise
    min_span_days: int = Field(
        default=21, gt=0
    )  # two store observations closer than this say nothing
    volume_months: int = Field(default=3, gt=0)  # each side of the review-volume comparison
    min_volume_reviews: int = Field(default=30, gt=0)  # fewer stored reviews say nothing


def _direction(change: float, threshold: float) -> Direction:
    if change > threshold:
        return Direction.GROWING
    if change < -threshold:
        return Direction.DECLINING
    return Direction.FLAT


def read_search_interest(observation: Observation, settings: TractionSettings) -> Reading | Missing:
    """The last weeks of relative search interest against the weeks before them."""
    values = [float(value) for _, value in observation.detail.get("weeks", [])]
    span = settings.interest_weeks
    if len(values) < 2 * span:
        return Missing(
            signal=SignalKind.SEARCH_INTEREST,
            reason=f"only {len(values)} weeks of search interest; {2 * span} are needed",
        )
    recent, earlier = values[-span:], values[-2 * span : -span]
    before = sum(earlier) / span
    if before < settings.min_interest_mean:
        return Missing(
            signal=SignalKind.SEARCH_INTEREST,
            reason="search interest is too low to compare (the values are relative, near zero)",
        )
    change = round((sum(recent) / span - before) / before, 4)
    return Reading(
        signal=SignalKind.SEARCH_INTEREST,
        direction=_direction(change, settings.change_threshold),
        change=change,
        basis=(
            f"relative search interest for {observation.detail.get('term', 'the product')!r} "
            f"changed by {change:+.0%} between two {span}-week periods"
        ),
        source=observation.source,
        observed_at=observation.observed_at,
    )


def read_downloads(history: Sequence[Observation], settings: TractionSettings) -> Reading | Missing:
    """The install range now against the earliest observation far enough back.

    The range is coarse ("10M+" for years), so an unchanged range says nothing
    and is reported as missing rather than as flat.
    """
    latest = history[-1]
    earlier = [
        o
        for o in history
        if latest.observed_at - o.observed_at >= timedelta(days=settings.min_span_days)
    ]
    if not earlier:
        return Missing(
            signal=SignalKind.DOWNLOADS,
            reason=f"the install range has been observed for under {settings.min_span_days} days",
        )
    first = earlier[0]
    if latest.value == first.value:
        return Missing(
            signal=SignalKind.DOWNLOADS,
            reason=f"the install range has not moved since {first.observed_at:%Y-%m-%d}; "
            "the range is too coarse for that to mean flat",
        )
    change = round((latest.value - first.value) / first.value, 4) if first.value else None
    rose = latest.value > first.value
    return Reading(
        signal=SignalKind.DOWNLOADS,
        direction=Direction.GROWING if rose else Direction.DECLINING,
        change=change,
        basis=(
            f"the install range moved from {first.value:,.0f}+ to {latest.value:,.0f}+ "
            f"since {first.observed_at:%Y-%m-%d}"
        ),
        source=latest.source,
        observed_at=latest.observed_at,
    )


def read_rating_count(
    history: Sequence[Observation], settings: TractionSettings
) -> Reading | Missing:
    """Are ratings arriving faster or slower? The latest interval's daily rate against the
    one before it. A total only ever grows, so two observations cannot tell."""
    spaced = [history[0]]
    for observation in history[1:]:
        if observation.observed_at - spaced[-1].observed_at >= timedelta(
            days=settings.min_span_days
        ):
            spaced.append(observation)
    if len(spaced) < 3:
        return Missing(
            signal=SignalKind.RATING_COUNT,
            reason=(
                f"{len(spaced)} observation(s) at least {settings.min_span_days} days apart; "
                "3 are needed to see whether ratings arrive faster or slower"
            ),
        )
    first, middle, last = spaced[-3:]

    def rate(start: Observation, end: Observation) -> float:
        days = (end.observed_at - start.observed_at).total_seconds() / 86400
        return (end.value - start.value) / days

    before, after = rate(first, middle), rate(middle, last)
    if before <= 0:
        return Missing(
            signal=SignalKind.RATING_COUNT,
            reason="no new ratings in the earlier interval, so there is no rate to compare with",
        )
    change = round((after - before) / before, 4)
    return Reading(
        signal=SignalKind.RATING_COUNT,
        direction=_direction(change, settings.change_threshold),
        change=change,
        basis=(
            f"new ratings went from {before:,.1f} to {after:,.1f} a day "
            f"between {first.observed_at:%Y-%m-%d} and {last.observed_at:%Y-%m-%d}"
        ),
        source=last.source,
        observed_at=last.observed_at,
    )


def _months_before(month: str, count: int) -> list[str]:
    """The `count` months before `month` ("YYYY-MM"), oldest first."""
    year, number = int(month[:4]), int(month[5:])
    months = []
    for _ in range(count):
        year, number = (year, number - 1) if number > 1 else (year - 1, 12)
        months.append(f"{year:04d}-{number:02d}")
    return months[::-1]


def read_review_volume(
    dates_by_source: Mapping[str, Sequence[datetime]], now: datetime, settings: TractionSettings
) -> Reading | Missing:
    """Stored reviews written in the last full months against the months before.

    The comparison is made only when every source has reviews in each month of
    the earlier half: a capped fetch keeps the newest reviews, so months it does
    not reach are empty, and counting them would look like growth. The current
    month is left out too.
    """
    span = settings.volume_months
    months = _months_before(f"{now:%Y-%m}", 2 * span)
    sources = {source: dates for source, dates in dates_by_source.items() if dates}
    if not sources:
        return Missing(signal=SignalKind.REVIEW_VOLUME, reason="no stored reviews")
    # A source covers the window when it has a review in every month of the earlier half.
    # The oldest stored date is not the test: one stray old review among a capped fetch's
    # newest reviews would pass it (seen on the live Splitwise data).
    uncovered = sorted(
        source
        for source, dates in sources.items()
        if not set(months[:span]).issubset({f"{moment:%Y-%m}" for moment in dates})
    )
    if uncovered:
        return Missing(
            signal=SignalKind.REVIEW_VOLUME,
            reason=(
                f"stored reviews from {', '.join(uncovered)} do not cover every month from "
                f"{months[0]} to {months[span - 1]}: the fetch cap, or too few reviews, would "
                "cut the count"
            ),
        )
    counts = dict.fromkeys(months, 0)
    for dates in sources.values():
        for moment in dates:
            if f"{moment:%Y-%m}" in counts:
                counts[f"{moment:%Y-%m}"] += 1
    earlier = sum(counts[month] for month in months[:span])
    recent = sum(counts[month] for month in months[span:])
    if earlier + recent < settings.min_volume_reviews or earlier == 0:
        return Missing(
            signal=SignalKind.REVIEW_VOLUME,
            reason=f"only {earlier + recent} stored reviews in the last {2 * span} full months",
        )
    change = round((recent - earlier) / earlier, 4)
    return Reading(
        signal=SignalKind.REVIEW_VOLUME,
        direction=_direction(change, settings.change_threshold),
        change=change,
        basis=(
            f"{recent} stored reviews in {months[span]} to {months[-1]} against {earlier} in "
            f"{months[0]} to {months[span - 1]}"
        ),
        source="stored reviews (" + ", ".join(sorted(sources)) + ")",
        observed_at=now,
    )


def score(product_id: str, results: Sequence[Reading | Missing]) -> TractionScore:
    """Combine what the signals say.

    - No reading: no label.
    - One reading: its direction, low confidence.
    - Readings that agree: that direction, medium confidence.
    - Growing and declining readings together: flat, low confidence, and the
      note says the signals disagree.
    - Otherwise (a mix with flat): the majority direction, or flat on a tie,
      with low confidence.
    Signals that could not be read are listed with the reason.
    """
    readings = [result for result in results if isinstance(result, Reading)]
    missing = [result for result in results if isinstance(result, Missing)]
    seen = {result.signal for result in results}
    missing += [
        Missing(signal=kind, reason="not collected for this product")
        for kind in SCORED
        if kind not in seen
    ]
    sources = sorted({reading.source for reading in readings})
    directions = [reading.direction for reading in readings]
    used = ", ".join(str(reading.signal) for reading in readings)
    if not readings:
        label, confidence, note = None, None, "Not enough data: no signal could be read."
    elif len(readings) == 1:
        label, confidence = directions[0], "low"
        note = f"Based on one signal only ({used}), so confidence is low."
    elif len(set(directions)) == 1:
        label, confidence = directions[0], "medium"
        note = f"{len(readings)} signals agree ({used})."
    elif Direction.GROWING in directions and Direction.DECLINING in directions:
        label, confidence = Direction.FLAT, "low"
        note = f"The signals disagree ({used}): some point up and some down."
    else:
        counts = {direction: directions.count(direction) for direction in set(directions)}
        top = max(counts.values())
        leaders = [direction for direction, count in counts.items() if count == top]
        label = leaders[0] if len(leaders) == 1 else Direction.FLAT
        confidence = "low"
        note = f"The signals only partly agree ({used}), so confidence is low."
    if missing and readings:
        note += " Missing: " + ", ".join(str(item.signal) for item in missing) + "."
    return TractionScore(
        product_id=product_id,
        label=label,
        confidence=confidence,
        readings=readings,
        missing=missing,
        sources=sources,
        note=note,
    )


class TractionTarget(Schema):
    """What to collect for one product."""

    product_id: ProductId
    name: NonEmptyStr
    term: str | None = None  # the search term for Google Trends; None: skip it
    region: str | None = None  # a country code for the search interest; None: worldwide
    store_ids: dict[str, str] = {}  # by "google_play" and "app_store"


def targets_from(
    observations: Sequence[Observation], names: Mapping[str, str]
) -> list[TractionTarget]:
    """What was collected before, so that the weekly pass collects it again: the search
    term of each product's latest search-interest row and the store ids of its store rows."""
    latest: dict[str, dict[str, Any]] = {}
    for observation in sorted(observations, key=lambda o: o.observed_at):
        found = latest.setdefault(observation.product_id, {"store_ids": {}})
        detail = observation.detail
        if observation.signal is SignalKind.SEARCH_INTEREST and detail.get("term"):
            found["term"], found["region"] = detail["term"], detail.get("region")
        elif detail.get("store_key") and detail.get("store_id"):
            found["store_ids"][detail["store_key"]] = detail["store_id"]
    return [
        TractionTarget(product_id=product, name=names.get(product, product), **found)
        for product, found in sorted(latest.items())
    ]


class TractionStore(Protocol):
    def add(self, observations: Sequence[Observation], product_name: str) -> None: ...

    def targets(self) -> list[TractionTarget]: ...

    def history(self, product_id: str, signal: SignalKind) -> list[Observation]:
        """The product's observations of one signal, oldest first."""
        ...
