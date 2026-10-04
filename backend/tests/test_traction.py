import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from productfoundry.cli import main
from productfoundry.core.traction import (
    Direction,
    Missing,
    Observation,
    Reading,
    SignalKind,
    TractionSettings,
    TractionTarget,
    read_downloads,
    read_rating_count,
    read_review_volume,
    read_search_interest,
    score,
)
from productfoundry.market.traction import collect, collect_tracked, render_score, score_product
from productfoundry.sources import SourceError
from productfoundry.sources.robots import Robots
from productfoundry.sources.trends import (
    AppStoreStats,
    FakeSearchInterest,
    GooglePlayStats,
    StoreFigures,
)
from productfoundry.storage.memory import InMemoryReviewStore, InMemoryTractionStore
from test_s3_pain_points import COMPETITORS, stored_reviews

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
SETTINGS = TractionSettings()
PRODUCT = "prod_a"
ALLOW_ALL = Robots(lambda url: "")


def weeks(*values: float) -> list[list]:
    start = NOW - timedelta(weeks=len(values))
    return [[f"{start + timedelta(weeks=n):%Y-%m-%d}", value] for n, value in enumerate(values)]


def interest(*values: float) -> Observation:
    return Observation(
        product_id=PRODUCT,
        signal="search_interest",
        value=values[-1] if values else 0,
        source="Google Trends",
        observed_at=NOW,
        detail={"term": "splitly", "weeks": weeks(*values)},
    )


def store_row(signal: str, value: float, days_ago: int, source="Google Play") -> Observation:
    return Observation(
        product_id=PRODUCT,
        signal=signal,
        value=value,
        source=source,
        observed_at=NOW - timedelta(days=days_ago),
    )


def reading(signal: str, direction: str) -> Reading:
    return Reading(
        signal=signal, direction=direction, basis="b", source=f"source of {signal}", observed_at=NOW
    )


# Each signal


def test_search_interest_compares_the_last_weeks_with_the_weeks_before():
    growing = read_search_interest(interest(*[40] * 13, *[60] * 13), SETTINGS)
    assert (growing.direction, growing.change) == (Direction.GROWING, 0.5)
    assert "'splitly' changed by +50% between two 13-week periods" in growing.basis

    assert read_search_interest(interest(*[50] * 13, *[52] * 13), SETTINGS).direction == "flat"
    assert read_search_interest(interest(*[50] * 13, *[30] * 13), SETTINGS).direction == "declining"
    # Only the last 26 weeks are read, whatever came before.
    assert read_search_interest(interest(*[1] * 26, *[50] * 26), SETTINGS).direction == "flat"


def test_search_interest_that_is_too_short_or_too_low_is_missing_not_flat():
    short = read_search_interest(interest(*[50] * 20), SETTINGS)
    assert isinstance(short, Missing) and "only 20 weeks" in short.reason
    low = read_search_interest(interest(*[1] * 13, *[3] * 13), SETTINGS)
    assert isinstance(low, Missing) and "too low" in low.reason


def test_the_install_range_counts_only_when_it_moves():
    rose = read_downloads(
        [store_row("downloads", 1_000_000, 60), store_row("downloads", 5_000_000, 0)], SETTINGS
    )
    assert (rose.direction, rose.change) == (Direction.GROWING, 4.0)
    assert "moved from 1,000,000+ to 5,000,000+" in rose.basis

    same = read_downloads(
        [store_row("downloads", 10_000_000, 60), store_row("downloads", 10_000_000, 0)], SETTINGS
    )
    assert isinstance(same, Missing) and "too coarse" in same.reason
    young = read_downloads(
        [store_row("downloads", 1_000_000, 7), store_row("downloads", 5_000_000, 0)], SETTINGS
    )
    assert isinstance(young, Missing) and "under 21 days" in young.reason


def test_rating_counts_need_three_observations_to_show_a_change_of_pace():
    def ratings(*rows: tuple[float, int]):
        history = [store_row("rating_count", value, days_ago) for value, days_ago in rows]
        return read_rating_count(history, SETTINGS)

    faster = ratings((10_000, 60), (10_300, 30), (10_900, 0))  # 10 a day, then 20 a day
    assert (faster.direction, faster.change) == (Direction.GROWING, 1.0)
    assert "new ratings went from 10.0 to 20.0 a day" in faster.basis
    assert ratings((10_000, 60), (10_600, 30), (10_900, 0)).direction == "declining"
    assert ratings((10_000, 60), (10_300, 30), (10_610, 0)).direction == "flat"

    two = ratings((10_000, 30), (10_300, 0))
    assert isinstance(two, Missing) and "3 are needed" in two.reason
    # Three rows within a week are one observation as far as pace goes.
    assert isinstance(ratings((10_000, 6), (10_010, 3), (10_020, 0)), Missing)
    assert isinstance(ratings((10_000, 60), (10_000, 30), (10_300, 0)), Missing)


def dates(*per_month: tuple[str, int]) -> list[datetime]:
    return [
        datetime(int(month[:4]), int(month[5:]), 1 + n % 27, tzinfo=UTC)
        for month, count in per_month
        for n in range(count)
    ]


def test_review_volume_compares_full_months_and_leaves_the_current_one_out():
    play = dates(
        ("2026-03", 5), ("2026-04", 10), ("2026-05", 10), ("2026-06", 10),
        ("2026-07", 20), ("2026-08", 20), ("2026-09", 20), ("2026-10", 99),
    )  # fmt: skip
    found = read_review_volume({"google_play": play}, NOW, SETTINGS)

    assert (found.direction, found.change) == (Direction.GROWING, 1.0)
    assert found.basis == "60 stored reviews in 2026-07 to 2026-09 against 30 in 2026-04 to 2026-06"
    assert found.source == "stored reviews (google_play)"


def test_review_volume_is_missing_when_a_capped_fetch_cuts_the_window():
    # A capped fetch kept only the newest reviews: they start in July, plus one stray old
    # review, as the live Splitwise fetch had.
    play = dates(("2016-05", 1), ("2026-07", 20), ("2026-08", 20), ("2026-09", 40))
    steady = dates(*[(f"2026-{month:02d}", 4) for month in range(3, 10)])
    cut = read_review_volume({"google_play": play, "app_store": steady}, NOW, SETTINGS)
    assert isinstance(cut, Missing)
    assert "google_play do not cover every month from 2026-04 to 2026-06" in cut.reason

    few = read_review_volume({"app_store": steady}, NOW, SETTINGS)
    assert isinstance(few, Missing) and "only 24 stored reviews" in few.reason
    assert isinstance(read_review_volume({}, NOW, SETTINGS), Missing)


# The score


def test_agreeing_signals_give_the_label_with_medium_confidence():
    result = score(
        PRODUCT, [reading("search_interest", "growing"), reading("downloads", "growing")]
    )

    assert (result.label, result.confidence) == (Direction.GROWING, "medium")
    assert result.note.startswith("2 signals agree (search_interest, downloads).")
    assert result.sources == ["source of downloads", "source of search_interest"]
    assert [item.signal for item in result.missing] == ["rating_count", "review_volume"]
    assert "Missing: rating_count, review_volume." in result.note


def test_conflicting_signals_give_flat_with_low_confidence_and_say_so():
    result = score(
        PRODUCT,
        [
            reading("search_interest", "growing"),
            reading("review_volume", "declining"),
            reading("downloads", "growing"),
        ],
    )
    assert (result.label, result.confidence) == (Direction.FLAT, "low")
    assert "The signals disagree" in result.note and len(result.sources) == 3


def test_one_signal_only_lowers_the_confidence():
    waiting = Missing(signal="rating_count", reason="1 observation(s)")
    result = score(PRODUCT, [reading("search_interest", "declining"), waiting])

    assert (result.label, result.confidence) == (Direction.DECLINING, "low")
    assert "Based on one signal only (search_interest)" in result.note
    assert result.missing[0] == waiting and len(result.missing) == 3


def test_a_partial_agreement_takes_the_majority_and_a_tie_is_flat():
    majority = score(
        PRODUCT,
        [
            reading("search_interest", "growing"),
            reading("downloads", "growing"),
            reading("review_volume", "flat"),
        ],
    )
    assert (majority.label, majority.confidence) == (Direction.GROWING, "low")
    tie = score(PRODUCT, [reading("search_interest", "growing"), reading("review_volume", "flat")])
    assert (tie.label, tie.confidence) == (Direction.FLAT, "low")


def test_no_reading_means_no_label_and_never_high_confidence():
    nothing = score(PRODUCT, [])
    assert (nothing.label, nothing.confidence, nothing.sources) == (None, None, [])
    assert nothing.note == "Not enough data: no signal could be read." and len(nothing.missing) == 4

    best = score(PRODUCT, [reading(kind, "growing") for kind in SignalKind])
    assert best.confidence == "medium" and best.missing == []


# Sources


def test_store_figures_are_read_from_the_details_page_and_the_lookup_api():
    asked = []

    def fetch_app(app_id, country):
        asked.append((app_id, country))
        return {"minInstalls": 10_000_000, "ratings": 250_000, "installs": "10,000,000+"}

    assert GooglePlayStats(ALLOW_ALL, fetch_app).figures("app.splitly") == StoreFigures(
        "Google Play", min_installs=10_000_000, rating_count=250_000
    )
    assert GooglePlayStats(ALLOW_ALL, lambda *_: None).figures("app.gone") is None
    deny = Robots(lambda url: "User-agent: *\nDisallow: /store/apps\n")
    with pytest.raises(SourceError, match="robots.txt disallows"):
        GooglePlayStats(deny, fetch_app).figures("app.splitly")
    assert asked == [("app.splitly", "us")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"userRatingCount": 4200}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert AppStoreStats("in", client).figures("123") == StoreFigures("App Store", None, 4200)


class Stats:
    def __init__(self, figures) -> None:
        self._figures = figures

    def figures(self, store_id: str):
        if isinstance(self._figures, Exception):
            raise self._figures
        return self._figures


TARGET = TractionTarget(
    product_id=PRODUCT,
    name="Splitly",
    term="splitly",
    region="IN",
    store_ids={"google_play": "app.splitly", "app_store": "123"},
)


def test_each_signal_is_stored_with_its_source_and_date():
    store = InMemoryTractionStore()
    trends = FakeSearchInterest(
        {"splitly": [tuple(week) for week in weeks(*[40] * 13, *[60] * 13)]}
    )
    stats = {
        "google_play": Stats(StoreFigures("Google Play", 10_000_000, 250_000)),
        "app_store": Stats(StoreFigures("App Store", None, 4_200)),
    }
    collected = collect(TARGET, interest=trends, stats=stats, store=store, now=lambda: NOW)

    assert (
        collected.requests == 3 and collected.skipped == [] and trends.asked == [("splitly", "IN")]
    )
    assert [(o.signal, o.value, o.source, o.observed_at) for o in collected.observations] == [
        ("search_interest", 60.0, "Google Trends", NOW),
        ("downloads", 10_000_000.0, "Google Play", NOW),
        ("rating_count", 250_000.0, "Google Play", NOW),
        ("rating_count", 4_200.0, "App Store", NOW),
    ]
    assert store.history(PRODUCT, SignalKind.DOWNLOADS) == [collected.observations[1]]
    # What was collected is what the weekly pass collects again.
    assert store.targets() == [TARGET]


def test_a_source_that_fails_is_reported_and_the_rest_is_still_stored():
    store = InMemoryTractionStore()
    stats = {
        "google_play": Stats(SourceError("robots.txt disallows fetching the page")),
        "app_store": Stats(None),
    }
    collected = collect(
        TARGET, interest=FakeSearchInterest({}), stats=stats, store=store, now=lambda: NOW
    )

    assert collected.observations == [] and store.targets() == []
    assert collected.skipped == [
        "Google Trends failed: ResponseError",
        "robots.txt disallows fetching the page",
        "app_store does not list 123",
    ]
    quiet = TARGET.model_copy(update={"term": None, "store_ids": {}})
    assert collect(quiet, interest=FakeSearchInterest({}), stats=stats, store=store).requests == 0


def test_the_weekly_pass_collects_again_for_every_product_collected_before():
    store = InMemoryTractionStore()
    trends = FakeSearchInterest({"splitly": [tuple(week) for week in weeks(*[50] * 26)]})
    stats = {"google_play": Stats(StoreFigures("Google Play", 1_000_000, 900))}
    target = TARGET.model_copy(update={"store_ids": {"google_play": "app.splitly"}})
    collect(target, interest=trends, stats=stats, store=store, now=lambda: NOW)

    again = collect_tracked(interest=trends, stats=stats, store=store)
    assert list(again) == [PRODUCT] and again[PRODUCT].requests == 2
    assert len(store.history(PRODUCT, SignalKind.RATING_COUNT)) == 2


# Scoring a product from what is stored


def test_a_product_is_scored_from_its_stored_signals_and_reviews():
    store = InMemoryTractionStore()
    store.add(
        [
            interest(*[40] * 13, *[60] * 13),
            store_row("downloads", 1_000_000, 60),
            store_row("downloads", 5_000_000, 0),
            store_row("rating_count", 900, 0),
        ],
        "Splitly",
    )
    reviews = InMemoryReviewStore()
    for item in COMPETITORS.competitors:
        reviews.ensure_product(item)
    reviews.upsert(stored_reviews())  # June to September 2026: under six full months
    result = score_product(PRODUCT, store=store, reviews=reviews, now=lambda: NOW)

    assert (result.label, result.confidence) == (Direction.GROWING, "medium")
    assert [r.signal for r in result.readings] == ["search_interest", "downloads"]
    assert {item.signal for item in result.missing} == {"rating_count", "review_volume"}
    assert result.sources == ["Google Play", "Google Trends"]
    text = render_score(result, "Splitly")
    assert text.startswith("Splitly: growing (medium confidence)\n  2 signals agree")
    assert "  downloads: growing: the install range moved from" in text
    assert "    source: Google Trends, 2026-10-04" in text
    assert "  rating_count: missing: 1 observation(s) at least 21 days apart" in text

    unknown = score_product("prod_none", store=store, reviews=reviews, now=lambda: NOW)
    assert unknown.label is None and "not enough data" in render_score(unknown, "Nobody")


def test_signals_are_stored_and_the_cli_prints_the_score(sessions, db_engine, monkeypatch, capsys):
    from productfoundry.core.ids import product_id
    from productfoundry.storage import TractionRepository

    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    product = product_id("splitly")
    store = TractionRepository(sessions)
    rows = [interest(*[60] * 13, *[40] * 13), store_row("rating_count", 900, 0)]
    store.add([row.model_copy(update={"product_id": product}) for row in rows], "Splitly")

    assert len(store.history(product, SignalKind.SEARCH_INTEREST)) == 1
    assert store.history(product, SignalKind.DOWNLOADS) == []
    (target,) = store.targets()
    assert (target.name, target.term, target.store_ids) == ("Splitly", "splitly", {})

    assert main(["traction", "score", "--product", "Splitly"]) == 0
    assert capsys.readouterr().out.startswith("Splitly: declining (low confidence)")
    assert main(["traction", "score", "--product", "Splitly", "--format", "json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["label"] == "declining" and shown["sources"] == ["Google Trends"]
    assert main(["--memory", "traction", "score", "--product", "Splitly"]) == 1
