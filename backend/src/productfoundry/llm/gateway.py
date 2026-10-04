"""`complete(task, messages, schema)`: a schema-valid object from an available provider."""

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from productfoundry.core.clock import utcnow
from productfoundry.core.errors import QuotaExhausted
from productfoundry.llm.routing import ProviderSpec, Routing
from productfoundry.llm.types import (
    CallStore,
    LlmCall,
    LlmFailed,
    Message,
    Outcome,
    Provider,
    ProviderError,
    ProviderRequest,
    UsageStore,
)

T = TypeVar("T", bound=BaseModel)

ATTEMPTS_PER_PROVIDER = 2  # the call and one retry


class Gateway:
    def __init__(
        self,
        routing: Routing,
        providers: Mapping[str, Provider],
        calls: CallStore,
        usage: UsageStore,
        *,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._routing = routing
        self._providers = providers  # only providers that have an API key
        self._calls = calls
        self._usage = usage
        self._clock = clock
        self._sleep = sleep

    def complete(
        self, task: str, messages: Sequence[Message], schema: type[T], *, run_id: str | None = None
    ) -> T:
        chain = self._routing.chain_for(task)
        prompt = _with_schema(messages, schema)
        prompt_hash = _hash(prompt)
        out_of_quota: list[str] = []
        failures: list[str] = []

        # An answer any provider in the chain already gave is used before calling anyone.
        for name in chain:
            spec = self._routing.providers[name]
            cached = self._from_cache(task, name, spec, prompt_hash, schema, run_id)
            if cached is not None:
                return cached

        for name in chain:
            spec = self._routing.providers[name]
            if name not in self._providers:
                continue
            if self._near_limit(name, spec):
                out_of_quota.append(name)
                continue
            request = ProviderRequest(
                task, spec.model, prompt, prompt_hash, self._routing.timeout_seconds
            )
            for attempt in range(ATTEMPTS_PER_PROVIDER):
                if attempt:
                    self._sleep(self._routing.retry_backoff_seconds)
                result, outcome, error = self._attempt(name, request, schema, run_id)
                if result is not None:
                    return result
                if outcome == "rejected":
                    break
            if outcome == "rate_limit":
                out_of_quota.append(name)
            else:
                failures.append(f"{name}: {outcome} ({error})")

        if failures:
            raise LlmFailed(f"no provider answered task {task}: " + "; ".join(failures))
        if out_of_quota:
            raise QuotaExhausted(f"out of quota for task {task}: " + ", ".join(out_of_quota))
        raise LlmFailed(f"no provider in {chain} has an API key in .env")

    def check(self, provider: str, schema: type[T], messages: Sequence[Message]) -> LlmCall:
        """One live call to one provider, with the usual accounting. For `llm check`."""
        spec = self._routing.providers[provider]
        prompt = _with_schema(messages, schema)
        request = ProviderRequest(
            "check", spec.model, prompt, _hash(prompt), self._routing.timeout_seconds
        )
        recorded: list[LlmCall] = []
        self._attempt(provider, request, schema, None, on_record=recorded.append)
        return recorded[0]

    def _from_cache(
        self,
        task: str,
        name: str,
        spec: ProviderSpec,
        prompt_hash: str,
        schema: type[T],
        run_id: str | None,
    ) -> T | None:
        stored = self._calls.cached(task, spec.model, prompt_hash)
        if stored is None:
            return None
        try:
            result = schema.model_validate(stored)
        except ValidationError:
            return None  # stored under an older schema; ask again
        self._calls.record(
            LlmCall(
                task=task,
                provider=name,
                model=spec.model,
                prompt_hash=prompt_hash,
                outcome="ok",
                created_at=self._clock(),
                run_id=run_id,
                cache_hit=True,
            )
        )
        return result

    def _near_limit(self, name: str, spec: ProviderSpec) -> bool:
        requests, tokens = self._usage.get(name, self._clock().date())
        share = self._routing.near_limit
        return (
            spec.requests_per_day is not None and requests >= share * spec.requests_per_day
        ) or (spec.tokens_per_day is not None and tokens >= share * spec.tokens_per_day)

    def _attempt(
        self,
        name: str,
        request: ProviderRequest,
        schema: type[T],
        run_id: str | None,
        on_record: Callable[[LlmCall], None] | None = None,
    ) -> tuple[T | None, Outcome, str | None]:
        started = time.perf_counter()
        result: T | None = None
        outcome: Outcome = "ok"
        error: str | None = None
        input_tokens = output_tokens = 0
        try:
            response = self._providers[name].complete(request)
            input_tokens, output_tokens = response.input_tokens, response.output_tokens
            result = schema.model_validate_json(_strip_fences(response.text))
        except ProviderError as exc:
            outcome, error = exc.kind, str(exc)
        except ValidationError as exc:
            outcome = "schema_invalid"
            error = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
                for e in exc.errors()
            )

        now = self._clock()
        self._usage.add(name, now.date(), 1, input_tokens + output_tokens)
        call = LlmCall(
            task=request.task,
            provider=name,
            model=request.model,
            prompt_hash=request.prompt_hash,
            outcome=outcome,
            created_at=now,
            run_id=run_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000),
            error=error,
            response=result.model_dump(mode="json") if result is not None else None,
        )
        self._calls.record(call)
        if on_record is not None:
            on_record(call)
        return result, outcome, error


def _with_schema(messages: Sequence[Message], schema: type[BaseModel]) -> list[Message]:
    """Append the output contract, so every provider is told the same thing."""
    instruction = (
        "Reply with one JSON object and nothing else. It must validate against this "
        "JSON Schema:\n" + json.dumps(schema.model_json_schema(), sort_keys=True)
    )
    return [*messages, {"role": "system", "content": instruction}]


def _hash(prompt: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(prompt, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
