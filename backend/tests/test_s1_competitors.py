import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from conftest import run_input_data
from productfoundry.core.competitors import CompetitorList
from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.names import normalise_name, same_product
from productfoundry.core.run_input import RunInput
from productfoundry.llm import Gateway, load_routing
from productfoundry.llm.fakes import FakeProvider, InMemoryCallStore, InMemoryUsageStore
from productfoundry.orchestrator import InMemoryRunStore, Orchestrator, RunStatus, Services
from productfoundry.orchestrator.checkpoints import edit_competitors
from productfoundry.sources import SourceError, StoreApp
from productfoundry.sources.app_store import AppStoreLookup
from productfoundry.sources.google_play import GooglePlayLookup
from productfoundry.sources.robots import Robots
from productfoundry.sources.search import SearchResult
from productfoundry.sources.search.fake import FakeSearch
from productfoundry.sources.search.tavily import TavilySearch
from productfoundry.stages.fakes import FAKE_STAGES
from productfoundry.stages.s1_competitors import (
    CompetitorStage,
    Limits,
    build_queries,
    filter_schema,
)

FIXTURES = Path(__file__).parent / "fixtures"
TAVILY = json.loads((FIXTURES / "sources/tavily_search_splitwise.json").read_text("utf-8"))
ITUNES = json.loads((FIXTURES / "sources/itunes_search_splitwise.json").read_text("utf-8"))
PLAY_APP = json.loads((FIXTURES / "sources/google_play_app_splitwise.json").read_text("utf-8"))
LLM_ANSWER = json.loads((FIXTURES / "llm/competitor_filtering_splitwise.json").read_text("utf-8"))
LLM_ANSWER.pop("_note")

# The rules of https://play.google.com/robots.txt that matter here (read 2026-10-04).
PLAY_ROBOTS = "User-Agent: *\nDisallow: /store/search\nDisallow: /store/getreviews\nDisallow: /_\n"
PLAY_URL = "https://play.google.com/store/apps/details?id=com.Splitwise.SplitwiseMobile"


def splitwise_input(**overrides) -> RunInput:
    data = run_input_data(
        idea="A bill splitting app with no daily limits",
        target_users="Flatmates and friends in India",
        incumbent={"name": "Splitwise", "urls": ["https://www.splitwise.com"]},
    )
    return RunInput.model_validate(data | overrides)


def search_results() -> list[SearchResult]:
    return [SearchResult(r["title"], r["url"], r["content"]) for r in TAVILY["results"]]


class FakeLookup:
    def __init__(self, apps: dict[str, StoreApp], fail: bool = False) -> None:
        self.apps = apps
        self.fail = fail
        self.asked: list[str] = []

    def find(self, name: str, region: str) -> StoreApp | None:
        self.asked.append(name)
        if self.fail:
            raise SourceError("store is down")
        return next((app for key, app in self.apps.items() if same_product(key, name)), None)


def gateway(*script, fallback=None):
    providers = {"gemini": FakeProvider(*script)}
    if fallback is not None:
        providers["groq"] = FakeProvider(fallback)
    made = Gateway(
        load_routing(),
        providers,
        InMemoryCallStore(),
        InMemoryUsageStore(),
        clock=lambda: datetime(2026, 10, 4, tzinfo=UTC),
        sleep=lambda _: None,
    )
    return made, providers


def services(llm, **lookups) -> Services:
    queries = build_queries(splitwise_input(), 3)
    search = FakeSearch({queries[0]: search_results(), queries[1]: search_results()[:3]})
    return Services(llm=llm, search=search, app_lookups=lookups)


def run_stage(answer=LLM_ANSWER, run_input=None, **lookups) -> CompetitorList:
    llm, _ = gateway(answer)
    return CompetitorStage()(run_input or splitwise_input(), {}, services(llm, **lookups))


# Names


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("Splitwise", "Splitwise: Split Bills", True),
        ("Tricount", "tricount app", True),
        ("Settle Up", "Settle Up - Group Expenses", True),
        ("Splid", "Splid – Split group bills", True),
        ("Splitwise", "Splitkaro", False),
        ("Split", "Splitwise", True),
        ("Go", "Google Pay", False),
        ("", "Splitwise", False),
    ],
)
def test_product_names_are_compared_without_subtitles(first, second, same):
    assert same_product(first, second) is same
    assert normalise_name("  Tricount: Split & Settle  ") == "tricount"


# Search adapter


def tavily(handler) -> TavilySearch:
    return TavilySearch("tvly-test", httpx.Client(transport=httpx.MockTransport(handler)))


def test_tavily_sends_the_documented_request_and_parses_the_results():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=TAVILY)

    search = tavily(handler)
    results = search.search("Splitwise alternatives", region="IN", domains=["play.google.com"])

    assert seen["url"] == "https://api.tavily.com/search"
    assert seen["auth"] == "Bearer tvly-test"
    assert seen["body"] == {
        "query": "Splitwise alternatives",
        "max_results": 8,
        "search_depth": "basic",
        "include_domains": ["play.google.com"],
        "country": "india",
    }
    assert len(results) == 7 and search.requests == 1
    assert results[1] == SearchResult(
        "Tricount - Split & settle bills with friends",
        "https://www.tricount.com/",
        TAVILY["results"][1]["content"],
    )


@pytest.mark.parametrize(("status", "message"), [(401, "API key"), (432, "limit"), (500, "500")])
def test_tavily_errors_are_reported_without_the_key(status, message):
    search = tavily(lambda request: httpx.Response(status, json={"detail": "tvly-test"}))
    with pytest.raises(SourceError, match=message) as error:
        search.search("anything")
    assert "tvly-test" not in str(error.value)


# Store lookups


def test_app_store_lookup_finds_the_app_by_name_in_a_recorded_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json=ITUNES)

    lookup = AppStoreLookup(httpx.Client(transport=httpx.MockTransport(handler)))
    app = lookup.find("Splitwise", "IN")

    assert seen == {"term": "Splitwise", "entity": "software", "country": "in", "limit": "5"}
    assert app.store_id == "458023433"
    assert app.name == "Splitwise" and app.developer == "Splitwise, Inc."
    assert app.url == "https://apps.apple.com/in/app/splitwise/id458023433"
    assert lookup.find("Settle Up", "IN").store_id == "737534985"
    assert lookup.find("An app that is not there", "IN") is None


def test_app_store_failure_is_a_source_error():
    lookup = AppStoreLookup(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    )
    with pytest.raises(SourceError, match="503"):
        lookup.find("Splitwise", "IN")


def play_lookup(robots_text=PLAY_ROBOTS, app=PLAY_APP):
    search = FakeSearch(
        {
            "Splitwise app": [
                SearchResult("Help", "https://play.google.com/store/apps/dev?id=123", ""),
                SearchResult("Splitwise", f"{PLAY_URL}&hl=en_IN", ""),
                SearchResult("Splitwise again", f"{PLAY_URL}&gl=us", ""),
            ]
        }
    )
    fetched = []

    def fetch_app(app_id, country):
        fetched.append((app_id, country))
        return app

    return GooglePlayLookup(search, Robots(lambda url: robots_text), fetch_app), search, fetched


def test_google_play_lookup_finds_the_id_by_search_and_reads_only_the_details_page():
    lookup, search, fetched = play_lookup()
    app = lookup.find("Splitwise", "IN")

    assert search.queries == [("Splitwise app", ("play.google.com",))]
    assert fetched == [("com.Splitwise.SplitwiseMobile", "in")]
    assert app == StoreApp(
        store_id="com.Splitwise.SplitwiseMobile",
        name="Splitwise",
        developer="Splitwise",
        url=PLAY_URL,
        description=PLAY_APP["summary"],
    )


def test_google_play_lookup_ignores_an_app_with_another_name():
    lookup, _, _ = play_lookup(app=PLAY_APP | {"title": "Something Else"})
    assert lookup.find("Splitwise", "IN") is None
    lookup, _, _ = play_lookup(app=None)
    assert lookup.find("Splitwise", "IN") is None
    assert lookup.find("Never searched", "IN") is None


def test_a_page_robots_txt_disallows_is_not_fetched():
    lookup, _, fetched = play_lookup(robots_text="User-agent: *\nDisallow: /store/apps\n")
    with pytest.raises(SourceError, match="robots.txt disallows"):
        lookup.find("Splitwise", "IN")
    assert fetched == []


def test_robots_rules_of_google_play():
    robots = Robots(lambda url: PLAY_ROBOTS)
    assert robots.allowed(PLAY_URL)
    assert not robots.allowed("https://play.google.com/store/search?q=splitwise&c=apps")
    assert not robots.allowed("https://play.google.com/_/PlayStoreUi/data/batchexecute")
    assert Robots(lambda url: "").allowed("https://example.com/anything")


# The stage


def test_queries_are_built_from_the_run_input():
    assert build_queries(splitwise_input(), 3) == [
        "Splitwise alternatives and competitors",
        "apps like Splitwise",
        "best apps for Flatmates and friends in India: A bill splitting app with no daily limits",
    ]
    new_idea = splitwise_input(mode="new_idea", incumbent=None)
    assert build_queries(new_idea, 3)[1] == "A bill splitting app with no daily limits app"


def test_the_output_validates_and_the_incumbent_is_first():
    output = run_stage()
    assert CompetitorList.model_validate(output.model_dump(mode="json")) == output
    assert [c.name for c in output.competitors] == [
        "Splitwise", "Tricount", "Settle Up", "Splitkaro", "Splid",
    ]  # fmt: skip
    incumbent = output.competitors[0]
    assert incumbent.is_incumbent and incumbent.url == "https://www.splitwise.com"
    assert incumbent.reason == "The incumbent named in the run input."
    assert incumbent.positioning.startswith("The best-known app")
    assert not any(c.is_incumbent for c in output.competitors[1:])
    for competitor in output.competitors:
        assert competitor.reason and competitor.positioning and competitor.target_users


def test_duplicates_of_the_same_product_are_merged():
    output = run_stage()
    tricount = [c for c in output.competitors if same_product(c.name, "tricount")]
    assert len(tricount) == 1
    assert tricount[0].url == "https://www.tricount.com/"
    assert len({c.id for c in output.competitors}) == len(output.competitors)


def test_rejected_candidates_are_listed_once_with_a_reason():
    output = run_stage()
    assert [(r.name, r.url) for r in output.rejected] == [
        ("YNAB", "https://www.ynab.com/"),
        ("How to split rent fairly with roommates", TAVILY["results"][6]["url"]),
    ]
    assert all(r.reason for r in output.rejected)


def test_a_url_no_search_result_shows_is_replaced_by_a_cited_result():
    splid = next(c for c in run_stage().competitors if c.name == "Splid")
    assert splid.url == TAVILY["results"][0]["url"]


def test_store_ids_come_from_the_lookups_for_the_platforms_of_the_run():
    play = FakeLookup(
        {
            "Splitwise": StoreApp("com.Splitwise.SplitwiseMobile", "Splitwise", "Splitwise", "u"),
            "Tricount": StoreApp("com.tribab.tricount.android", "tricount", "Tricount", "u"),
        }
    )
    ios = FakeLookup({"Settle Up": StoreApp("737534985", "Settle Up", "Step Up Labs", "u")})
    output = run_stage(google_play=play, app_store=ios)

    ids = {c.name: (c.store_ids.google_play, c.store_ids.app_store) for c in output.competitors}
    assert ids == {
        "Splitwise": ("com.Splitwise.SplitwiseMobile", None),
        "Tricount": ("com.tribab.tricount.android", None),
        "Settle Up": (None, "737534985"),
        "Splitkaro": (None, None),
        "Splid": (None, None),
    }
    assert play.asked == ["Splitwise", "Tricount", "Settle Up", "Splitkaro", "Splid"]

    web_only = run_stage(run_input=splitwise_input(platforms=["web"]), google_play=play)
    assert all(c.store_ids.google_play is None for c in web_only.competitors)


def test_store_ids_given_by_the_user_are_kept_and_not_looked_up():
    play = FakeLookup({"Splitwise": StoreApp("com.other.app", "Splitwise", "X", "u")})
    incumbent = {"name": "Splitwise", "store_ids": {"google_play": "com.given.by.user"}}
    output = run_stage(run_input=splitwise_input(incumbent=incumbent), google_play=play)
    assert output.competitors[0].store_ids.google_play == "com.given.by.user"
    assert "Splitwise" not in play.asked
    assert output.competitors[0].url == TAVILY["results"][0]["url"]


def test_a_store_that_fails_leaves_the_ids_empty_and_lookups_are_capped():
    llm, _ = gateway(LLM_ANSWER)
    failing = FakeLookup({}, fail=True)
    stage = CompetitorStage(Limits(max_store_lookups=2))
    output = stage(splitwise_input(), {}, services(llm, google_play=failing))
    assert all(c.store_ids.google_play is None for c in output.competitors)
    assert failing.asked == ["Splitwise", "Tricount", "Settle Up"]


def test_the_search_budget_is_respected():
    llm, _ = gateway(LLM_ANSWER)
    used = services(llm)
    CompetitorStage(Limits(max_queries=2))(splitwise_input(), {}, used)
    assert len(used.search.queries) == 2


def test_the_llm_is_given_the_task_the_numbered_results_and_the_prompt_file():
    llm, providers = gateway(LLM_ANSWER)
    CompetitorStage()(splitwise_input(known_competitors=["Splid"]), {}, services(llm))
    request = providers["gemini"].requests[0]
    assert request.task == "competitor_filtering"
    assert "true competitor" in request.messages[0]["content"]
    payload = json.loads(request.messages[1]["content"])
    assert payload["product"]["incumbent"] == "Splitwise"
    assert payload["known_competitors"] == ["Splid"]
    assert [r["n"] for r in payload["results"]] == [1, 2, 3, 4, 5, 6, 7]


def test_a_kept_product_that_cites_nothing_real_triggers_the_fallback():
    invented = {
        "kept": [
            {
                "name": "Imaginary Split",
                "positioning": "p",
                "target_users": "t",
                "reason": "r",
                "results": [],
            }
        ]
    }
    out_of_range = {"kept": [dict(invented["kept"][0], results=[99])]}
    llm, providers = gateway(invented, out_of_range, fallback=LLM_ANSWER)
    output = CompetitorStage()(splitwise_input(), {}, services(llm))
    assert len(providers["gemini"].requests) == 2
    assert len(providers["groq"].requests) == 1
    assert "Imaginary Split" not in [c.name for c in output.competitors]


def test_the_incumbent_and_known_competitors_need_no_citation():
    schema = filter_schema(3, ["Splitwise", "Splid"])
    entry = {"positioning": "p", "target_users": "t", "reason": "r"}
    schema.model_validate(
        {"kept": [{"name": "Splitwise app", **entry}, {"name": "Splid", **entry}]}
    )
    with pytest.raises(ValueError, match="cites no search result"):
        schema.model_validate({"kept": [{"name": "Tricount", **entry}]})
    with pytest.raises(ValueError, match="do not exist"):
        schema.model_validate({"kept": [{"name": "Tricount", "results": [4], **entry}]})


def test_missing_services_are_reported():
    with pytest.raises(ProductFoundryError, match="TAVILY_API_KEY"):
        CompetitorStage()(splitwise_input(), {}, Services())


# The checkpoint


def test_an_edited_list_submitted_at_the_checkpoint_is_what_stage_2_receives():
    received = {}

    def stage_2(run_input, earlier_outputs, services):
        received["competitors"] = earlier_outputs["s1_competitors"]
        return FAKE_STAGES["s2_reviews"](run_input, earlier_outputs, services)

    llm, _ = gateway(LLM_ANSWER)
    store = InMemoryRunStore()
    orchestrator = Orchestrator(
        store,
        FAKE_STAGES | {"s1_competitors": CompetitorStage(), "s2_reviews": stage_2},
        services=services(llm),
    )
    run = orchestrator.resume(orchestrator.create_run(splitwise_input()).id)
    assert run.status is RunStatus.AWAITING_APPROVAL

    added = {
        "name": "Khatabook",
        "url": "https://khatabook.example",
        "positioning": "A ledger app for small shops.",
        "target_users": "Shop owners in India",
    }
    edited = edit_competitors(run.stage("s1_competitors").output, remove=["splid"], add=[added])
    orchestrator.approve(run.id, edited)
    run = orchestrator.resume(run.id)

    names = [c.name for c in received["competitors"].competitors]
    assert names == ["Splitwise", "Tricount", "Settle Up", "Splitkaro", "Khatabook"]
    assert received["competitors"].competitors[-1].reason == "Added by the user at the checkpoint."
    assert len(run.stage("s1_competitors").output["competitors"]) == 5
    assert len(run.stage("s2_reviews").output["products"]) == 5


def test_checkpoint_edits_report_mistakes():
    output = run_stage().model_dump(mode="json")
    with pytest.raises(ProductFoundryError, match="no competitor named 'Venmo'"):
        edit_competitors(output, remove=["Venmo"])
    with pytest.raises(ProductFoundryError, match="at least a name"):
        edit_competitors(output, add=[{"url": "https://x.example"}])
    by_id = edit_competitors(output, remove=[output["competitors"][1]["id"]])
    assert [c["name"] for c in by_id["competitors"]] == [
        "Splitwise", "Settle Up", "Splitkaro", "Splid",
    ]  # fmt: skip


# Recorded live run (2026-10-04, Splitwise, region IN)

RECORDED = json.loads((FIXTURES / "sources/tavily_recorded_splitwise.json").read_text("utf-8"))


def recorded_search() -> FakeSearch:
    return FakeSearch(
        {
            item["request"]["query"]: [
                SearchResult(r["title"], r["url"], r["content"])
                for r in item["response"]["results"]
            ]
            for item in RECORDED
        }
    )


def test_the_recorded_live_run_replays_offline():
    from productfoundry.llm.fakes import ReplayProvider

    llm = Gateway(
        load_routing(),
        {"groq": ReplayProvider(FIXTURES / "llm")},
        InMemoryCallStore(),
        InMemoryUsageStore(),
    )
    run_input = splitwise_input(
        idea="A bill splitting app with no daily limits and UPI settle-up",
        target_users="Flatmates and friend groups in India",
    )
    output = CompetitorStage()(run_input, {}, Services(llm=llm, search=recorded_search()))

    live = json.loads((FIXTURES / "llm/competitor_list_splitwise_live.json").read_text("utf-8"))
    assert [c.name for c in output.competitors] == [c["name"] for c in live["competitors"]]
    assert output.competitors[0].is_incumbent
    assert {"Tricount", "Splid", "Settle Up"} <= {c.name for c in output.competitors}


def test_google_play_lookup_prefers_the_result_whose_title_is_the_app():
    fetched = []

    def fetch_app(app_id, country):
        fetched.append(app_id)
        return {"title": "Splitwise", "developer": "Splitwise", "summary": "Split bills"}

    lookup = GooglePlayLookup(recorded_search(), Robots(lambda url: PLAY_ROBOTS), fetch_app)
    app = lookup.find("Splitwise", "IN")
    assert app.store_id == "com.Splitwise.SplitwiseMobile"
    assert fetched == ["com.Splitwise.SplitwiseMobile"]
