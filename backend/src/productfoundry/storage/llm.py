"""`llm_calls` and `provider_usage`: the gateway's provenance, cache and quota counters."""

from dataclasses import asdict
from datetime import date
from typing import Any

from sqlalchemy import Integer, func, select
from sqlalchemy.dialects.postgresql import insert

from productfoundry.llm.types import LlmCall
from productfoundry.storage.db import Sessions
from productfoundry.storage.models import LlmCallRow, ProviderUsageRow


class PostgresCallStore:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    def record(self, call: LlmCall) -> None:
        with self._sessions.begin() as session:
            session.add(LlmCallRow(**asdict(call)))

    def cached(self, task: str, model: str, prompt_hash: str) -> dict[str, Any] | None:
        statement = (
            select(LlmCallRow.response)
            .where(
                LlmCallRow.prompt_hash == prompt_hash,
                LlmCallRow.task == task,
                LlmCallRow.model == model,
                LlmCallRow.outcome == "ok",
                LlmCallRow.response.is_not(None),
            )
            .order_by(LlmCallRow.id.desc())
            .limit(1)
        )
        with self._sessions() as session:
            return session.scalar(statement)

    def run_totals(self, run_id: str) -> dict[str, int]:
        """What a run cost: provider attempts, answers, cache hits and tokens."""
        live = ~LlmCallRow.cache_hit
        statement = select(
            func.coalesce(func.sum(live.cast(Integer)), 0),
            func.coalesce(func.sum((live & (LlmCallRow.outcome == "ok")).cast(Integer)), 0),
            func.coalesce(func.sum(LlmCallRow.cache_hit.cast(Integer)), 0),
            func.coalesce(func.sum(LlmCallRow.input_tokens), 0),
            func.coalesce(func.sum(LlmCallRow.output_tokens), 0),
        ).where(LlmCallRow.run_id == run_id)
        with self._sessions() as session:
            row = session.execute(statement).one()
        names = ("attempts", "answered", "cache_hits", "input_tokens", "output_tokens")
        return dict(zip(names, (int(value) for value in row), strict=True))


class PostgresUsageStore:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    def get(self, provider: str, day: date) -> tuple[int, int]:
        with self._sessions() as session:
            row = session.get(ProviderUsageRow, (provider, day))
            return (row.requests, row.tokens) if row else (0, 0)

    def add(self, provider: str, day: date, requests: int, tokens: int) -> None:
        statement = insert(ProviderUsageRow).values(
            provider=provider, day=day, requests=requests, tokens=tokens
        )
        statement = statement.on_conflict_do_update(
            index_elements=[ProviderUsageRow.provider, ProviderUsageRow.day],
            set_={
                "requests": ProviderUsageRow.requests + statement.excluded.requests,
                "tokens": ProviderUsageRow.tokens + statement.excluded.tokens,
            },
        )
        with self._sessions.begin() as session:
            session.execute(statement)
