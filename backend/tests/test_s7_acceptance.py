import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from productfoundry.cli import main
from productfoundry.core.acceptance import AcceptanceCriteria
from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.roadmap import Roadmap, prd_order
from productfoundry.llm import LlmFailed, ProviderResponse
from productfoundry.orchestrator import PIPELINE, InMemoryRunStore, Orchestrator, RunStatus
from productfoundry.orchestrator.protocols import Services
from productfoundry.stages.fakes import FAKE_STAGES
from productfoundry.stages.s5_tasks.export import export_tasks, render_tasks_markdown
from productfoundry.stages.s6_roadmap import roadmap_stage
from productfoundry.stages.s7_acceptance import acceptance_stage, criteria_problems, epic_schema
from test_s3_pain_points import gateway
from test_s5_tasks import PRD, break_down

CRITERIA = json.loads(
    (Path(__file__).parent / "fixtures/llm/acceptance_criteria.json").read_text("utf-8")
)["criteria"]
S4, S5, S6, S7 = "s4_prd", "s5_tasks", "s6_roadmap", "s7_acceptance"


class CriteriaWriter:
    """The hand-written criteria of whichever tasks the call was sent."""

    def __init__(self, change=None) -> None:
        self.payloads: list[dict] = []
        self._change = change

    def complete(self, request) -> ProviderResponse:
        payload = json.loads(request.messages[1]["content"])
        self.payloads.append(payload)
        tasks = [
            {"task_id": task["id"], "criteria": copy.deepcopy(CRITERIA[task["id"]])}
            for task in payload["tasks"]
        ]
        if self._change is not None:
            self._change(tasks)
        return ProviderResponse(json.dumps({"tasks": tasks}))


@pytest.fixture
def plan(run_input):
    return break_down(run_input)[0]


def write_criteria(run_input, plan, provider=None, **providers):
    provider = provider or CriteriaWriter()
    llm = gateway(gemini=provider, **providers)
    criteria = acceptance_stage(run_input, {S4: PRD, S5: plan}, Services(llm=llm))
    return criteria, provider


# The stage


def test_every_task_gets_given_when_then_criteria_in_plan_order(run_input, plan):
    criteria, _ = write_criteria(run_input, plan)
    assert [task.task_id for task in criteria.tasks] == [task.id for task in plan.tasks]
    for task in criteria.tasks:
        kinds = {criterion.kind for criterion in task.criteria}
        assert "happy_path" in kinds and kinds & {"edge_case", "failure_state"}
        assert all(c.given and c.when and c.then for c in task.criteria)
    third = criteria.tasks[2]
    assert [c.kind for c in third.criteria] == ["happy_path", "edge_case", "failure_state"]
    assert third.criteria[0].then == "the group shows the payment as completed"
    assert criteria_problems(criteria, plan) == []


def test_the_calls_are_batched_by_epic(run_input, plan):
    _, provider = write_criteria(run_input, plan)
    assert len(provider.payloads) == len(plan.epics) == 2
    first, second = provider.payloads
    assert first["epic"]["title"] == "Reliable payments"
    assert [task["id"] for task in first["tasks"]] == [
        "task_001", "task_002", "task_003", "task_004",
    ]  # fmt: skip
    assert [task["id"] for task in second["tasks"]] == ["task_005", "task_006"]
    assert first["tasks"][0]["requirements"] == [r.statement for r in PRD.requirements[:2]]
    assert first["product"]["idea"] == run_input.idea


def no_happy_path(tasks):
    tasks[0]["criteria"] = [c | {"kind": "edge_case"} for c in tasks[0]["criteria"]]


def only_happy_paths(tasks):
    tasks[0]["criteria"] = [c | {"kind": "happy_path"} for c in tasks[0]["criteria"]]


def empty_part(tasks):
    tasks[1]["criteria"][0]["then"] = "   "


def skipped_task(tasks):
    del tasks[1]


def extra_task(tasks):
    tasks.append({"task_id": "task_099", "criteria": tasks[0]["criteria"]})


def one_criterion(tasks):
    del tasks[0]["criteria"][1:]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (no_happy_path, "needs at least one happy_path criterion"),
        (only_happy_paths, "needs at least one edge_case or failure_state criterion"),
        (empty_part, "at least 1 character"),
        (skipped_task, "expected criteria for exactly: task_"),
        (extra_task, "expected criteria for exactly: task_"),
        (one_criterion, "at least 2 items"),
    ],
)
def test_incomplete_criteria_are_rejected_and_asked_for_again(run_input, plan, change, message):
    bad = CriteriaWriter(change)
    with pytest.raises(LlmFailed, match="schema_invalid") as error:
        write_criteria(run_input, plan, bad)
    assert message in str(error.value)
    assert len(bad.payloads) == 2  # the first epic, twice; the stage does not go on without it

    good = CriteriaWriter()
    criteria, _ = write_criteria(run_input, plan, CriteriaWriter(change), groq=good)
    assert len(criteria.tasks) == 6 and len(good.payloads) >= 1


def test_the_epic_schema_accepts_complete_criteria():
    schema = epic_schema(["task_005", "task_006"])
    answer = {"tasks": [{"task_id": t, "criteria": CRITERIA[t]} for t in ("task_006", "task_005")]}
    assert len(schema.model_validate(answer).tasks) == 2


def test_the_stage_needs_the_gateway(run_input, plan):
    with pytest.raises(ProductFoundryError, match="need the LLM gateway"):
        acceptance_stage(run_input, {S4: PRD, S5: plan}, Services())


# The schema


def criteria_data(**overrides) -> dict:
    return {"tasks": [{"task_id": "task_001", "criteria": CRITERIA["task_001"]} | overrides]}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"criteria": CRITERIA["task_001"][:1]}, "at least 2 items"),
        ({"criteria": [CRITERIA["task_001"][1]] * 2}, "task_001 needs at least one happy_path"),
        ({"criteria": [CRITERIA["task_001"][0]] * 2}, "needs at least one edge_case or failure"),
        ({"criteria": [CRITERIA["task_001"][0] | {"when": ""}] * 2}, "at least 1 character"),
        ({"criteria": [CRITERIA["task_001"][0] | {"kind": "sad_path"}] * 2}, "Input should be"),
        ({"task_id": "the first task"}, "pattern"),
    ],
)
def test_the_acceptance_schema_rejects(overrides, message):
    assert AcceptanceCriteria.model_validate(criteria_data())
    with pytest.raises(ValidationError, match=message):
        AcceptanceCriteria.model_validate(criteria_data(**overrides))
    twice = criteria_data()["tasks"] * 2
    with pytest.raises(ValidationError, match="each task appears once"):
        AcceptanceCriteria.model_validate({"tasks": twice})


def test_criteria_are_checked_against_the_plan(run_input, plan):
    criteria, _ = write_criteria(run_input, plan)
    fewer = criteria.model_copy(update={"tasks": criteria.tasks[:5]})
    assert criteria_problems(fewer, plan) == ["task_006 has no acceptance criteria"]
    stray = criteria.tasks[0].model_copy(update={"task_id": "task_099"})
    more = criteria.model_copy(update={"tasks": [*criteria.tasks, stray]})
    assert criteria_problems(more, plan) == ["task_099 is not a task of the plan"]


# Stage 6, the pass-through


def test_the_pass_through_keeps_prd_order_and_lists_each_requirement_s_tasks(run_input, plan):
    roadmap = roadmap_stage(run_input, {S4: PRD, S5: plan}, Services())
    assert roadmap == prd_order(PRD, plan) and roadmap.ordering == "prd_order"
    assert [(item.rank, item.requirement_id) for item in roadmap.items] == [
        (1, "req_001"), (2, "req_002"), (3, "req_003"), (4, "req_004"),
    ]  # fmt: skip
    assert roadmap.items[0].task_ids == ["task_001", "task_002", "task_003", "task_006"]
    assert roadmap.items[3].task_ids == ["task_006"]

    data = roadmap.model_dump(mode="json")
    data["items"][0]["rank"] = 2
    with pytest.raises(ValidationError, match="ordered by rank"):
        Roadmap.model_validate(data)


def test_the_pass_through_has_no_checkpoint_and_the_pipeline_runs_to_stage_7(run_input):
    spec = next(spec for spec in PIPELINE if spec.key == S6)
    assert spec.number == 6 and spec.pass_through and not spec.checkpoint
    assert [spec.key for spec in PIPELINE if spec.checkpoint] == [
        "s1_competitors",
        "s3_pain_points",
    ]
    assert [spec.number for spec in PIPELINE] == [1, 2, 3, 4, 5, 6, 7]

    orchestrator = Orchestrator(InMemoryRunStore(), FAKE_STAGES)
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    orchestrator.approve(run.id)
    orchestrator.approve(orchestrator.resume(run.id).id)
    run = orchestrator.resume(run.id)  # stages 4 to 7 run without another stop
    assert run.status is RunStatus.COMPLETED
    assert [record.status for record in run.stages] == ["completed"] * 7
    assert Roadmap.model_validate(run.stage(S6).output).items
    assert AcceptanceCriteria.model_validate(run.stage(S7).output).tasks


# Export


def test_criteria_are_added_to_the_task_json_and_markdown(run_input, plan):
    criteria, _ = write_criteria(run_input, plan)
    export = json.loads(json.dumps(export_tasks(plan, PRD, title="x", criteria=criteria)))
    tasks = {task["id"]: task for epic in export["epics"] for task in epic["tasks"]}
    assert tasks["task_003"]["acceptance_criteria"] == CRITERIA["task_003"]
    assert all(len(task["acceptance_criteria"]) >= 2 for task in tasks.values())

    markdown = render_tasks_markdown(export)
    assert markdown.count("Acceptance criteria:") == 6
    assert (
        "- (failure) Given a payment with no answer from the provider for 10 minutes, "
        "when the user opens the group, then the payment shows as pending with the time it "
        "was started"
    ) in markdown
    assert "- (happy path) Given an empty database, when" in markdown
    assert "- (edge case) Given" in markdown

    without = render_tasks_markdown(export_tasks(plan, PRD, title="x"))
    assert "Acceptance criteria" not in without


def test_the_cli_task_export_carries_the_criteria(run_input, tmp_path, capsys):
    input_file = tmp_path / "input.json"
    input_file.write_text(run_input.model_dump_json(), encoding="utf-8")
    base = ["--memory", "--fake-stages", "--state-file", str(tmp_path / "runs.json")]
    assert main([*base, "run", "--input", str(input_file)]) == 0
    run_id = capsys.readouterr().out.split()[1]
    assert main([*base, "approve", run_id]) == 0
    assert main([*base, "approve", run_id]) == 0
    out = capsys.readouterr().out
    assert "status  completed" in out and "s7_acceptance    completed" in out

    assert main([*base, "tasks", run_id]) == 0
    assert "- (happy path) Given a signed-in user, when they use it, then it works" in (
        capsys.readouterr().out
    )
    assert main([*base, "tasks", run_id, "--format", "json"]) == 0
    export = json.loads(capsys.readouterr().out)
    assert export["epics"][0]["tasks"][0]["acceptance_criteria"][1]["kind"] == "failure_state"
