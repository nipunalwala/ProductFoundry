"""The task plan as JSON and as a Markdown view ordered by dependencies."""

from collections.abc import Mapping
from typing import Any

from productfoundry.core.prd import Prd
from productfoundry.core.tasks import TaskPlan


def export_tasks(plan: TaskPlan, prd: Prd, *, title: str) -> dict[str, Any]:
    """The plan as plain data, with the order to build in and each requirement's statement."""
    return {
        "title": title,
        **plan.model_dump(mode="json"),
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
    return "\n".join(lines) + "\n"
