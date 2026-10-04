import copy
import inspect

import pytest

from productfoundry.core.competitors import CompetitorList
from productfoundry.core.errors import InvalidTransition, QuotaExhausted, StageOutputInvalid
from productfoundry.core.pain_points import PainPointReport
from productfoundry.orchestrator import (
    CHECKPOINT_STAGES,
    PIPELINE,
    InMemoryRunStore,
    Orchestrator,
    RunStatus,
    Services,
    Stage,
    StageStatus,
)
from productfoundry.stages.fakes import FAKE_STAGES

S1, S2, S3 = "s1_competitors", "s2_reviews", "s3_pain_points"


class Recorder:
    """Wraps the fake stages, counting calls and keeping what each stage received."""

    def __init__(self, **replacements):
        self.calls: list[str] = []
        self.received: dict[str, dict] = {}
        self.stages = {key: self._wrap(key, replacements.get(key, FAKE_STAGES[key]))
                       for key in FAKE_STAGES}  # fmt: skip

    def _wrap(self, key, stage):
        def wrapped(run_input, earlier_outputs, services):
            self.calls.append(key)
            self.received[key] = dict(earlier_outputs)
            return stage(run_input, earlier_outputs, services)

        return wrapped


def build(**replacements):
    store = InMemoryRunStore()
    recorder = Recorder(**replacements)
    return Orchestrator(store, recorder.stages), store, recorder


def statuses(run):
    return {record.key: record.status for record in run.stages}


def edited_competitors(run) -> dict:
    edited = copy.deepcopy(run.stage(S1).output)
    edited["competitors"] = edited["competitors"][:1]
    return edited


# Checkpoints


def test_checkpoints_are_declared_after_stages_1_3_and_6():
    assert CHECKPOINT_STAGES == {1, 3, 6}
    assert [spec.key for spec in PIPELINE if spec.checkpoint] == [S1, S3]


def test_a_new_run_is_pending_with_its_seed(run_input):
    orchestrator, store, _ = build()
    run = orchestrator.create_run(run_input, seed=11)
    stored = store.get(run.id)
    assert stored.status is RunStatus.PENDING
    assert stored.seed == 11
    assert set(statuses(stored).values()) == {StageStatus.PENDING}


def test_the_run_stops_at_the_first_checkpoint(run_input):
    orchestrator, store, recorder = build()
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert statuses(run) == {
        S1: StageStatus.AWAITING_APPROVAL,
        S2: StageStatus.PENDING,
        S3: StageStatus.PENDING,
    }
    assert recorder.calls == [S1]
    assert run.stage(S1).schema_version == 1
    assert store.get(run.id) == run


def test_approving_continues_to_the_next_checkpoint_and_then_completes(run_input):
    orchestrator, _, recorder = build()
    run = orchestrator.resume(orchestrator.create_run(run_input).id)

    orchestrator.approve(run.id)
    run = orchestrator.resume(run.id)
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert statuses(run) == {
        S1: StageStatus.COMPLETED,
        S2: StageStatus.COMPLETED,
        S3: StageStatus.AWAITING_APPROVAL,
    }

    orchestrator.approve(run.id)
    run = orchestrator.resume(run.id)
    assert run.status is RunStatus.COMPLETED
    assert set(statuses(run).values()) == {StageStatus.COMPLETED}
    assert recorder.calls == [S1, S2, S3]
    assert run.stage(S1).approved_at is not None


def test_resuming_a_run_that_awaits_approval_is_refused(run_input):
    orchestrator, _, recorder = build()
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    with pytest.raises(InvalidTransition, match="approve it first"):
        orchestrator.resume(run.id)
    assert recorder.calls == [S1]


def test_approving_a_run_that_is_not_at_a_checkpoint_is_refused(run_input):
    orchestrator, _, _ = build()
    run = orchestrator.create_run(run_input)
    with pytest.raises(InvalidTransition, match="not awaiting approval"):
        orchestrator.approve(run.id)


# Edited outputs


def test_an_edited_output_is_what_later_stages_see_and_the_original_is_kept(run_input):
    orchestrator, _, recorder = build()
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    original = copy.deepcopy(run.stage(S1).output)
    assert len(original["competitors"]) == 2

    orchestrator.approve(run.id, edited_competitors(run))
    run = orchestrator.resume(run.id)

    seen = recorder.received[S2][S1]
    assert isinstance(seen, CompetitorList)
    assert [c.name for c in seen.competitors] == ["Walnut"]
    assert run.stage(S1).output == original
    assert len(run.stage(S1).edited_output["competitors"]) == 1
    assert len(run.stage(S2).output["products"]) == 1


def test_an_invalid_edit_is_rejected_and_changes_nothing(run_input):
    orchestrator, store, _ = build()
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    edited = edited_competitors(run)
    edited["competitors"][0]["url"] = "not a url"

    with pytest.raises(StageOutputInvalid, match="competitors.0.url"):
        orchestrator.approve(run.id, edited)

    assert store.get(run.id) == run
    assert store.get(run.id).stage(S1).edited_output is None


# Validation of stage outputs


def test_an_output_that_fails_its_schema_fails_the_stage_and_is_not_saved(run_input):
    orchestrator, store, recorder = build(s1_competitors=lambda *_: {"competitors": []})
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    assert run.status is RunStatus.FAILED
    assert run.stage(S1).status is StageStatus.FAILED
    assert run.stage(S1).output is None
    assert "output failed its schema" in run.stage(S1).error
    assert run.error.startswith(S1)
    assert store.get(run.id).stage(S1).output is None
    assert recorder.calls == [S1]


def test_a_model_built_without_validation_is_still_checked(run_input):
    unchecked = CompetitorList.model_construct(competitors=[], rejected=[])
    orchestrator, _, _ = build(s1_competitors=lambda *_: unchecked)
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    assert run.stage(S1).status is StageStatus.FAILED
    assert run.stage(S1).output is None


def test_an_output_of_the_wrong_type_fails_the_stage(run_input):
    orchestrator, _, _ = build(s1_competitors=lambda *_: "a string")
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    assert run.status is RunStatus.FAILED


# Errors and quota


def test_any_other_error_fails_the_stage_and_the_run_with_the_error_recorded(run_input):
    def broken(*_):
        raise RuntimeError("store returned HTML")

    orchestrator, _, _ = build(s2_reviews=broken)
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    orchestrator.approve(run.id)
    run = orchestrator.resume(run.id)

    assert run.status is RunStatus.FAILED
    assert run.stage(S2).status is StageStatus.FAILED
    assert run.stage(S2).error == "RuntimeError: store returned HTML"
    assert run.error == "s2_reviews: RuntimeError: store returned HTML"
    assert run.stage(S3).status is StageStatus.PENDING


def test_a_failed_run_retries_its_failed_stage_on_resume(run_input):
    attempts = []

    def flaky(run_input, earlier_outputs, services):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("timeout")
        return FAKE_STAGES[S2](run_input, earlier_outputs, services)

    orchestrator, _, recorder = build(s2_reviews=flaky)
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    orchestrator.approve(run.id)
    assert orchestrator.resume(run.id).status is RunStatus.FAILED

    run = orchestrator.resume(run.id)
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert run.error is None
    assert run.stage(S2).error is None
    assert recorder.calls == [S1, S2, S2, S3]


def test_quota_exhausted_pauses_the_run_and_resume_continues_it(run_input):
    quota = {"left": 0}

    def limited(run_input, earlier_outputs, services):
        if quota["left"] == 0:
            raise QuotaExhausted("all providers are out of quota until 00:00 UTC")
        return FAKE_STAGES[S2](run_input, earlier_outputs, services)

    orchestrator, store, _ = build(s2_reviews=limited)
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    orchestrator.approve(run.id)
    run = orchestrator.resume(run.id)

    assert run.status is RunStatus.PAUSED_QUOTA
    assert run.error is None
    assert "out of quota" in run.pause_reason
    assert run.stage(S2).status is StageStatus.PENDING
    assert run.stage(S2).error is None
    assert store.get(run.id).status is RunStatus.PAUSED_QUOTA

    quota["left"] = 1
    run = orchestrator.resume(run.id)
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert run.pause_reason is None
    assert run.stage(S2).status is StageStatus.COMPLETED


# Resume and invalidation


def complete(orchestrator, run_input):
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    orchestrator.approve(run.id, edited_competitors(run))
    run = orchestrator.resume(run.id)
    orchestrator.approve(run.id)
    return orchestrator.resume(run.id)


def test_a_run_resumes_from_what_a_new_orchestrator_finds_in_the_store(run_input):
    store = InMemoryRunStore()
    first = Orchestrator(store, FAKE_STAGES)
    run = first.resume(first.create_run(run_input).id)

    restored = InMemoryRunStore.load_json(store.dump_json())
    recorder = Recorder()
    second = Orchestrator(restored, recorder.stages)
    second.approve(run.id)
    run = second.resume(run.id)

    assert run.status is RunStatus.AWAITING_APPROVAL
    assert recorder.calls == [S2, S3]


def test_rerunning_a_stage_invalidates_the_stages_after_it(run_input):
    orchestrator, _, recorder = build()
    run = complete(orchestrator, run_input)
    assert run.status is RunStatus.COMPLETED
    recorder.calls.clear()

    run = orchestrator.resume(run.id, from_stage=S2)

    assert recorder.calls == [S2, S3]
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert run.stage(S1).status is StageStatus.COMPLETED
    assert run.stage(S1).edited_output is not None
    assert run.stage(S2).status is StageStatus.COMPLETED
    assert run.stage(S3).status is StageStatus.AWAITING_APPROVAL
    assert run.stage(S3).approved_at is None


def test_rerunning_the_first_stage_clears_everything_after_it(run_input):
    orchestrator, _, recorder = build()
    run = complete(orchestrator, run_input)

    run = orchestrator.resume(run.id, from_stage=S1)

    assert run.status is RunStatus.AWAITING_APPROVAL
    assert run.stage(S1).edited_output is None
    for key in (S2, S3):
        record = run.stage(key)
        assert record.status is StageStatus.PENDING
        assert record.output is None and record.schema_version is None


def test_a_stage_cannot_be_rerun_before_the_stages_it_depends_on(run_input):
    orchestrator, _, _ = build()
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    with pytest.raises(InvalidTransition, match="before s1_competitors"):
        orchestrator.resume(run.id, from_stage=S2)
    with pytest.raises(InvalidTransition, match="unknown stage"):
        orchestrator.resume(run.id, from_stage="s9_nothing")


def test_a_completed_run_is_not_resumed_without_naming_a_stage(run_input):
    orchestrator, _, _ = build()
    run = complete(orchestrator, run_input)
    with pytest.raises(InvalidTransition, match="completed"):
        orchestrator.resume(run.id)


# Stages cannot write run state


def test_the_stage_interface_gives_no_access_to_run_state():
    parameters = list(inspect.signature(Stage.__call__).parameters)
    assert parameters == ["self", "run_input", "earlier_outputs", "services"]
    assert set(Services.__dataclass_fields__) == {"seed", "llm", "search", "app_lookups"}


def test_a_stage_that_tampers_with_its_inputs_does_not_change_run_state(run_input):
    def tampering(run_input, earlier_outputs, services):
        run_input.platforms.clear()
        run_input.incumbent.urls.append("https://tampered.example")
        earlier_outputs[S1].competitors.clear()
        with pytest.raises(TypeError):
            earlier_outputs[S1] = None
        with pytest.raises(AttributeError):
            services.seed = 99
        return FAKE_STAGES[S2](run_input, {S1: CompetitorList.model_validate(expected[S1])}, None)

    expected = {}
    orchestrator, store, _ = build(s2_reviews=tampering)
    run = orchestrator.resume(orchestrator.create_run(run_input, seed=5).id)
    expected[S1] = copy.deepcopy(run.stage(S1).output)
    orchestrator.approve(run.id)
    run = orchestrator.resume(run.id)

    stored = store.get(run.id)
    assert stored.input == run_input
    assert stored.seed == 5
    assert stored.stage(S1).output == expected[S1]
    assert stored.stage(S1).edited_output is None
    assert stored.stage(S2).status is StageStatus.COMPLETED


def test_stages_receive_validated_models_and_the_run_seed(run_input):
    seen = {}

    def spying(run_input, earlier_outputs, services):
        seen["seed"] = services.seed
        seen["types"] = {key: type(value) for key, value in earlier_outputs.items()}
        return FAKE_STAGES[S3](run_input, earlier_outputs, services)

    orchestrator, _, _ = build(s3_pain_points=spying)
    run = orchestrator.resume(orchestrator.create_run(run_input, seed=42).id)
    orchestrator.approve(run.id)
    run = orchestrator.resume(run.id)

    assert seen["seed"] == 42
    assert list(seen["types"]) == [S1, S2]
    assert PainPointReport.model_validate(run.stage(S3).output).pain_points[0].rank == 1


def test_every_pipeline_stage_needs_an_implementation():
    with pytest.raises(ValueError, match="s3_pain_points"):
        Orchestrator(InMemoryRunStore(), {S1: FAKE_STAGES[S1], S2: FAKE_STAGES[S2]})
