"""The task plan as JSON and as a Markdown view ordered by dependencies."""

from collections.abc import Mapping
from typing import Any

from productfoundry.core.acceptance import AcceptanceCriteria
from productfoundry.core.prd import Prd
from productfoundry.core.tasks import TaskPlan

KIND_NAMES = {"happy_path": "happy path", "edge_case": "edge case", "failure_state": "failure"}


def export_tasks(
    plan: TaskPlan, prd: Prd, *, title: str, criteria: AcceptanceCriteria | None = None
) -> dict[str, Any]:
    """The plan as plain data, with the order to build in and each requirement's statement.

    When the run has acceptance criteria, each task carries its own.
    """
    data = plan.model_dump(mode="json")
    if criteria is not None:
        by_task = {
            task.task_id: task.model_dump(mode="json")["criteria"] for task in criteria.tasks
        }
        for epic in data["epics"]:
            for task in epic["tasks"]:
                task["acceptance_criteria"] = by_task.get(task["id"], [])
    return {
        "title": title,
        **data,
        "build_order": [task.id for task in plan.in_dependency_order()],
        "requirements": {r.id: r.statement for r in prd.requirements},
    }


def render_tasks_markdown(export: Mapping[str, Any]) -> str:
    """Epics first, then every task in an order that respects its dependencies."""
    tasks = {
        task["id"]: task | {"epic": epic} for epic in export["epics"] for task in epic["tasks"]
    }
    lines = [f"# Tasks: {export['title']}", "", "## Epics"]
    for epic in export["epics"]:
        ids = ", ".join(f"`{task['id']}`" for task in epic["tasks"])
        lines += ["", f"- **{epic['title']}** (`{epic['id']}`): {epic['description']} Tasks: {ids}"]

    lines += ["", "## Tasks, in build order"]
    for number, task_id in enumerate(export["build_order"], start=1):
        task = tasks[task_id]
        after = ", ".join(f"`{other}`" for other in task["depends_on"]) or "nothing"
        lines += [
            "",
            f"### {number}. {task['title']} (`{task_id}`)",
            "",
            f"Epic: {task['epic']['title']} · Effort: {task['effort']} · After: {after}",
            "",
            task["description"],
            "",
            "Serves:",
            *(
                f"- `{requirement}`: {export['requirements'].get(requirement, 'not in the PRD')}"
                for requirement in task["requirement_ids"]
            ),
        ]
        if task.get("acceptance_criteria"):
            lines += ["", "Acceptance criteria:"]
            lines += [
                f"- ({KIND_NAMES[criterion['kind']]}) Given {criterion['given']}, "
                f"when {criterion['when']}, then {criterion['then']}"
                for criterion in task["acceptance_criteria"]
            ]
    return "\n".join(lines) + "\n"
