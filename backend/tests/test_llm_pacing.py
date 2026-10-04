from datetime import UTC, datetime

from pydantic import BaseModel

from productfoundry.llm import Gateway, ProviderResponse, load_routing
from productfoundry.llm.fakes import FakeProvider, InMemoryCallStore, InMemoryUsageStore

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


class Verdict(BaseModel):
    label: str


class Ticker:
    """A monotonic clock that only moves when the gateway sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def paced_gateway(provider, ticker) -> Gateway:
    return Gateway(
        load_routing(),
        {"groq": provider},
        InMemoryCallStore(),
        InMemoryUsageStore(),
        clock=lambda: NOW,
        sleep=ticker.sleep,
        monotonic=lambda: ticker.now,
    )


def ask(gateway, n):
    messages = [{"role": "user", "content": f"review {n}"}]
    return gateway.complete("review_sentiment", messages, Verdict)


def test_calls_wait_for_the_per_minute_token_limit():
    limit = load_routing().providers["groq"].tokens_per_minute
    big = ProviderResponse(text='{"label": "negative"}', input_tokens=limit, output_tokens=0)
    ticker = Ticker()
    gateway = paced_gateway(FakeProvider(big), ticker)

    ask(gateway, 1)
    assert ticker.sleeps == []
    ask(gateway, 2)  # the minute's tokens are used up
    assert ticker.sleeps == [60.0]
    ticker.now += 120
    ask(gateway, 3)
    assert ticker.sleeps == [60.0]


def test_calls_wait_for_the_per_minute_request_limit():
    limit = load_routing().providers["groq"].requests_per_minute
    ticker = Ticker()
    provider = FakeProvider({"label": "negative"})
    gateway = paced_gateway(provider, ticker)
    for n in range(limit):
        ask(gateway, n)
        ticker.now += 1
    assert ticker.sleeps == []
    ask(gateway, limit)
    assert ticker.sleeps == [60.0 - limit]
    assert len(provider.requests) == limit + 1


def test_a_single_request_larger_than_the_limit_is_still_sent():
    ticker = Ticker()
    provider = FakeProvider({"label": "negative"})
    gateway = paced_gateway(provider, ticker)
    huge = [{"role": "user", "content": "x" * 100_000}]
    gateway.complete("review_sentiment", huge, Verdict)
    assert ticker.sleeps == [] and len(provider.requests) == 1
