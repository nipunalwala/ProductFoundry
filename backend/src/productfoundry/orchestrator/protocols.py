from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from pydantic import BaseModel

from productfoundry.core.reviews import ReviewStore
from productfoundry.core.run_input import RunInput
from productfoundry.llm.types import Completer
from productfoundry.orchestrator.state import RunRecord
from productfoundry.sources import AppLookup, ReviewSource
from productfoundry.sources.search import SearchProvider


@dataclass(frozen=True)
class Services:
    """What a stage may use besides its inputs: adapters, never run state."""

    seed: int = 0
    llm: Completer | None = None
    search: SearchProvider | None = None
    app_lookups: Mapping[str, AppLookup] = field(default_factory=dict)  # by store name
    review_sources: Mapping[str, ReviewSource] = field(default_factory=dict)  # by store name
    reviews: ReviewStore | None = None

    def for_run(self, run_id: str, seed: int) -> "Services":
        """The services one run's stages get: its seed, and LLM calls recorded against it."""
        llm = self.llm.for_run(run_id) if hasattr(self.llm, "for_run") else self.llm
        return replace(self, seed=seed, llm=llm)


class Stage(Protocol):
    """A stage is a function of its inputs. It never sees the run record or the store."""

    def __call__(
        self,
        run_input: RunInput,
        earlier_outputs: Mapping[str, BaseModel],
        services: Services,
    ) -> BaseModel | Mapping[str, Any]: ...


class RunStore(Protocol):
    """Persistence for run records. `get` and `list_runs` return copies."""

    def create(self, run: RunRecord) -> None: ...

    def get(self, run_id: str) -> RunRecord: ...

    def save(self, run: RunRecord) -> None: ...

    def list_runs(self) -> list[RunRecord]: ...
