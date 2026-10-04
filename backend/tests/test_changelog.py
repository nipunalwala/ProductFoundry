import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from productfoundry.api.app import Backend, create_app
from productfoundry.cli import main
from productfoundry.core.changelog import (
    ChangelogAlert,
    ChangelogMatch,
    ChangelogSource,
    build_alerts,
    follow_up,
    match_problems,
    release_id,
)
from productfoundry.core.errors import StageOutputInvalid
from productfoundry.core.pain_points import Trend, TrendPoint, TrendSettings
from productfoundry.core.run_input import RunInput
from productfoundry.jobs import RecordingQueue
from productfoundry.llm import LlmFailed
from productfoundry.llm.fakes import FakeProvider
from productfoundry.market.changelog import (
    collect,
    match_run,
    render_alerts,
    run_alerts,
    to_item,
)
from productfoundry.orchestrator import InMemoryRunStore, Orchestrator
from productfoundry.sources import SourceError
from productfoundry.sources.changelog import (
    AppStoreNotes,
    FakeReleases,
    FeedReleases,
    GitHubReleases,
    GooglePlayNotes,
    PageReleases,
    RawRelease,
    parse_feed,
    split_changelog,
)
from productfoundry.sources.pricing import FakeFetcher
from productfoundry.sources.robots import Robots
from productfoundry.stages.fakes import FAKE_STAGES
from productfoundry.storage.memory import InMemoryChangelogStore
from test_s3_pain_points import COMPETITORS, gateway, run_stage
from test_s4_prd import write_prd

FIXTURE = json.loads((Path(__file__).parent / "fixtures/sources/changelog.json").read_text("utf-8"))
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
ALLOW_ALL = Robots(lambda url: "")
DENY_ALL = Robots(lambda url: "User-agent: *\nDisallow: /\n")
TABBY = "prod_b"  # the second competitor of the stage 3 fixture


def client_for(payload, status=200) -> tuple[httpx.Client, list[httpx.Request]]:
    """An HTTP client that answers every request with `payload` and records the requests."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


# Sources


def test_github_releases_skip_drafts_and_keep_no_author():
    client, seen = client_for(FIXTURE["github"])
    releases = GitHubReleases(client).fetch("tabby-app/tabby")

    assert [r.version for r in releases] == ["v4.2.0", "v4.1.3"]
    assert releases[0].title == "4.2.0: no more daily cap" and releases[1].title == "v4.1.3"
    assert releases[0].released_at == datetime(2026, 8, 10, 9, 30, tzinfo=UTC)
    assert releases[0].source_item_id == "tabby-app/tabby#9001"
    assert str(seen[0].url).startswith("https://api.github.com/repos/tabby-app/tabby/releases")
    assert not hasattr(releases[0], "author")

    with pytest.raises(SourceError, match="owner/repo"):
        GitHubReleases(client).fetch("https://github.com/tabby-app/tabby")
    missing, _ = client_for({"message": "Not Found"}, status=404)
    with pytest.raises(SourceError, match="HTTP 404"):
        GitHubReleases(missing).fetch("tabby-app/gone")


def test_an_atom_feed_is_read_as_text_with_dates_and_links():
    first, second = parse_feed(FIXTURE["atom"])

    assert first.title == "Group budgets"
    assert first.body == "Set a monthly budget for a group & get a warning at 80%."
    assert first.url == "https://tabby.example/releases/group-budgets"
    assert first.released_at == datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
    assert first.source_item_id == "tag:tabby.example,2026:release/42"
    assert second.body == "Dependency updates." and second.released_at.utcoffset().seconds == 19800


def test_an_rss_feed_prefers_the_full_content_and_drops_an_untitled_item():
    (release,) = parse_feed(FIXTURE["rss"])

    assert release.title == "Totals are now exact"
    assert release.body == "We fixed rounding so that group totals always add up."
    assert release.released_at == datetime(2026, 9, 14, 6, 0, tzinfo=UTC)


def test_a_feed_is_not_fetched_when_robots_txt_disallows_it_and_bad_xml_is_reported():
    client, seen = client_for(FIXTURE["atom"])
    with pytest.raises(SourceError, match="robots.txt disallows"):
        FeedReleases(DENY_ALL, client).fetch("https://tabby.example/feed.xml")
    assert seen == []

    assert len(FeedReleases(ALLOW_ALL, client).fetch("https://tabby.example/feed.xml")) == 2
    with pytest.raises(SourceError, match="not valid XML"):
        parse_feed("<rss><channel>")
    with pytest.raises(SourceError, match="not valid XML"):
        parse_feed('<!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel>&a;</channel></rss>')


def test_a_changelog_page_is_split_at_its_dated_headings():
    url = "https://tabby.example/changelog"
    releases = split_changelog(FIXTURE["page"], url)

    assert [(r.released_at.date().isoformat(), r.title) for r in releases] == [
        ("2026-09-28", "v5.0.1"),
        ("2026-09-12", "Receipt scanning for everyone"),
        ("2026-08-30", "Bug fixes."),
    ]
    assert releases[0].version == "5.0.1" and releases[0].body.startswith("v5.0.1\nUPI settle-up")
    assert releases[1].body == "Scanning a receipt no longer needs a paid plan."
    # The long paragraph that mentions a date belongs to the item above it.
    assert "moved offices" in releases[2].body
    assert len({r.source_item_id for r in releases}) == 3
    assert split_changelog("Our story\nWe build Tabby.", url) == []


def test_a_changelog_page_is_not_fetched_when_robots_txt_disallows_it():
    url = "https://tabby.example/changelog"
    fetcher = FakeFetcher({url: FIXTURE["page"]})
    with pytest.raises(SourceError, match="robots.txt disallows"):
        PageReleases(fetcher, DENY_ALL).fetch(url)
    assert fetcher.fetched == []
    assert len(PageReleases(fetcher, ALLOW_ALL).fetch(url)) == 3


def test_the_stores_give_the_note_of_the_current_version():
    client, seen = client_for(FIXTURE["itunes_lookup"])
    (ios,) = AppStoreNotes("in", client).fetch("123456789")
    assert (ios.title, ios.version, ios.body) == (
        "Version 5.0.1", "5.0.1", "Settle up over UPI.\nSmaller fixes.",
    )  # fmt: skip
    assert ios.url == "https://apps.apple.com/us/app/tabby/id123456789"
    assert ios.source_item_id == "123456789#5.0.1" and "country=in" in str(seen[0].url)

    asked = []

    def fetch_app(app_id, country):
        asked.append((app_id, country))
        return FIXTURE["google_play_app"]

    (android,) = GooglePlayNotes(ALLOW_ALL, fetch_app).fetch("app.tabby")
    assert android.body == "Settle up over UPI.\nSmaller fixes." and asked == [("app.tabby", "us")]
    assert android.released_at == datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
    assert android.source_item_id == "app.tabby#2026-09-28"
    assert GooglePlayNotes(ALLOW_ALL, lambda *_: None).fetch("app.gone") == []
    with pytest.raises(SourceError, match="robots.txt disallows"):
        GooglePlayNotes(DENY_ALL, fetch_app).fetch("app.tabby")


# Collecting


def release(n: int, title: str, body: str, day: int = 10, month: int = 8) -> RawRelease:
    when = datetime(2026, month, day, tzinfo=UTC)
    return RawRelease(f"tabby#{n}", title, body, released_at=when, url=f"https://t.example/{n}")


RELEASES = [
    release(1, "No more daily cap", "The daily limit on payments is removed."),
    release(2, "Group budgets", "Set a monthly budget for a group.", day=2, month=9),
    release(3, "Maintenance", "Bug fixes and performance improvements.", day=1, month=7),
]
GITHUB = ChangelogSource(product_id=TABBY, kind="github", target="tabby-app/tabby")


def tracked(releases=RELEASES) -> tuple[InMemoryChangelogStore, FakeReleases]:
    store = InMemoryChangelogStore()
    store.track(GITHUB, "Tabby")
    return store, FakeReleases({GITHUB.target: releases})


def test_new_release_items_are_stored_once():
    store, source = tracked()
    (first,) = collect({"github": source}, store, now=lambda: NOW)
    (again,) = collect({"github": source}, store, now=lambda: NOW)

    assert (first.found, first.new, first.skipped) == (3, 3, None) and again.new == 0
    items = store.items([TABBY])
    assert [item.title for item in items] == ["Group budgets", "No more daily cap", "Maintenance"]
    assert items[0].id == release_id("github", "tabby#2") and items[0].fetched_at == NOW
    assert store.items(["prod_a"]) == []


def test_a_source_that_fails_is_reported_and_the_others_are_still_read():
    store, source = tracked()
    feed = ChangelogSource(product_id=TABBY, kind="feed", target="https://t.example/feed")
    page = ChangelogSource(product_id=TABBY, kind="page", target="https://t.example/changelog")
    store.track(feed, "Tabby")
    store.track(page, "Tabby")
    outcomes = collect({"github": source, "feed": FakeReleases({})}, store, now=lambda: NOW)

    by_kind = {str(outcome.source.kind): outcome for outcome in outcomes}
    assert by_kind["feed"].skipped == "cannot read https://t.example/feed"
    assert by_kind["page"].skipped == "no adapter for page" and by_kind["github"].new == 3


def test_a_long_note_is_cut_and_a_bad_link_is_dropped_before_storage():
    raw = RawRelease("x#1", "  Big release  ", "a" * 5000, url="javascript:alert(1)")
    item = to_item(GITHUB, raw, NOW)
    assert (item.title, len(item.body), item.url) == ("Big release", 2000, None)


# Matching


@pytest.fixture(scope="module")
def plan():
    """A run's competitors, approved pain points and PRD, on the invented reviews."""
    from conftest import run_input_data

    report, _, _ = run_stage()
    prd, _ = write_prd(report, RunInput.model_validate(run_input_data()))
    return COMPETITORS, report, prd


class Matcher:
    """Answers as a careful reader would: the cap removal fixes the first pain point, the
    budgets are a new feature, maintenance is nothing."""

    def __init__(self, change=None) -> None:
        self.payloads: list[dict] = []
        self._change = change

    def complete(self, request):
        from productfoundry.llm import ProviderResponse

        payload = json.loads(request.messages[1]["content"])
        self.payloads.append(payload)
        payments = next(p["id"] for p in payload["pain_points"] if "Payments" in p["label"])
        matches = []
        for item in payload["releases"]:
            if "daily cap" in item["title"]:
                match = {
                    "kind": "fix",
                    "cluster_ids": [payments],
                    "requirement_ids": ["req_001"],
                    "reason": "The notes say the payment limit is removed.",
                }
            elif "budgets" in item["title"]:
                match = {"kind": "feature", "reason": "Budgets are new and nothing plans them."}
            else:
                match = {"kind": "other", "reason": "The notes name no change."}
            matches.append({"n": item["n"], **match})
        answer = {"matches": matches}
        if self._change is not None:
            self._change(answer)
        return ProviderResponse(json.dumps(answer))


def matched(plan, provider=None, **providers):
    store, source = tracked()
    collect({"github": source}, store, now=lambda: NOW)
    provider = provider or Matcher()
    llm = gateway(groq=provider, **providers)
    matches = match_run("run_a", *plan, llm=llm, store=store)
    return matches, store, provider


def test_release_items_are_matched_to_the_run_s_pain_points_and_requirements(plan):
    matches, store, provider = matched(plan)
    _, report, _ = plan
    payments = next(p for p in report.pain_points if "Payments" in p.label)

    assert [(m.kind, m.cluster_ids, m.requirement_ids) for m in matches] == [
        ("feature", [], []),
        ("fix", [payments.cluster_id], ["req_001"]),
        ("other", [], []),
    ]
    assert store.matches("run_a") == sorted(matches, key=lambda match: match.item_id)
    (payload,) = provider.payloads
    assert [r["title"] for r in payload["releases"]] == [
        "Group budgets", "No more daily cap", "Maintenance",
    ]  # fmt: skip
    assert payload["releases"][0] == {
        "n": 1,
        "product": "Tabby",
        "title": "Group budgets",
        "version": None,
        "date": "2026-09-02",
        "notes": "Set a monthly budget for a group.",
    }
    assert len(payload["pain_points"]) == 3 and len(payload["requirements"]) == 4


def test_items_matched_once_are_not_sent_again(plan):
    _, store, provider = matched(plan)
    llm = gateway(groq=provider)

    assert match_run("run_a", *plan, llm=llm, store=store) == []
    assert len(provider.payloads) == 1

    store.add_items([to_item(GITHUB, release(4, "Dark mode", "A dark theme."), NOW)])
    (new,) = match_run("run_a", *plan, llm=llm, store=store)
    assert new.kind == "other" and len(provider.payloads[1]["releases"]) == 1
    assert len(store.matches("run_a")) == 4 and store.matches("run_other") == []


def test_a_match_citing_a_cluster_that_is_not_in_the_run_is_rejected(plan):
    def invent(answer):
        answer["matches"][1]["cluster_ids"] = ["cl_0000000000000000"]

    bad, good = Matcher(invent), Matcher()
    matches, _, _ = matched(plan, bad, gemini=good)
    assert len(bad.payloads) == 2 and len(good.payloads) == 1  # one retry, then the fallback
    assert all(not m.cluster_ids or m.cluster_ids[0].startswith("cl_") for m in matches)

    with pytest.raises(LlmFailed, match="cites ids that are not in the run"):
        matched(plan, Matcher(invent))


def test_an_answer_that_skips_a_release_or_invents_a_requirement_is_rejected(plan):
    def skip(answer):
        del answer["matches"][0]

    def invent(answer):
        answer["matches"][1]["requirement_ids"] = ["req_999"]

    for change, message in [(skip, "one entry for each of releases 1 to 3"), (invent, "req_999")]:
        with pytest.raises(LlmFailed, match=message):
            matched(plan, Matcher(change))


def test_a_match_must_cite_a_stored_item_and_a_cluster_of_the_run(plan):
    _, report, prd = plan
    item = to_item(GITHUB, RELEASES[0], NOW)
    cluster = report.pain_points[0].cluster_id

    def problems(**match):
        found = ChangelogMatch(kind="fix", reason="r", **match)
        return match_problems([found], {item.id: item}, report, prd)

    assert problems(item_id=item.id, cluster_ids=[cluster], requirement_ids=["req_001"]) == []
    assert problems(item_id="rel_unknown", cluster_ids=[cluster]) == [
        "rel_unknown is not a stored release item"
    ]
    assert "is not a pain point of this run" in problems(item_id=item.id, cluster_ids=["cl_x"])[0]
    assert (
        "is not a requirement of this run"
        in (problems(item_id=item.id, requirement_ids=["req_999"])[0])
    )
    with pytest.raises(ValidationError, match="names each id once"):
        ChangelogMatch(item_id=item.id, kind="fix", reason="r", cluster_ids=[cluster, cluster])


# Alerts


def test_both_alerts_are_raised_and_maintenance_raises_none(plan):
    _, store, _ = matched(plan)
    _, report, _ = plan
    alerts = run_alerts("run_a", *plan, store)

    assert alerts.items_matched == 3
    feature, fix = alerts.alerts  # newest release first
    assert (feature.kind, feature.item.title, feature.cluster_ids) == (
        "unplanned_feature", "Group budgets", [],
    )  # fmt: skip
    payments = next(p for p in report.pain_points if "Payments" in p.label)
    assert (fix.kind, fix.product_name, fix.item.title) == (
        "shipped_fix", "Tabby", "No more daily cap",
    )  # fmt: skip
    assert fix.cluster_ids == [payments.cluster_id] and fix.pain_point_labels == [payments.label]
    assert fix.requirement_ids == ["req_001"] and set(fix.follow_ups) == {payments.cluster_id}

    text = render_alerts(alerts)
    assert f"SHIPPED FIX  Tabby shipped a fix for: {payments.label}" in text
    assert "NEW FEATURE  Tabby shipped something not in our roadmap" in text
    assert "No more daily cap (2026-08-10, github)  https://t.example/1" in text
    assert "Maintenance" not in text


def test_a_feature_that_one_of_our_requirements_already_plans_raises_no_alert(plan):
    _, report, _ = plan
    item = to_item(GITHUB, RELEASES[1], NOW)
    planned = ChangelogMatch(
        item_id=item.id, kind="feature", requirement_ids=["req_002"], reason="r"
    )
    assert build_alerts([planned], {item.id: item}, {TABBY: "Tabby"}, report) == []


def test_alerts_whose_matches_no_longer_fit_the_run_are_an_error_not_a_gap(plan):
    _, store, _ = matched(plan)
    competitors, report, prd = plan
    payments = next(p for p in report.pain_points if "Payments" in p.label)
    dropped = report.model_copy(
        update={"pain_points": [p for p in report.pain_points if p is not payments]}
    )
    with pytest.raises(StageOutputInvalid, match="match it again"):
        run_alerts("run_a", competitors, dropped, prd, store)


def test_the_alert_schema_requires_the_citation():
    item = to_item(GITHUB, RELEASES[0], NOW)
    base = {"item": item, "product_name": "Tabby", "reason": "r"}
    with pytest.raises(ValidationError, match="cites the pain point"):
        ChangelogAlert(kind="shipped_fix", **base)
    with pytest.raises(ValidationError, match="matches nothing"):
        ChangelogAlert(kind="unplanned_feature", requirement_ids=["req_001"], **base)


# The follow-up: the complaint's share before and after the release

SETTINGS = TrendSettings(
    months=12, window_months=3, min_month_reviews=5, min_window_reviews=15, rising_threshold=0.25
)


def trend(*months: tuple[str, int, int]) -> Trend:
    return Trend(
        months=[
            TrendPoint(month=month, reviews=reviews, total_reviews=total, enough=total >= 5)
            for month, reviews, total in months
        ]
    )


def test_a_complaint_that_fades_after_the_fix_shows_in_the_follow_up():
    series = trend(
        ("2026-03", 8, 20), ("2026-04", 10, 20), ("2026-05", 12, 20), ("2026-06", 9, 20),
        ("2026-07", 4, 20), ("2026-08", 2, 20), ("2026-09", 0, 20),
    )  # fmt: skip
    result = follow_up(series, datetime(2026, 6, 15, tzinfo=UTC), SETTINGS)

    # Three months on each side; June itself is left out.
    assert (result.share_before, result.share_after, result.change) == (0.5, 0.1, -0.8)


def test_a_release_too_recent_or_undated_gives_no_follow_up():
    series = trend(("2026-06", 9, 20), ("2026-07", 9, 20), ("2026-08", 9, 20), ("2026-09", 2, 6))
    recent = follow_up(series, datetime(2026, 8, 20, tzinfo=UTC), SETTINGS)
    assert recent.share_before == 0.45 and recent.share_after is None and recent.change is None

    undated = follow_up(series, None, SETTINGS)
    assert (undated.share_before, undated.share_after) == (None, None)


# Storage, API and CLI


def test_sources_items_and_matches_are_stored(sessions, plan, run_input):
    from productfoundry.orchestrator import RunRecord
    from productfoundry.storage import ChangelogRepository, PostgresRunStore

    PostgresRunStore(sessions).create(
        RunRecord(id="run_a", input=run_input, seed=0, stages=[], created_at=NOW, updated_at=NOW)
    )
    store = ChangelogRepository(sessions)
    store.track(GITHUB, "Tabby")
    store.track(GITHUB, "Tabby")
    assert store.sources() == [GITHUB] and store.product_names() == {TABBY: "Tabby"}

    source = FakeReleases({GITHUB.target: RELEASES})
    assert collect({"github": source}, store, now=lambda: NOW)[0].new == 3
    assert collect({"github": source}, store, now=lambda: NOW)[0].new == 0
    undated = to_item(GITHUB, RawRelease("tabby#9", "Undated", ""), NOW)
    store.add_items([undated])
    assert [item.title for item in store.items([TABBY])] == [
        "Group budgets", "No more daily cap", "Maintenance", "Undated",
    ]  # fmt: skip

    matches = match_run("run_a", *plan, llm=gateway(groq=Matcher()), store=store)
    assert len(matches) == 4 and store.matches("run_a") == sorted(matches, key=lambda m: m.item_id)
    assert len(run_alerts("run_a", *plan, store).alerts) == 2
    store.clear_matches("run_a")
    assert store.matches("run_a") == []


def run_with_plan(store, plan, run_input):
    """A completed run whose stage outputs are the fixture's plan."""
    from productfoundry.orchestrator import PIPELINE

    competitors, report, prd = plan
    stages = FAKE_STAGES | {
        "s1_competitors": lambda *_: competitors,
        "s3_pain_points": lambda *_: report,
        "s4_prd": lambda *_: prd,
    }
    orchestrator = Orchestrator(store, stages, pipeline=PIPELINE[:4])
    run = orchestrator.resume(orchestrator.create_run(run_input, seed=5).id)
    orchestrator.approve(run.id)
    orchestrator.resume(run.id)
    orchestrator.approve(run.id)
    return orchestrator, orchestrator.resume(run.id)


def test_the_api_shows_a_run_s_changelog_alerts(plan, run_input):
    runs = InMemoryRunStore()
    orchestrator, run = run_with_plan(runs, plan, run_input)
    changelog, source = tracked()
    collect({"github": source}, changelog, now=lambda: NOW)
    match_run(run.id, *plan, llm=gateway(groq=Matcher()), store=changelog)
    client = TestClient(
        create_app(Backend(runs, RecordingQueue(), orchestrator, changelog=changelog))
    )

    body = client.get(f"/runs/{run.id}/changelog").json()
    assert body["items_matched"] == 3
    assert [alert["kind"] for alert in body["alerts"]] == ["unplanned_feature", "shipped_fix"]
    assert body["alerts"][1]["item"]["title"] == "No more daily cap"
    assert client.get("/runs/run_missing/changelog").status_code == 404

    no_database = Backend(runs, RecordingQueue(), orchestrator)
    assert TestClient(create_app(no_database)).get(f"/runs/{run.id}/changelog").status_code == 400


def test_the_cli_tracks_a_source_and_prints_a_run_s_alerts(
    sessions, db_engine, plan, run_input, monkeypatch, capsys
):
    from productfoundry.storage import ChangelogRepository, PostgresRunStore

    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    _, run = run_with_plan(PostgresRunStore(sessions), plan, run_input)

    assert main(["changelog", "track", "--product", "Tabby", "--run", run.id]) == 1
    assert "name a source" in capsys.readouterr().err
    track = ["changelog", "track", "--product", "Tabby", "--run", run.id]
    assert main([*track, "--github", "tabby-app/tabby"]) == 0
    assert "tracking github tabby-app/tabby for Tabby" in capsys.readouterr().out
    store = ChangelogRepository(sessions)
    assert store.sources() == [GITHUB]

    assert main(["changelog", "alerts", run.id]) == 0
    assert "0 release items compared" in capsys.readouterr().out

    collect({"github": FakeReleases({GITHUB.target: RELEASES})}, store, now=lambda: NOW)
    match_run(run.id, *plan, llm=gateway(groq=Matcher()), store=store)
    assert main(["changelog", "alerts", run.id]) == 0
    assert "SHIPPED FIX  Tabby shipped a fix for:" in capsys.readouterr().out
    assert main(["changelog", "alerts", run.id, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["items_matched"] == 3


def test_the_recorded_matching_answer_is_accepted_by_the_schema(plan):
    """The fixture a provider would return, as a plain recorded response."""
    recorded = json.loads(
        (Path(__file__).parent / "fixtures/llm/changelog_matching.json").read_text("utf-8")
    )
    _, report, _ = plan
    payments = next(p for p in report.pain_points if "Payments" in p.label)
    answer = json.loads(
        json.dumps(recorded["answer"]).replace("CLUSTER_PAYMENTS", payments.cluster_id)
    )
    matches, store, _ = matched(plan, FakeProvider(answer))

    assert [m.kind for m in matches] == ["feature", "fix", "other"]
    assert [alert.kind for alert in run_alerts("run_a", *plan, store).alerts] == [
        "unplanned_feature", "shipped_fix",
    ]  # fmt: skip
