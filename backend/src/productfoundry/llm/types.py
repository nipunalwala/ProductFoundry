from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Protocol, TypedDict

from productfoundry.core.errors import ProductFoundryError


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


# How one attempt ended. Everything but `ok` and `rejected` is retried once.
Outcome = Literal["ok", "rate_limit", "timeout", "server_error", "schema_invalid", "rejected"]


@dataclass(frozen=True)
class ProviderRequest:
    task: str
    model: str
    messages: list[Message]
    prompt_hash: str
    timeout: float


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class ProviderError(Exception):
    """A provider call failed. `kind` says how, so the gateway knows whether to retry."""

    def __init__(self, kind: Outcome, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class Provider(Protocol):
    def complete(self, request: ProviderRequest) -> ProviderResponse: ...


@dataclass(frozen=True)
class LlmCall:
    """Provenance of one gateway attempt or cache hit: a row of `llm_calls`."""

    task: str
    provider: str
    model: str
    prompt_hash: str
    outcome: Outcome
    created_at: datetime
    run_id: str | None = None
    cache_hit: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None
    response: dict[str, Any] | None = None  # the validated object, kept for the cache


class CallStore(Protocol):
    def record(self, call: LlmCall) -> None: ...

    def cached(self, task: str, model: str, prompt_hash: str) -> dict[str, Any] | None:
        """The stored response of an earlier successful call with the same key."""
        ...


class UsageStore(Protocol):
    def get(self, provider: str, day: date) -> tuple[int, int]:
        """(requests, tokens) used by the provider on that day."""
        ...

    def add(self, provider: str, day: date, requests: int, tokens: int) -> None: ...


class LlmFailed(ProductFoundryError):
    """No provider produced a valid answer, for reasons other than quota."""
