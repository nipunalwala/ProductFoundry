from pydantic import TypeAdapter

from productfoundry.core.errors import RunAlreadyExists, RunNotFound
from productfoundry.orchestrator.state import RunRecord

_RUNS = TypeAdapter(list[RunRecord])


class InMemoryRunStore:
    """A `RunStore` held in a dict. It can be dumped to JSON so the CLI survives a restart."""

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def create(self, run: RunRecord) -> None:
        if run.id in self._runs:
            raise RunAlreadyExists(f"run {run.id} already exists")
        self._runs[run.id] = run.model_copy(deep=True)

    def get(self, run_id: str) -> RunRecord:
        try:
            return self._runs[run_id].model_copy(deep=True)
        except KeyError:
            raise RunNotFound(f"run {run_id} not found") from None

    def save(self, run: RunRecord) -> None:
        if run.id not in self._runs:
            raise RunNotFound(f"run {run.id} not found")
        self._runs[run.id] = run.model_copy(deep=True)

    def list_runs(self) -> list[RunRecord]:
        runs = sorted(self._runs.values(), key=lambda run: (run.created_at, run.id))
        return [run.model_copy(deep=True) for run in runs]

    def dump_json(self) -> str:
        return _RUNS.dump_json(self.list_runs(), indent=2).decode()

    @classmethod
    def load_json(cls, text: str) -> "InMemoryRunStore":
        store = cls()
        for run in _RUNS.validate_json(text):
            store._runs[run.id] = run
        return store
