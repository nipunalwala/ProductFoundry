from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from productfoundry.core.run_input import RunInput
from productfoundry.orchestrator.state import RunRecord


@dataclass(frozen=True)
class Services:
    """What a stage may use besides its inputs. Later phases add the gateway and sources."""

    seed: int = 0


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
