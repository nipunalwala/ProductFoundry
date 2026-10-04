import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from productfoundry.cli import main
from productfoundry.core.pricing import (
    Plan,
    PricingSnapshot,
    numbers_on_page,
    pricing_problems,
)
from productfoundry.llm import LlmFailed
from productfoundry.llm.fakes import FakeProvider
from productfoundry.market.pricing import (
    PricingOutcome,
    extract,
    render_snapshot,
    snapshot_pricing,
)
from productfoundry.sources import SourceError
from productfoundry.sources.pricing import FakeFetcher, PricingPage, read_page
from productfoundry.sources.robots import Robots
from productfoundry.storage.memory import InMemoryPricingStore
from test_s3_pain_points import gateway

PAGES = json.loads(
    (Path(__file__).parent / "fixtures/sources/pricing_pages.json").read_text("utf-8")
)
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
ALLOW_ALL = Robots(lambda url: "")
PRODUCT = "prod_tallyo"


def page(layout: str) -> PricingPage:
    return PricingPage(PAGES[layout]["url"], PAGES[layout]["text"], NOW)


def answer(layout: str) -> dict:
    return copy.deepcopy(PAGES[layout]["answer"])


def extracted(layout: str) -> PricingSnapshot:
    llm = gateway(gemini=FakeProvider(answer(layout)))
    return extract(llm, PRODUCT, PAGES[layout]["product"], page(layout))


# Numbers on the page


def test_numbers_are_read_with_western_and_indian_separators_and_decimals():
    text = "₹1,499 or Rs. 1,49,999 / year, $2.99, save 20%, 14-day trial"
    assert numbers_on_page(text) == {
        Decimal("1499"), Decimal("149999"), Decimal("2.99"), Decimal("20"), Decimal("14"),
    }  # fmt: skip


def test_a_price_passes_only_when_its_amount_and_its_currency_are_on_the_page():
    text = "Pro\n₹499 per month"

    def problems(**price):
        plan = Plan(name="Pro", prices=[{"period": "month", **price}])
        return pricing_problems([plan], None, text)

    assert problems(amount="499", currency="INR") == []
    assert problems(amount="499.00", currency="INR") == []
    assert problems(amount="5988", currency="INR") == [
        "Pro: the page does not show the amount 5988"
    ]
    assert problems(amount="499", currency="USD") == ["Pro: the page does not show USD"]
    # 49 is part of "499", not a number the page shows.
    assert problems(amount="49", currency="INR") == ["Pro: the page does not show the amount 49"]


def test_a_plan_the_page_does_not_name_and_a_trial_it_does_not_state_are_problems():
    plan = Plan(name="Ultimate", is_free=True)
    assert pricing_problems([plan], 30, "Free forever\n14-day trial") == [
        "the page names no plan 'Ultimate'",
        "the page does not show a trial of 30 days",
    ]


# The schema


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        ({"name": "Pro"}, "needs a price"),
        (
            {
                "name": "Free",
                "is_free": True,
                "prices": [{"amount": "5", "currency": "USD", "period": "month"}],
            },
            "is free",
        ),
        (
            {
                "name": "Pro",
                "prices": [
                    {"amount": "5", "currency": "USD", "period": "month"},
                    {"amount": "6", "currency": "USD", "period": "month"},
                ],
            },
            "repeats a price",
        ),
        (
            {"name": "Pro", "prices": [{"amount": "0", "currency": "USD", "period": "month"}]},
            "greater than 0",
        ),
        (
            {"name": "Pro", "prices": [{"amount": "5", "currency": "usd", "period": "month"}]},
            "should match pattern",
        ),
    ],
)
def test_the_plan_schema_rejects(plan, message):
    with pytest.raises(ValidationError, match=message):
        Plan.model_validate(plan)


def test_the_snapshot_schema_ties_the_flags_to_the_plans():
    good = extracted("toggle").model_dump(mode="json")
    for change, message in [
        ({"free_tier": False}, "free_tier"),
        ({"free_trial": False}, "trial_days needs free_trial"),
        ({"plans": good["plans"] + [good["plans"][0]]}, "unique"),
    ]:
        with pytest.raises(ValidationError, match=message):
            PricingSnapshot.model_validate(good | change)


# Extraction: the three layouts


def test_a_monthly_and_annual_page_gives_every_price_in_both_currencies():
    snapshot = extracted("toggle")

    assert [plan.name for plan in snapshot.plans] == ["Free", "Plus", "Family"]
    assert snapshot.free_tier and snapshot.free_trial and snapshot.trial_days == 14
    assert snapshot.currencies() == ["INR", "USD"]
    plus = snapshot.plans[1]
    assert [(p.amount, p.currency, str(p.period)) for p in plus.prices] == [
        (Decimal("149"), "INR", "month"),
        (Decimal("1430"), "INR", "year"),
        (Decimal("2.99"), "USD", "month"),
    ]
    assert snapshot.plans[0].limits == ["Up to 5 expenses per day", "1 group"]
    assert snapshot.text_hash == page("toggle").text_hash and snapshot.fetched_at == NOW


def test_a_per_seat_page_keeps_the_annual_and_the_monthly_figure_apart():
    snapshot = extracted("per_seat")

    starter = snapshot.plans[0]
    assert [(p.amount, p.per_seat, p.billed_annually) for p in starter.prices] == [
        (Decimal("6"), True, True),
        (Decimal("8"), True, False),
    ]
    assert not snapshot.free_tier and not snapshot.free_trial


def test_a_contact_us_tier_has_no_price_and_indian_separators_are_read():
    snapshot = extracted("contact")

    assert snapshot.plans[1].prices[0].amount == Decimal("149999")
    enterprise = snapshot.plans[2]
    assert enterprise.contact_sales and enterprise.prices == []
    text = render_snapshot(snapshot, "Ledgerly")
    assert "  Team: INR 4999 per month" in text and "  Enterprise: contact sales" in text
    assert "free tier: no, free trial: no" in text


def test_the_prompt_receives_the_page_text_and_nothing_else_about_the_product():
    provider = FakeProvider(answer("toggle"))
    extract(gateway(gemini=provider), PRODUCT, "Tallyo", page("toggle"))

    (request,) = provider.requests
    assert request.task == "pricing_extraction"
    payload = json.loads(request.messages[1]["content"])
    assert payload == {
        "product": "Tallyo",
        "url": PAGES["toggle"]["url"],
        "text": PAGES["toggle"]["text"],
    }


# The validator


def invented() -> dict:
    """The right answer, except that the yearly price was calculated: 149 x 12."""
    wrong = answer("toggle")
    wrong["plans"][1]["prices"][1]["amount"] = "1788"
    return wrong


def test_a_hallucinated_price_is_rejected_and_the_next_provider_is_asked():
    first, second = FakeProvider(invented()), FakeProvider(answer("toggle"))
    snapshot = extract(gateway(gemini=first, groq=second), PRODUCT, "Tallyo", page("toggle"))

    assert len(first.requests) == 2 and len(second.requests) == 1  # one retry, then fallback
    assert snapshot.plans[1].prices[1].amount == Decimal("1430")


def test_a_hallucinated_price_never_becomes_a_snapshot():
    store = InMemoryPricingStore()
    llm = gateway(gemini=FakeProvider(invented()))
    with pytest.raises(LlmFailed, match="does not show the amount 1788"):
        snapshot_pricing(
            PRODUCT,
            "Tallyo",
            PAGES["toggle"]["url"],
            fetcher=FakeFetcher({PAGES["toggle"]["url"]: PAGES["toggle"]["text"]}),
            robots=ALLOW_ALL,
            llm=llm,
            store=store,
        )
    assert store.history(PRODUCT) == []


def test_a_price_below_the_cut_of_a_long_page_cannot_be_extracted(monkeypatch):
    monkeypatch.setattr("productfoundry.market.pricing.MAX_PAGE_CHARS", 120)
    with pytest.raises(LlmFailed, match="the page"):
        extracted("toggle")


# Fetching


def snapshot(layout="toggle", robots=ALLOW_ALL, provider=None, store=None, fetcher=None):
    url = PAGES[layout]["url"]
    fetcher = fetcher or FakeFetcher({url: PAGES[layout]["text"]})
    provider = provider or FakeProvider(answer(layout))
    store = store or InMemoryPricingStore()
    outcome = snapshot_pricing(
        PRODUCT,
        PAGES[layout]["product"],
        url,
        fetcher=fetcher,
        robots=robots,
        llm=gateway(gemini=provider),
        store=store,
    )
    return outcome, fetcher, provider, store


def test_a_snapshot_is_fetched_extracted_and_stored():
    outcome, fetcher, _, store = snapshot()

    assert outcome.skipped is None and fetcher.fetched == [PAGES["toggle"]["url"]]
    assert store.latest(PRODUCT) == outcome.snapshot and store.names == {PRODUCT: "Tallyo"}


def test_a_page_robots_txt_disallows_is_not_fetched_and_the_skip_is_reported():
    robots = Robots(lambda url: "User-agent: *\nDisallow: /pricing\n")
    outcome, fetcher, provider, store = snapshot(robots=robots)

    assert outcome == PricingOutcome(
        url=PAGES["toggle"]["url"],
        skipped=f"robots.txt disallows fetching {PAGES['toggle']['url']}",
    )
    assert fetcher.fetched == [] and provider.requests == [] and store.history(PRODUCT) == []


def test_a_page_with_no_plan_is_skipped_not_stored():
    outcome, _, _, store = snapshot(provider=FakeProvider({"plans": []}))

    assert "lists no plan" in outcome.skipped and store.history(PRODUCT) == []


def test_a_page_that_cannot_be_read_or_is_empty_is_skipped():
    outcome, _, provider, _ = snapshot(fetcher=FakeFetcher({}))
    assert "HTTP 404" in outcome.skipped and provider.requests == []

    with pytest.raises(SourceError, match="rendered no text"):
        read_page("https://a.example/p", FakeFetcher({"https://a.example/p": " \n "}), ALLOW_ALL)


def test_the_page_hash_ignores_layout_whitespace_but_not_a_changed_price():
    def read(text):
        url = "https://a.example/p"
        return read_page(url, FakeFetcher({url: text}), ALLOW_ALL, now=lambda: NOW)

    assert read("Pro\n  $5  \n\n\n").text_hash == read("Pro\n$5").text_hash
    assert read("Pro\n$6").text_hash != read("Pro\n$5").text_hash


# Storage


def test_snapshots_are_kept_in_order_and_a_new_product_gets_its_row(sessions):
    from sqlalchemy import select

    from productfoundry.storage import PricingRepository
    from productfoundry.storage.models import ProductRow

    store = PricingRepository(sessions)
    first = extracted("toggle")
    later = first.model_copy(update={"fetched_at": NOW + timedelta(days=7), "text_hash": "changed"})
    elsewhere = first.model_copy(update={"url": "https://tallyo.example/in/pricing"})
    for item in (later, first, elsewhere):
        store.add(item, "Tallyo")

    assert [s.text_hash for s in store.history(PRODUCT)] == [
        first.text_hash, first.text_hash, "changed",
    ]  # fmt: skip
    assert store.latest(PRODUCT) == later
    assert store.latest(PRODUCT, url=elsewhere.url) == elsewhere
    assert store.latest("prod_unknown") is None and store.history("prod_unknown") == []
    with sessions() as session:
        assert [row.name for row in session.scalars(select(ProductRow))] == ["Tallyo"]


def test_the_cli_shows_stored_snapshots(sessions, db_engine, monkeypatch, capsys):
    from productfoundry.core.ids import product_id
    from productfoundry.storage import PricingRepository

    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    assert main(["pricing", "show", "--product", "Tallyo"]) == 1
    assert "no pricing snapshot is stored for Tallyo" in capsys.readouterr().out

    stored = extracted("toggle").model_copy(update={"product_id": product_id("tallyo")})
    PricingRepository(sessions).add(stored, "Tallyo")
    assert main(["pricing", "show", "--product", "Tallyo"]) == 0
    shown = capsys.readouterr().out
    assert "  Plus: INR 149 per month, INR 1430 per year, USD 2.99 per month" in shown
    assert "    limit: Up to 10 groups" in shown and "free trial: 14 days" in shown

    assert main(["--memory", "pricing", "show", "--product", "Tallyo"]) == 1
    assert "not with --memory" in capsys.readouterr().err
