import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from productfoundry.api.app import Backend, create_app
from productfoundry.cli import main
from productfoundry.core.errors import QuotaExhausted
from productfoundry.core.pricing import PricingSnapshot, diff_snapshots
from productfoundry.jobs import RecordingQueue
from productfoundry.jobs.worker import next_pricing_refresh, pricing_counts
from productfoundry.llm.fakes import FakeProvider
from productfoundry.market.pricing import (
    describe_change,
    refresh_tracked,
    render_alert,
    snapshot_pricing,
)
from productfoundry.orchestrator import InMemoryRunStore, Orchestrator
from productfoundry.sources.pricing import FakeFetcher
from productfoundry.stages.fakes import FAKE_STAGES
from productfoundry.storage.memory import InMemoryAlertStore, InMemoryPricingStore
from test_pricing import ALLOW_ALL, PAGES, PRODUCT, answer, extracted
from test_s3_pain_points import gateway

URL = PAGES["toggle"]["url"]
TEXT = PAGES["toggle"]["text"]
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def changed(edit) -> PricingSnapshot:
    """The toggle fixture's snapshot a week later, with `edit` applied to its plans."""
    data = extracted("toggle").model_dump(mode="json")
    edit(data["plans"])
    data["free_tier"] = any(plan["is_free"] for plan in data["plans"])
    data["fetched_at"] = (NOW + timedelta(days=7)).isoformat()
    return PricingSnapshot.model_validate(data)


def diff(edit) -> list[dict]:
    changes = diff_snapshots(extracted("toggle"), changed(edit))
    return [change.model_dump(mode="json", exclude_defaults=True) for change in changes]


# The diff: one fixture pair per kind of change


def test_identical_snapshots_have_no_change_and_features_are_not_compared():
    def reword(plans):
        plans[1]["features"] = ["As many expenses as you like"]
        plans[1]["limits"] = plans[1]["limits"][::-1]

    assert diff(lambda plans: None) == [] and diff(reword) == []


def test_a_price_that_went_up():
    def raise_plus(plans):
        plans[1]["prices"][0]["amount"] = "199"

    assert diff(raise_plus) == [
        {
            "kind": "price_increased",
            "plan": "Plus",
            "currency": "INR",
            "period": "month",
            "old_amount": "149",
            "new_amount": "199",
        }
    ]


def test_a_price_that_went_down_is_told_apart_by_currency_and_period():
    def cut_family_usd(plans):
        plans[2]["prices"][2]["amount"] = "3.99"

    (change,) = diff_snapshots(extracted("toggle"), changed(cut_family_usd))
    assert (change.kind, change.plan, change.currency, change.period) == (
        "price_decreased", "Family", "USD", "month",
    )  # fmt: skip
    assert (change.old_amount, change.new_amount) == (Decimal("4.99"), Decimal("3.99"))
    assert describe_change(change) == "Family: price fell from 4.99 to 3.99 USD per month"


def test_a_plan_added_and_a_plan_removed():
    def swap_plans(plans):
        del plans[2]
        plans.append(
            {"name": "Team", "prices": [{"amount": "499", "currency": "INR", "period": "month"}]}
        )

    assert diff(swap_plans) == [
        {"kind": "plan_added", "plan": "Team"},
        {"kind": "plan_removed", "plan": "Family"},
    ]


def test_a_renamed_plan_is_matched_whatever_the_case_of_its_name():
    def shout(plans):
        plans[1]["name"] = "PLUS"

    assert diff(shout) == []


def test_a_limit_changed():
    def tighten(plans):
        plans[0]["limits"] = ["Up to 3 expenses per day", "1 group"]

    assert diff(tighten) == [
        {
            "kind": "limits_changed",
            "plan": "Free",
            "old_limits": ["Up to 5 expenses per day", "1 group"],
            "new_limits": ["Up to 3 expenses per day", "1 group"],
        }
    ]


def test_a_price_option_that_appears_or_disappears():
    def drop_yearly_add_gbp(plans):
        del plans[1]["prices"][1]
        plans[1]["prices"].append({"amount": "2.49", "currency": "GBP", "period": "month"})

    kinds = [(c["kind"], c.get("currency"), c.get("period")) for c in diff(drop_yearly_add_gbp)]
    assert kinds == [("price_added", "GBP", "month"), ("price_removed", "INR", "year")]


def test_a_free_plan_that_starts_to_cost_money():
    def charge(plans):
        plans[0]["is_free"] = False
        plans[0]["prices"] = [{"amount": "49", "currency": "INR", "period": "month"}]

    (change,) = diff_snapshots(extracted("toggle"), changed(charge))
    assert describe_change(change) == "Free: new price 49 INR per month"


# A changed page produces one alert; an unchanged one makes no LLM call


class Tracker:
    """One tracked page whose text and whose extraction can be changed between passes."""

    def __init__(self) -> None:
        self.text = TEXT
        self.answer = answer("toggle")
        self.provider = FakeProvider(self.answer)
        self.fetcher = FakeFetcher({URL: self.text})
        self.store, self.alerts = InMemoryPricingStore(), InMemoryAlertStore()

    def raise_plus_price(self) -> None:
        self.text = self.text.replace("₹149 per month", "₹199 per month")
        self.answer = copy.deepcopy(self.answer)
        self.answer["plans"][1]["prices"][0]["amount"] = "199"
        self.provider = FakeProvider(self.answer)
        self.fetcher = FakeFetcher({URL: self.text})

    def parts(self) -> dict:
        return {
            "fetcher": self.fetcher,
            "robots": ALLOW_ALL,
            "llm": gateway(gemini=self.provider),
            "store": self.store,
            "alerts": self.alerts,
        }

    def snapshot(self):
        return snapshot_pricing(PRODUCT, "Tallyo", URL, **self.parts())


def test_an_unchanged_page_makes_no_llm_call_and_stores_nothing():
    tracker = Tracker()
    first = tracker.snapshot()
    again = tracker.snapshot()

    assert not first.unchanged and again.unchanged and again.snapshot == first.snapshot
    assert len(tracker.provider.requests) == 1  # the second pass asked nothing
    assert len(tracker.fetcher.fetched) == 2  # the page itself is read each time
    assert len(tracker.store.history(PRODUCT)) == 1 and tracker.alerts.list() == []


def test_a_changed_page_produces_one_alert():
    tracker = Tracker()
    first = tracker.snapshot()
    tracker.raise_plus_price()
    second = tracker.snapshot()

    (alert,) = tracker.alerts.list()
    assert second.alert == alert and len(tracker.store.history(PRODUCT)) == 2
    assert alert.product_name == "Tallyo" and alert.url == URL
    assert alert.previous_fetched_at == first.snapshot.fetched_at
    assert alert.detected_at == second.snapshot.fetched_at
    assert [describe_change(change) for change in alert.changes] == [
        "Plus: price rose from 149 to 199 INR per month"
    ]
    assert "  Plus: price rose from 149 to 199 INR per month" in render_alert(alert)

    # A third pass over the same changed page is quiet again.
    assert tracker.snapshot().unchanged and len(tracker.alerts.list()) == 1


def test_a_page_whose_text_changed_but_whose_plans_did_not_gives_a_snapshot_and_no_alert():
    tracker = Tracker()
    tracker.snapshot()
    tracker.fetcher = FakeFetcher({URL: TEXT + "\nNew: dark mode"})
    outcome = tracker.snapshot()

    assert not outcome.unchanged and outcome.alert is None and tracker.alerts.list() == []
    assert len(tracker.store.history(PRODUCT)) == 2


# The weekly pass


def test_the_weekly_pass_reads_every_tracked_page_and_one_failure_does_not_stop_it():
    tracker = Tracker()
    tracker.snapshot()
    other = "https://tallyo.example/in/pricing"
    tracker.store.add(extracted("toggle").model_copy(update={"url": other}), "Tallyo")
    assert [page.url for page in tracker.store.tracked()] == [other, URL]

    tracker.raise_plus_price()  # the fetcher now knows only URL: the other page is a 404
    outcomes = refresh_tracked(**tracker.parts())

    assert [outcome.url for outcome in outcomes] == [other, URL]
    assert "HTTP 404" in outcomes[0].skipped and outcomes[1].alert is not None
    assert pricing_counts(outcomes) == {"pages": 2, "unchanged": 0, "alerts": 1, "skipped": 1}


def test_the_weekly_pass_stops_asking_once_the_quota_is_exhausted():
    class OutOfQuota:
        calls = 0

        def complete(self, task, messages, schema):
            self.calls += 1
            raise QuotaExhausted("every provider is out of quota")

    store = InMemoryPricingStore()
    urls = [f"https://tallyo.example/{n}" for n in range(3)]
    for url in urls:
        store.add(extracted("toggle").model_copy(update={"url": url, "text_hash": "old"}), "Tallyo")
    llm = OutOfQuota()
    outcomes = refresh_tracked(
        fetcher=FakeFetcher(dict.fromkeys(urls, TEXT)),
        robots=ALLOW_ALL,
        llm=llm,
        store=store,
        alerts=InMemoryAlertStore(),
    )

    assert [outcome.skipped for outcome in outcomes] == ["LLM quota exhausted"] * 3
    assert llm.calls == 1


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 4, 12, 0, tzinfo=UTC), datetime(2026, 10, 5, 3, 0, tzinfo=UTC)),  # Sun
        (datetime(2026, 10, 5, 2, 59, tzinfo=UTC), datetime(2026, 10, 5, 3, 0, tzinfo=UTC)),
        (datetime(2026, 10, 5, 3, 0, tzinfo=UTC), datetime(2026, 10, 12, 3, 0, tzinfo=UTC)),
        (datetime(2026, 10, 8, 9, 30, tzinfo=UTC), datetime(2026, 10, 12, 3, 0, tzinfo=UTC)),
    ],
)
def test_pricing_pages_are_read_on_mondays_at_three_utc(now, expected):
    assert next_pricing_refresh(now) == expected


# Stored and shown through the API


def test_alerts_are_stored_and_listed_newest_first(sessions):
    from productfoundry.storage import PricingAlertRepository, PricingRepository

    tracker = Tracker()
    tracker.store, tracker.alerts = PricingRepository(sessions), PricingAlertRepository(sessions)
    tracker.snapshot()
    tracker.raise_plus_price()
    tracker.snapshot()

    (alert,) = tracker.alerts.list()
    assert alert.changes[0].new_amount == Decimal("199")
    assert tracker.alerts.list(PRODUCT) == [alert] and tracker.alerts.list("prod_other") == []
    assert [(page.product_name, page.url) for page in tracker.store.tracked()] == [("Tallyo", URL)]


def test_the_api_shows_the_alerts():
    tracker = Tracker()
    tracker.snapshot()
    tracker.raise_plus_price()
    tracker.snapshot()
    store = InMemoryRunStore()
    backend = Backend(
        store, RecordingQueue(), Orchestrator(store, FAKE_STAGES), pricing_alerts=tracker.alerts
    )
    client = TestClient(create_app(backend))

    (alert,) = client.get("/pricing/alerts").json()
    assert alert["product_name"] == "Tallyo" and alert["changes"] == [
        {
            "kind": "price_increased",
            "plan": "Plus",
            "currency": "INR",
            "period": "month",
            "billed_annually": False,
            "old_amount": "149",
            "new_amount": "199",
            "old_limits": [],
            "new_limits": [],
        }
    ]
    assert client.get("/pricing/alerts", params={"product_id": "prod_other"}).json() == []
    assert client.get("/pricing/alerts", params={"product_id": PRODUCT}).json() == [alert]

    no_database = Backend(store, RecordingQueue(), Orchestrator(store, FAKE_STAGES))
    assert TestClient(create_app(no_database)).get("/pricing/alerts").json() == []


def test_the_cli_lists_alerts(sessions, db_engine, monkeypatch, capsys):
    from productfoundry.storage import PricingAlertRepository, PricingRepository

    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    assert main(["pricing", "alerts"]) == 0
    assert "no price change has been found" in capsys.readouterr().out

    tracker = Tracker()
    tracker.store, tracker.alerts = PricingRepository(sessions), PricingAlertRepository(sessions)
    tracker.snapshot()
    tracker.raise_plus_price()
    tracker.snapshot()
    assert main(["pricing", "alerts"]) == 0
    assert "Plus: price rose from 149 to 199 INR per month" in capsys.readouterr().out
