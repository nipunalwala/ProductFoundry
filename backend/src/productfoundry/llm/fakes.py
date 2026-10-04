"""Stand-ins for tests and offline runs: a scripted provider, record/replay, in-memory stores."""

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from productfoundry.llm.types import (
    LlmCall,
    Provider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)


class FakeProvider:
    """Answers from a script, in order. An exception in the script is raised.

    A dict or a model is sent as JSON; a string is sent as it is. When the
    script runs out, the last entry repeats.
    """

    def __init__(self, *script: Any) -> None:
        if not script:
            raise ValueError("a FakeProvider needs at least one scripted answer")
        self._script = list(script)
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        answer = self._script[min(len(self.requests), len(self._script) - 1)]
        self.requests.append(request)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, ProviderResponse):
            return answer
        if isinstance(answer, BaseModel):
            answer = answer.model_dump_json()
        elif not isinstance(answer, str):
            answer = json.dumps(answer)
        return ProviderResponse(text=answer, input_tokens=10, output_tokens=5)


def _fixture_path(directory: Path, request: ProviderRequest) -> Path:
    return directory / f"{request.task}-{request.prompt_hash[:16]}.json"


class RecordingProvider:
    """Calls a real provider and saves each response as a fixture for `ReplayProvider`."""

    def __init__(self, inner: Provider, directory: Path) -> None:
        self._inner = inner
        self._directory = directory

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        response = self._inner.complete(request)
        self._directory.mkdir(parents=True, exist_ok=True)
        fixture = {"task": request.task, "model": request.model, **asdict(response)}
        _fixture_path(self._directory, request).write_text(
            json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return response


class ReplayProvider:
    """Serves recorded responses by task and prompt hash. Never touches the network."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        path = _fixture_path(self._directory, request)
        if not path.exists():
            raise ProviderError("rejected", f"no recorded response at {path}")
        fixture = json.loads(path.read_text(encoding="utf-8"))
        return ProviderResponse(
            fixture["text"], fixture.get("input_tokens", 0), fixture.get("output_tokens", 0)
        )


class InMemoryCallStore:
    def __init__(self) -> None:
        self.calls: list[LlmCall] = []

    def record(self, call: LlmCall) -> None:
        self.calls.append(call)

    def cached(self, task: str, model: str, prompt_hash: str) -> dict[str, Any] | None:
        for call in reversed(self.calls):
            if (
                (call.task, call.model, call.prompt_hash) == (task, model, prompt_hash)
                and call.outcome == "ok"
                and call.response is not None
            ):
                return call.response
        return None


class InMemoryUsageStore:
    def __init__(self) -> None:
        self._usage: dict[tuple[str, date], list[int]] = defaultdict(lambda: [0, 0])

    def get(self, provider: str, day: date) -> tuple[int, int]:
        requests, tokens = self._usage[provider, day]
        return requests, tokens

    def add(self, provider: str, day: date, requests: int, tokens: int) -> None:
        entry = self._usage[provider, day]
        entry[0] += requests
        entry[1] += tokens
