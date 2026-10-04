import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

import productfoundry
from productfoundry.core.errors import QuotaExhausted
from productfoundry.llm import Gateway, LlmFailed, ProviderError, load_routing
from productfoundry.llm.fakes import (
    FakeProvider,
    InMemoryCallStore,
    InMemoryUsageStore,
    RecordingProvider,
    ReplayProvider,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
MESSAGES = [{"role": "user", "content": "Is this review negative? 'app bahut slow hai'"}]
GOOD = {"label": "negative"}
RATE_LIMIT = ProviderError("rate_limit", "HTTP 429")


class Verdict(BaseModel):
    label: str


@pytest.fixture(params=["memory", "postgres"])
def stores(request):
    if request.param == "memory":
        return InMemoryCallStore(), InMemoryUsageStore()
    from productfoundry.storage import PostgresCallStore, PostgresUsageStore

    sessions = request.getfixturevalue("sessions")
    return PostgresCallStore(sessions), PostgresUsageStore(sessions)


class Harness:
    def __init__(self, stores, **scripts):
        self.routing = load_routing()
        self.providers = {name: FakeProvider(*script) for name, script in scripts.items()}
        self.calls, self.usage = stores
        self.sleeps: list[float] = []
        self.gateway = Gateway(
            self.routing,
            self.providers,
            self.calls,
            self.usage,
            clock=lambda: NOW,
            sleep=self.sleeps.append,
        )

    def complete(self, task="review_sentiment", messages=MESSAGES, schema=Verdict):
        return self.gateway.complete(task, messages, schema)

    def requests(self) -> dict[str, int]:
        return {name: len(provider.requests) for name, provider in self.providers.items()}


def all_good(**overrides):
    return {"gemini": [GOOD], "groq": [GOOD], "openrouter": [GOOD]} | overrides


# Routing


def test_the_routing_table_matches_the_architecture():
    routing = load_routing()
    assert routing.chain_for("review_sentiment") == ["groq", "gemini", "openrouter"]
    assert routing.chain_for("switching_intent") == ["groq", "gemini", "openrouter"]
    assert routing.chain_for("changelog_matching") == ["groq", "gemini", "openrouter"]
    assert routing.chain_for("cluster_labelling") == ["gemini", "groq", "openrouter"]
    assert routing.chain_for("competitor_filtering") == ["gemini", "groq", "openrouter"]
    assert routing.chain_for("prd") == ["gemini", "openrouter", "groq"]
    assert routing.chain_for("task_breakdown") == ["gemini", "openrouter", "groq"]
    assert routing.chain_for("pricing_recommendation") == ["gemini", "openrouter", "groq"]
    with pytest.raises(KeyError, match="unknown LLM task"):
        routing.chain_for("write_a_poem")


def test_every_model_is_pinned_and_free():
    providers = load_routing().providers
    for spec in providers.values():
        assert "latest" not in spec.model
        assert spec.requests_per_day is not None
    assert providers["openrouter"].model.endswith(":free")


@pytest.mark.parametrize(
    ("task", "order"),
    [
        ("review_sentiment", ["groq", "gemini", "openrouter"]),
        ("cluster_labelling", ["gemini", "groq", "openrouter"]),
        ("prd", ["gemini", "openrouter", "groq"]),
    ],
)
def test_fallback_follows_the_order_of_the_task_group(task, order):
    for failing in range(3):
        scripts = {name: [RATE_LIMIT] for name in order[:failing]}
        scripts |= {name: [{"label": name}] for name in order[failing:]}
        harness = Harness((InMemoryCallStore(), InMemoryUsageStore()), **scripts)
        assert harness.complete(task).label == order[failing]
        expected = {name: 2 for name in order[:failing]} | {order[failing]: 1}
        expected |= {name: 0 for name in order[failing + 1 :]}
        assert harness.requests() == expected


# Failure handling


@pytest.mark.parametrize("kind", ["rate_limit", "timeout", "server_error"])
def test_a_failure_is_retried_once_on_the_same_provider_after_a_backoff(stores, kind):
    harness = Harness(stores, **all_good(groq=[ProviderError(kind, "boom"), GOOD]))
    assert harness.complete() == Verdict(label="negative")
    assert harness.requests() == {"groq": 2, "gemini": 0, "openrouter": 0}
    assert harness.sleeps == [harness.routing.retry_backoff_seconds]


def test_two_failures_move_to_the_next_provider(stores):
    harness = Harness(stores, **all_good(groq=[ProviderError("timeout", "slow")]))
    assert harness.complete().label == "negative"
    assert harness.requests() == {"groq": 2, "gemini": 1, "openrouter": 0}


@pytest.mark.parametrize("bad", ["not json", '{"wrong": 1}', '["negative"]', ""])
def test_schema_invalid_output_is_retried_and_then_falls_back(stores, bad):
    harness = Harness(stores, **all_good(groq=[bad]))
    assert harness.complete().label == "negative"
    assert harness.requests() == {"groq": 2, "gemini": 1, "openrouter": 0}


def test_json_wrapped_in_a_code_fence_is_accepted(stores):
    harness = Harness(stores, **all_good(groq=['```json\n{"label": "negative"}\n```']))
    assert harness.complete().label == "negative"
    assert harness.requests()["groq"] == 1


def test_a_rejected_request_is_not_retried(stores):
    harness = Harness(stores, **all_good(groq=[ProviderError("rejected", "HTTP 401")]))
    assert harness.complete().label == "negative"
    assert harness.requests() == {"groq": 1, "gemini": 1, "openrouter": 0}
    assert harness.sleeps == []


def test_a_provider_without_a_key_is_passed_over(stores):
    harness = Harness(stores, gemini=[GOOD])
    assert harness.complete().label == "negative"
    with pytest.raises(LlmFailed, match="API key"):
        Harness(stores).complete("prd")


def test_failures_other_than_quota_are_an_error_not_a_pause(stores):
    harness = Harness(
        stores,
        groq=[ProviderError("server_error", "HTTP 503")],
        gemini=["nope"],
        openrouter=[RATE_LIMIT],
    )
    with pytest.raises(LlmFailed, match="groq: server_error.*gemini: schema_invalid"):
        harness.complete()


# Quota


def test_a_provider_near_its_limit_is_skipped_without_being_called(stores):
    harness = Harness(stores, **all_good())
    limit = harness.routing.providers["groq"].requests_per_day
    harness.usage.add("groq", NOW.date(), int(limit * harness.routing.near_limit), 0)
    assert harness.complete().label == "negative"
    assert harness.requests() == {"groq": 0, "gemini": 1, "openrouter": 0}


def test_the_token_limit_counts_too(stores):
    harness = Harness(stores, **all_good())
    harness.usage.add("groq", NOW.date(), 1, harness.routing.providers["groq"].tokens_per_day)
    harness.complete()
    assert harness.requests()["groq"] == 0


def test_yesterdays_usage_does_not_count(stores):
    harness = Harness(stores, **all_good())
    yesterday = NOW.date().replace(day=NOW.day - 1)
    harness.usage.add("groq", yesterday, 10**6, 10**9)
    harness.complete()
    assert harness.requests()["groq"] == 1


def test_all_providers_exhausted_raises_quota_exhausted(stores):
    harness = Harness(stores, **all_good(openrouter=[RATE_LIMIT]))
    for name in ("groq", "gemini"):
        harness.usage.add(name, NOW.date(), harness.routing.providers[name].requests_per_day, 0)
    with pytest.raises(QuotaExhausted, match="groq, gemini, openrouter"):
        harness.complete()
    assert harness.requests() == {"groq": 0, "gemini": 0, "openrouter": 2}


def test_every_attempt_is_counted_with_its_tokens(stores):
    harness = Harness(stores, **all_good(groq=["nope", GOOD]))
    harness.complete()
    assert harness.usage.get("groq", NOW.date()) == (2, 30)
    assert harness.usage.get("gemini", NOW.date()) == (0, 0)


# Cache and provenance


def test_a_repeated_call_is_a_cache_hit_and_makes_no_provider_call(stores):
    harness = Harness(stores, **all_good())
    first = harness.complete()
    second = harness.complete()
    assert first == second
    assert harness.requests() == {"groq": 1, "gemini": 0, "openrouter": 0}
    assert harness.usage.get("groq", NOW.date())[0] == 1


def test_the_cache_key_is_task_model_and_prompt(stores):
    harness = Harness(stores, **all_good())
    harness.complete()
    harness.complete(messages=[{"role": "user", "content": "Another review"}])
    harness.complete("switching_intent")

    class Other(BaseModel):
        label: str
        reason: str = ""

    harness.complete(schema=Other)
    assert harness.requests()["groq"] == 4


def test_a_cached_answer_is_used_even_when_its_provider_is_now_out_of_quota(stores):
    harness = Harness(stores, **all_good())
    harness.complete()
    harness.usage.add("groq", NOW.date(), 10**6, 0)
    harness.complete()
    assert harness.requests() == {"groq": 1, "gemini": 0, "openrouter": 0}


def test_every_call_and_cache_hit_has_a_provenance_row():
    calls = InMemoryCallStore()
    harness = Harness((calls, InMemoryUsageStore()), **all_good(groq=[RATE_LIMIT]))
    harness.gateway.complete("review_sentiment", MESSAGES, Verdict, run_id="run_a")
    harness.complete()

    rows = [(c.provider, c.outcome, c.cache_hit, c.input_tokens, c.output_tokens)
            for c in calls.calls]  # fmt: skip
    assert rows == [
        ("groq", "rate_limit", False, 0, 0),
        ("groq", "rate_limit", False, 0, 0),
        ("gemini", "ok", False, 10, 5),
        ("gemini", "ok", True, 0, 0),
    ]
    ok = calls.calls[2]
    assert ok.model == harness.routing.providers["gemini"].model
    assert ok.run_id == "run_a" and ok.task == "review_sentiment"
    assert ok.response == GOOD and ok.latency_ms >= 0
    assert calls.calls[0].error == "HTTP 429"


def test_provenance_rows_reach_the_database(sessions):
    from sqlalchemy import select

    from productfoundry.storage import PostgresCallStore, PostgresUsageStore
    from productfoundry.storage.models import LlmCallRow

    stores = PostgresCallStore(sessions), PostgresUsageStore(sessions)
    harness = Harness(stores, **all_good(groq=["nope"]))
    harness.complete()
    harness.complete()
    with sessions() as session:
        rows = session.scalars(select(LlmCallRow).order_by(LlmCallRow.id)).all()
    assert [(r.provider, r.outcome, r.cache_hit) for r in rows] == [
        ("groq", "schema_invalid", False),
        ("groq", "schema_invalid", False),
        ("gemini", "ok", False),
        ("gemini", "ok", True),
    ]
    assert rows[2].response == GOOD and rows[2].input_tokens == 10


# The prompt


def test_the_provider_is_told_the_schema_and_given_the_pinned_model(stores):
    harness = Harness(stores, **all_good())
    harness.complete()
    request = harness.providers["groq"].requests[0]
    assert request.model == "groq/openai/gpt-oss-120b"
    assert request.messages[0] == MESSAGES[0]
    assert "JSON Schema" in request.messages[-1]["content"]
    assert '"label"' in request.messages[-1]["content"]
    assert request.timeout == harness.routing.timeout_seconds


# Record and replay


def test_a_recorded_response_is_replayed_without_the_live_provider(tmp_path):
    live = FakeProvider(GOOD)
    recording = Harness((InMemoryCallStore(), InMemoryUsageStore()))
    recording.providers["groq"] = RecordingProvider(live, tmp_path)
    assert recording.complete().label == "negative"
    assert len(live.requests) == 1
    assert [path.name for path in tmp_path.iterdir()] == [
        f"review_sentiment-{live.requests[0].prompt_hash[:16]}.json"
    ]

    replaying = Harness((InMemoryCallStore(), InMemoryUsageStore()))
    replaying.providers["groq"] = ReplayProvider(tmp_path)
    assert replaying.complete().label == "negative"

    with pytest.raises(LlmFailed, match="no recorded response"):
        replaying.complete(messages=[{"role": "user", "content": "never recorded"}])


# Seams


def test_only_the_litellm_provider_imports_litellm():
    package = Path(productfoundry.__file__).parent
    importers = [
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if re.search(r"^\s*(import|from) litellm", path.read_text(encoding="utf-8"), re.M)
    ]
    assert importers == ["llm/litellm_provider.py"]
