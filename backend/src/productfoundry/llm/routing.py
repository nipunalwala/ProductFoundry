"""Loads `routing.toml`: which providers serve which task, in what order."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

ROUTING_FILE = Path(__file__).with_name("routing.toml")


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderSpec(_Config):
    model: str = Field(min_length=1)
    api_key_setting: str
    requests_per_minute: PositiveInt | None = None
    tokens_per_minute: PositiveInt | None = None
    requests_per_day: PositiveInt | None = None
    tokens_per_day: PositiveInt | None = None


class TaskGroup(_Config):
    tasks: list[str] = Field(min_length=1)
    chain: list[str] = Field(min_length=1)


class Routing(_Config):
    near_limit: float = Field(gt=0, le=1)
    retry_backoff_seconds: float = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    providers: dict[str, ProviderSpec]
    groups: dict[str, TaskGroup]

    @model_validator(mode="after")
    def _check(self) -> "Routing":
        seen: set[str] = set()
        for name, group in self.groups.items():
            unknown = [provider for provider in group.chain if provider not in self.providers]
            if unknown:
                raise ValueError(f"group {name} names unknown providers: {unknown}")
            if len(set(group.chain)) != len(group.chain):
                raise ValueError(f"group {name} lists a provider twice")
            repeated = seen.intersection(group.tasks)
            if repeated:
                raise ValueError(f"tasks in more than one group: {sorted(repeated)}")
            seen.update(group.tasks)
        return self

    def chain_for(self, task: str) -> list[str]:
        for group in self.groups.values():
            if task in group.tasks:
                return group.chain
        raise KeyError(f"unknown LLM task {task!r}; add it to a group in routing.toml")


def load_routing(path: Path = ROUTING_FILE) -> Routing:
    return Routing.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
