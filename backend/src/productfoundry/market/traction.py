"""Traction: collect a product's public signals, then score them."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from productfoundry.core.clock import utcnow
from productfoundry.core.reviews import ReviewStore
from productfoundry.core.traction import (
    Missing,
    Observation,
    Reading,
    SignalKind,
    TractionScore,
    TractionSettings,
    TractionStore,
    TractionTarget,
    read_downloads,
    read_rating_count,
    read_review_volume,
    read_search_interest,
    score,
)
from productfoundry.sources import SourceError
from productfoundry.sources.trends import SearchInterest, StoreStats


@dataclass(frozen=True)
class Collected:
    observations: list[Observation]
    skipped: list[str]  # one line for each source that could not be read
    requests: int


def collect(
    target: TractionTarget,
    *,
    interest: SearchInterest | None,
    stats: Mapping[str, StoreStats],
    store: TractionStore,
    now: Callable[[], datetime] = utcnow,
) -> Collected:
    """Read the signals that have a source and store them, each with its source and date.

    A source that fails is reported and the others are still read.
    """
    observations: list[Observation] = []
    skipped: list[str] = []
    requests = 0

    def observed(signal: SignalKind, value: float, source: str, **detail) -> None:
        observations.append(
            Observation(
                product_id=target.product_id,
                signal=signal,
                value=value,
                source=source,
                observed_at=now(),
                detail=detail,
            )
        )

    if interest is not None and target.term:
        requests += 1
        try:
            weeks = interest.weekly(target.term, target.region)
        except SourceError as exc:
            skipped.append(str(exc))
        else:
            if weeks:
                observed(
                    SignalKind.SEARCH_INTEREST,
                    float(weeks[-1][1]),
                    "Google Trends",
                    term=target.term,
                    region=target.region,
                    weeks=[list(week) for week in weeks],
                )
            else:
                skipped.append(f"Google Trends has no data for {target.term!r}")
    for name, store_id in target.store_ids.items():
        reader = stats.get(name)
        if reader is None or not store_id:
            continue
        requests += 1
        try:
            figures = reader.figures(store_id)
        except SourceError as exc:
            skipped.append(str(exc))
            continue
        if figures is None:
            skipped.append(f"{name} does not list {store_id}")
            continue
        where = {"store_key": name, "store_id": store_id}
        if figures.min_installs is not None:
            observed(SignalKind.DOWNLOADS, float(figures.min_installs), figures.store, **where)
        if figures.rating_count is not None:
            observed(SignalKind.RATING_COUNT, float(figures.rating_count), figures.store, **where)
    if observations:
        store.add(observations, target.name)
    return Collected(observations, skipped, requests)


def score_product(
    product_id: str,
    *,
    store: TractionStore,
    reviews: ReviewStore | None,
    settings: TractionSettings | None = None,
    now: Callable[[], datetime] = utcnow,
) -> TractionScore:
    """The traction label from what is stored. No outside request is made."""
    settings = settings or TractionSettings()
    results: list[Reading | Missing] = []

    interest = store.history(product_id, SignalKind.SEARCH_INTEREST)
    if interest:
        results.append(read_search_interest(interest[-1], settings))

    downloads = store.history(product_id, SignalKind.DOWNLOADS)
    if downloads:
        results.append(read_downloads(downloads, settings))

    # Each store counts its own ratings, so each is read on its own history; the store
    # with the longest history speaks for the signal.
    ratings = store.history(product_id, SignalKind.RATING_COUNT)
    by_store: dict[str, list[Observation]] = {}
    for observation in ratings:
        by_store.setdefault(observation.source, []).append(observation)
    if by_store:
        read = [read_rating_count(history, settings) for history in by_store.values()]
        readings = [result for result in read if isinstance(result, Reading)]
        results.append(readings[0] if readings else read[0])

    if reviews is not None:
        dates: dict[str, list[datetime]] = {}
        for review in reviews.for_product(product_id):
            dates.setdefault(str(review.source), []).append(review.reviewed_at)
        if dates:
            results.append(read_review_volume(dates, now(), settings))
    return score(product_id, results)


def render_score(result: TractionScore, product_name: str) -> str:
    label = str(result.label) if result.label else "not enough data"
    confidence = f" ({result.confidence} confidence)" if result.confidence else ""
    lines = [f"{product_name}: {label}{confidence}", f"  {result.note}"]
    for reading in result.readings:
        lines.append(f"  {reading.signal}: {reading.direction}: {reading.basis}")
        lines.append(f"    source: {reading.source}, {reading.observed_at:%Y-%m-%d}")
    lines.extend(f"  {item.signal}: missing: {item.reason}" for item in result.missing)
    return "\n".join(lines)


def collect_tracked(
    *,
    interest: SearchInterest | None,
    stats: Mapping[str, StoreStats],
    store: TractionStore,
) -> dict[str, Collected]:
    """The weekly pass: collect again for every product that has signals, by product id."""
    return {
        target.product_id: collect(target, interest=interest, stats=stats, store=store)
        for target in store.targets()
    }
