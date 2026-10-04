import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from productfoundry.cli import main
from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.prd import Prd
from productfoundry.core.tasks import TaskPlan, dependency_order
from productfoundry.llm import LlmFailed, ProviderResponse
from productfoundry.llm.fakes import FakeProvider
from productfoundry.orchestrator import InMemoryRunStore, Orchestrator, RunStatus
from productfoundry.orchestrator.protocols import Services
from productfoundry.stages.fakes import FAKE_STAGES
from productfoundry.stages.s5_tasks import coverage_problems, plan_schema, tasks_stage
from productfoundry.stages.s5_tasks.export import export_tasks, render_tasks_markdown
from test_s3_pain_points import gateway

FIXTURES = Path(__file__).parent / "fixtures/llm"
ANSWER = json.loads((FIXTURES / "task_breakdown.json").read_text("utf-8"))["answer"]
PRD_ANSWER = json.loads((FIXTURES / "prd.json").read_text("utf-8"))["answer"]
S4, S5 = "s4_prd", "s5_tasks"
REQUIREMENTS = ["req_001", "req_002", "req_003", "req_004"]
PRD = Prd.model_validate(
    PRD_ANSWER
    | {
        "requirements": [
            requirement | {"id": requirement_id, "evidence": ["cl_a"]}
            for requirement_id, requirement in zip(
                REQUIREMENTS, PRD_ANSWER["requirements"], strict=True
            )
        ]
    }
)


def answer(change=None) -> dict:
    """The hand-written plan; `change` gets its tasks by key to tamper with."""
    data = copy.deepcopy(ANSWER)
    if change is not None:
        change({task["key"]: task for epic in data["epics"] for task in epic["tasks"]}, data)
    return data


def break_down(run_input, *answers, **providers) -> tuple[TaskPlan, FakeProvider]:
    provider = FakeProvider(*(answers or [answer()]))
    llm = gateway(gemini=provider, **providers)
    return tasks_stage(run_input, {S4: PRD}, Services(llm=llm)), provider


# The stage


def test_the_prd_becomes_epics_and_tasks_with_ids_requirements_and_dependencies(run_input):
    plan, provider = break_down(run_input)
    assert [(epic.id, epic.title, len(epic.tasks)) for epic in plan.epics] == [
        ("epic_01", "Reliable payments", 4),
        ("epic_02", "Getting in and settling up", 2),
    ]
    tasks = {task.id: task for task in plan.tasks}
    assert list(tasks) == [f"task_00{n}" for n in range(1, 7)]
    assert tasks["task_001"].depends_on == [] and tasks["task_001"].effort == "M"
    assert tasks["task_002"].title == "Refund failed payments automatically"
    assert tasks["task_002"].depends_on == ["task_003"]  # keys become ids
    assert tasks["task_006"].depends_on == ["task_003", "task_005"]
    assert tasks["task_006"].requirement_ids == ["req_004", "req_001"]
    assert coverage_problems(plan, PRD) == []
    assert len(provider.requests) == 1


def test_the_prompt_receives_the_prd_and_the_tech_stack(run_input):
    stack = run_input.model_copy(update={"tech_stack": ["Flutter", "FastAPI"]})
    _, provider = break_down(stack)
    payload = json.loads(provider.requests[0].messages[1]["content"])
    assert payload["tech_stack"] == ["Flutter", "FastAPI"]
    assert payload["product"]["region"] == "IN" and payload["product"]["idea"] == run_input.idea
    assert [r["id"] for r in payload["prd"]["requirements"]] == REQUIREMENTS
    assert payload["prd"]["requirements"][0]["priority"] == "must"
    assert "evidence" not in payload["prd"]["requirements"][0]
    assert payload["prd"]["non_goals"] == PRD.non_goals
    assert "tech_stack" in provider.requests[0].messages[0]["content"]

    _, provider = break_down(run_input)
    assert json.loads(provider.requests[0].messages[1]["content"])["tech_stack"] == []


def cycle(tasks, _):
    tasks["T1"]["depends_on"] = ["T6"]


def orphan(tasks, _):
    tasks["T5"]["requirements"] = []


def stray(tasks, _):
    tasks["T5"]["requirements"] = ["req_009"]


def uncovered(tasks, _):
    tasks["T6"]["requirements"] = ["req_001"]


def missing_dependency(tasks, _):
    tasks["T3"]["depends_on"] = ["T9"]


def on_itself(tasks, _):
    tasks["T3"]["depends_on"] = ["T3"]


def repeated_key(tasks, _):
    tasks["T3"]["key"] = "T1"


def too_big(tasks, _):
    tasks["T3"]["effort"] = "XXL"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (cycle, "dependencies form a cycle among: T1, T4, T2, T5, T3, T6"),
        (orphan, "at least 1 item"),
        (stray, "T5 names requirements that do not exist"),
        (uncovered, "no task serves: req_004"),
        (missing_dependency, "T3 depends on tasks that do not exist: T9"),
        (on_itself, "T3 depends on itself"),
        (repeated_key, "task keys must be unique"),
        (too_big, "'XS', 'S', 'M', 'L' or 'XL'"),
    ],
)
def test_a_cycle_an_orphan_task_and_an_uncovered_requirement_are_rejected(
    run_input, change, message
):
    with pytest.raises(ValidationError, match=message):
        plan_schema(REQUIREMENTS).model_validate(answer(change))
    # In the stage the bad answer is asked for again, then the run fails rather than keep it.
    with pytest.raises(LlmFailed, match="schema_invalid"):
        break_down(run_input, answer(change))


def test_a_rejected_plan_falls_back_to_the_next_provider(run_input):
    good = FakeProvider(answer())
    plan, bad = break_down(run_input, answer(cycle), openrouter=good)
    assert len(bad.requests) == 2 and len(good.requests) == 1
    assert len(plan.tasks) == 6


def test_the_stage_needs_the_gateway(run_input):
    with pytest.raises(ProductFoundryError, match="needs the LLM gateway"):
        tasks_stage(run_input, {S4: PRD}, Services())


# The schema


def test_dependency_order_keeps_the_given_order_where_dependencies_allow():
    assert dependency_order({"a": [], "b": ["c"], "c": ["a"], "d": []}) == ["a", "c", "b", "d"]
    assert dependency_order({}) == []
    with pytest.raises(ValueError, match="cycle among: b, c"):
        dependency_order({"a": [], "b": ["c"], "c": ["b"]})


def test_the_task_plan_schema_rejects_what_the_stage_would_never_build(run_input):
    plan, _ = break_down(run_input)
    data = plan.model_dump(mode="json")

    def changed(path, value) -> dict:
        copied = copy.deepcopy(data)
        task = copied["epics"][path[0]]["tasks"][path[1]]
        task[path[2]] = value
        return copied

    for path, value, message in [
        ((0, 0, "depends_on"), ["task_006"], "dependencies form a cycle"),
        ((0, 0, "depends_on"), ["task_099"], "depends on tasks that do not exist"),
        ((0, 0, "requirement_ids"), [], "at least 1 item"),
        ((0, 0, "requirement_ids"), ["the payment one"], "pattern"),
        ((0, 1, "id"), "task_001", "task ids must be unique"),
        ((0, 0, "effort"), "3 days", "'XS', 'S', 'M', 'L' or 'XL'"),
    ]:
        with pytest.raises(ValidationError, match=message):
            TaskPlan.model_validate(changed(path, value))
    assert TaskPlan.model_validate(data) == plan


def test_coverage_is_checked_against_the_prd_of_the_run(run_input):
    plan, _ = break_down(run_input)
    smaller = PRD.model_copy(update={"requirements": PRD.requirements[:3]})
    assert (
        "task_006 names requirements that are not in the PRD" in coverage_problems(plan, smaller)[0]
    )
    extra = PRD.requirements[0].model_copy(update={"id": "req_005"})
    larger = PRD.model_copy(update={"requirements": [*PRD.requirements, extra]})
    assert coverage_problems(plan, larger) == ["no task serves: req_005"]


# Export


def test_the_markdown_view_is_ordered_by_dependencies(run_input):
    plan, _ = break_down(run_input)
    export = export_tasks(plan, PRD, title=run_input.idea)
    assert export["build_order"] == [
        "task_001", "task_003", "task_002", "task_004", "task_005", "task_006",
    ]  # fmt: skip
    markdown = render_tasks_markdown(export)

    assert markdown.startswith("# Tasks: A simpler UPI expense tracker\n")
    assert "- **Reliable payments** (`epic_01`): A payment always ends" in markdown
    headings = [line for line in markdown.splitlines() if line.startswith("### ")]
    assert headings[1] == "### 2. Track the state of every payment (`task_003`)"
    assert headings[2] == "### 3. Refund failed payments automatically (`task_002`)"
    # No task is listed before a task it depends on.
    position = {task_id: markdown.index(f"(`{task_id}`)\n") for task_id in export["build_order"]}
    for task in plan.tasks:
        assert all(position[other] < position[task.id] for other in task.depends_on)
    assert "Epic: Reliable payments · Effort: L · After: `task_003`" in markdown
    assert "Epic: Reliable payments · Effort: M · After: nothing" in markdown
    assert f"- `req_004`: {PRD.requirements[3].statement}" in markdown


def test_the_json_export_is_the_plan_with_its_build_order(run_input):
    plan, _ = break_down(run_input)
    export = json.loads(json.dumps(export_tasks(plan, PRD, title="x")))
    assert TaskPlan.model_validate({k: export[k] for k in ("schema_version", "epics")}) == plan
    assert sorted(export["build_order"]) == [task.id for task in plan.tasks]
    assert export["requirements"]["req_001"] == PRD.requirements[0].statement


# In a run


class Planner:
    """A task per requirement of whatever PRD it is sent, each after the one before."""

    def complete(self, request) -> ProviderResponse:
        requirements = json.loads(request.messages[1]["content"])["prd"]["requirements"]
        tasks = [
            {
                "key": f"T{n}",
                "title": f"Build {requirement['id']}",
                "description": requirement["statement"],
                "requirements": [requirement["id"]],
                "depends_on": [f"T{n - 1}"] if n > 1 else [],
                "effort": "S",
            }
            for n, requirement in enumerate(requirements, start=1)
        ]
        epic = {"title": "First version", "description": "All of it.", "tasks": tasks}
        return ProviderResponse(json.dumps({"epics": [epic]}))


def test_the_run_ends_with_a_task_plan_and_the_cli_exports_it(run_input, tmp_path, capsys):
    orchestrator = Orchestrator(
        InMemoryRunStore(),
        FAKE_STAGES | {S5: tasks_stage},
        services=Services(llm=gateway(gemini=Planner())),
    )
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    orchestrator.approve(run.id)
    orchestrator.approve(orchestrator.resume(run.id).id)
    run = orchestrator.resume(run.id)
    assert run.status is RunStatus.COMPLETED
    plan = TaskPlan.model_validate(run.stage(S5).output)
    prd = Prd.model_validate(run.stage(S4).output)
    assert [t.requirement_ids for t in plan.tasks] == [[r.id] for r in prd.requirements]

    # The export needs no stored reviews, so it also works on a --memory run.
    input_file = tmp_path / "input.json"
    input_file.write_text(run_input.model_dump_json(), encoding="utf-8")
    base = ["--memory", "--fake-stages", "--state-file", str(tmp_path / "runs.json")]
    assert main([*base, "run", "--input", str(input_file)]) == 0
    run_id = capsys.readouterr().out.split()[1]
    assert main([*base, "tasks", run_id]) == 1
    assert "has no task plan yet" in capsys.readouterr().err
    assert main([*base, "approve", run_id]) == 0
    assert main([*base, "approve", run_id]) == 0
    assert "s5_tasks         completed" in capsys.readouterr().out
    assert main([*base, "tasks", run_id]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Tasks: A simpler UPI expense tracker\n")
    assert "### 2. Build req_002 (`task_002`)" in out and "After: `task_001`" in out
    assert main([*base, "tasks", run_id, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["build_order"] == ["task_001", "task_002"]
