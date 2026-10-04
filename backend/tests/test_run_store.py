"""Contract tests every `RunStore` implementation must pass."""

from datetime import UTC, datetime, timedelta

import pytest

from productfoundry.core.errors import RunAlreadyExists, RunNotFound
from productfoundry.orchestrator import (
    InMemoryRunStore,
    Orchestrator,
    RunRecord,
    RunStatus,
    StageRecord,
)
from productfoundry.stages.fakes import FAKE_STAGES

T0 = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    if request.param == "memory":
        return InMemoryRunStore()
    from productfoundry.storage import PostgresRunStore

    return PostgresRunStore(request.getfixturevalue("sessions"))


def make_run(run_input, run_id="run_a", created_at=T0) -> RunRecord:
    return RunRecord(
        id=run_id,
        input=run_input,
        seed=7,
        stages=[StageRecord(key="s1_competitors"), StageRecord(key="s2_reviews")],
        created_at=created_at,
        updated_at=created_at,
    )


def test_a_created_run_is_returned_unchanged(store, run_input):
    run = make_run(run_input)
    store.create(run)
    assert store.get("run_a") == run


def test_creating_the_same_run_twice_is_an_error(store, run_input):
    store.create(make_run(run_input))
    with pytest.raises(RunAlreadyExists):
        store.create(make_run(run_input))


def test_missing_runs_raise(store, run_input):
    with pytest.raises(RunNotFound):
        store.get("run_missing")
    with pytest.raises(RunNotFound):
        store.save(make_run(run_input, run_id="run_missing"))


def test_save_replaces_the_stored_run(store, run_input):
    run = make_run(run_input)
    store.create(run)
    run.status = RunStatus.AWAITING_APPROVAL
    run.stages[0].output = {"competitors": []}
    run.stages[0].edited_output = {"competitors": ["edited"]}
    run.updated_at = T0 + timedelta(minutes=5)
    store.save(run)
    assert store.get("run_a") == run


def test_changes_reach_the_store_only_through_save(store, run_input):
    run = make_run(run_input)
    store.create(run)
    run.status = RunStatus.FAILED
    fetched = store.get("run_a")
    fetched.stages[0].error = "changed on a copy"
    stored = store.get("run_a")
    assert stored.status is RunStatus.PENDING
    assert stored.stages[0].error is None


def test_runs_are_listed_oldest_first(store, run_input):
    store.create(make_run(run_input, "run_b", T0 + timedelta(hours=1)))
    store.create(make_run(run_input, "run_a", T0))
    assert [run.id for run in store.list_runs()] == ["run_a", "run_b"]


def test_times_stay_timezone_aware(store, run_input):
    store.create(make_run(run_input))
    assert store.get("run_a").created_at == T0
    assert store.get("run_a").created_at.utcoffset() == timedelta(0)


def test_in_memory_store_round_trips_through_json(run_input):
    store = InMemoryRunStore()
    store.create(make_run(run_input))
    restored = InMemoryRunStore.load_json(store.dump_json())
    assert restored.list_runs() == store.list_runs()


def test_a_full_run_works_on_any_store(store, run_input):
    orchestrator = Orchestrator(store, FAKE_STAGES)
    run = orchestrator.resume(orchestrator.create_run(run_input, seed=3).id)
    edited = dict(run.stages[0].output, competitors=run.stages[0].output["competitors"][:1])
    orchestrator.approve(run.id, edited)
    orchestrator.resume(run.id)
    orchestrator.approve(run.id)
    run = orchestrator.resume(run.id)

    stored = store.get(run.id)
    assert stored == run
    assert stored.status is RunStatus.COMPLETED
    assert stored.seed == 3
    assert len(stored.stages[0].output["competitors"]) == 2
    assert len(stored.stages[0].edited_output["competitors"]) == 1

    stored = Orchestrator(store, FAKE_STAGES).resume(run.id, from_stage="s2_reviews")
    assert store.get(run.id).stages[2].approved_at is None
    assert [stage.key for stage in store.get(run.id).stages] == [s.key for s in run.stages]
