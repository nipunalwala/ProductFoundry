"""Stage 5: the PRD becomes epics and tasks, each task tied to the requirements it serves."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from productfoundry.core.base import NonEmptyStr
from productfoundry.core.errors import ProductFoundryError, StageOutputInvalid
from productfoundry.core.prd import Prd
from productfoundry.core.run_input import RunInput
from productfoundry.core.tasks import Effort, Epic, Task, TaskPlan, dependency_order
from productfoundry.orchestrator.protocols import Services

TASK = "task_breakdown"
PROMPT = (Path(__file__).parent.parent / "prompts" / "task_breakdown_v1.md").read_text(
    encoding="utf-8"
)


class TaskDraft(BaseModel):
    key: NonEmptyStr
    title: NonEmptyStr
    description: NonEmptyStr
    requirements: list[str] = Field(min_length=1)
    depends_on: list[str] = []
    effort: Effort


class EpicDraft(BaseModel):
    title: NonEmptyStr
    description: NonEmptyStr
    tasks: list[TaskDraft] = Field(min_length=1)


class PlanDraft(BaseModel):
    """What the LLM returns. Tasks name each other by `key`; the stage assigns the ids."""

    epics: list[EpicDraft] = Field(min_length=1)


def plan_schema(requirement_ids: Sequence[str]) -> type[PlanDraft]:
    """`PlanDraft` that must cover the PRD: no orphan task, no uncovered requirement,
    no dependency that is missing or circular. A draft that fails is asked for again.
    """
    known = list(requirement_ids)

    class CoveringPlanDraft(PlanDraft):
        @model_validator(mode="after")
        def _covering(self) -> "CoveringPlanDraft":
            tasks = [task for epic in self.epics for task in epic.tasks]
            keys = [task.key for task in tasks]
            if len(set(keys)) != len(keys):
                raise ValueError("task keys must be unique")
            for task in tasks:
                unknown = [r for r in task.requirements if r not in known]
                if unknown:
                    raise ValueError(f"{task.key} names requirements that do not exist: {unknown}")
            served = {r for task in tasks for r in task.requirements}
            uncovered = [r for r in known if r not in served]
            if uncovered:
                raise ValueError(f"no task serves: {', '.join(uncovered)}")
            dependency_order({task.key: task.depends_on for task in tasks})
            return self

    CoveringPlanDraft.__name__ = PlanDraft.__name__
    return CoveringPlanDraft


def coverage_problems(plan: TaskPlan, prd: Prd) -> list[str]:
    """Tasks that serve no requirement of this PRD, and requirements no task serves."""
    known = {requirement.id for requirement in prd.requirements}
    problems = []
    for task in plan.tasks:
        unknown = [r for r in task.requirement_ids if r not in known]
        if unknown:
            problems.append(f"{task.id} names requirements that are not in the PRD: {unknown}")
    served = {r for task in plan.tasks for r in task.requirement_ids}
    uncovered = [r.id for r in prd.requirements if r.id not in served]
    if uncovered:
        problems.append(f"no task serves: {', '.join(uncovered)}")
    return problems


def tasks_stage(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> TaskPlan:
    if services.llm is None:
        raise ProductFoundryError("task breakdown needs the LLM gateway")
    prd: Prd = earlier_outputs["s4_prd"]
    payload = {
        "product": run_input.model_dump(
            mode="json", include={"idea", "target_users", "platforms", "region"}
        ),
        "tech_stack": run_input.tech_stack,
        "prd": prd.model_dump(
            mode="json",
            include={"problem", "users", "goals", "non_goals"},
        )
        | {
            "requirements": [
                r.model_dump(mode="json", exclude={"evidence"}) for r in prd.requirements
            ]
        },
    }
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    draft = services.llm.complete(TASK, messages, plan_schema([r.id for r in prd.requirements]))

    drafts = [task for epic in draft.epics for task in epic.tasks]
    ids = {task.key: f"task_{number:03d}" for number, task in enumerate(drafts, start=1)}
    plan = TaskPlan(
        epics=[
            Epic(
                id=f"epic_{number:02d}",
                title=epic.title,
                description=epic.description,
                tasks=[
                    Task(
                        id=ids[task.key],
                        title=task.title,
                        description=task.description,
                        requirement_ids=list(dict.fromkeys(task.requirements)),
                        depends_on=[ids[key] for key in dict.fromkeys(task.depends_on)],
                        effort=task.effort,
                    )
                    for task in epic.tasks
                ],
            )
            for number, epic in enumerate(draft.epics, start=1)
        ]
    )
    problems = coverage_problems(plan, prd)
    if problems:
        raise StageOutputInvalid("the task plan does not cover the PRD: " + "; ".join(problems))
    return plan
