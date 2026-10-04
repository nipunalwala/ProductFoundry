"""Stage 7: every task gets Given/When/Then acceptance criteria, one LLM call per epic."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from productfoundry.core.acceptance import (
    AcceptanceCriteria,
    Criterion,
    CriterionKind,
    TaskCriteria,
    kinds_problem,
)
from productfoundry.core.base import NonEmptyStr
from productfoundry.core.errors import ProductFoundryError, StageOutputInvalid
from productfoundry.core.prd import Prd
from productfoundry.core.run_input import RunInput
from productfoundry.core.tasks import Epic, TaskPlan
from productfoundry.llm.types import Completer
from productfoundry.orchestrator.protocols import Services

TASK = "acceptance_criteria"
PROMPT = (Path(__file__).parent / "prompts" / "acceptance_criteria_v1.md").read_text(
    encoding="utf-8"
)


class CriterionDraft(BaseModel):
    kind: CriterionKind
    given: NonEmptyStr
    when: NonEmptyStr
    then: NonEmptyStr


class TaskCriteriaDraft(BaseModel):
    task_id: str
    criteria: list[CriterionDraft] = Field(min_length=2)


class EpicCriteria(BaseModel):
    tasks: list[TaskCriteriaDraft]


def epic_schema(task_ids: Sequence[str]) -> type[EpicCriteria]:
    """`EpicCriteria` for exactly these tasks, each with a happy path and an edge or failure.

    An answer that skips a task, adds one or leaves a task without both kinds
    fails validation, so the gateway asks again instead of the stage losing a task.
    """
    wanted = list(task_ids)

    class CompleteEpicCriteria(EpicCriteria):
        @model_validator(mode="after")
        def _complete(self) -> "CompleteEpicCriteria":
            if sorted(task.task_id for task in self.tasks) != sorted(wanted):
                raise ValueError(f"expected criteria for exactly: {', '.join(wanted)}")
            for task in self.tasks:
                problem = kinds_problem([criterion.kind for criterion in task.criteria])
                if problem:
                    raise ValueError(f"{task.task_id} {problem}")
            return self

    CompleteEpicCriteria.__name__ = EpicCriteria.__name__
    return CompleteEpicCriteria


def criteria_for_epic(llm: Completer, epic: Epic, prd: Prd, run_input: RunInput) -> EpicCriteria:
    statements = {requirement.id: requirement.statement for requirement in prd.requirements}
    payload = {
        "product": {"idea": run_input.idea, "target_users": run_input.target_users},
        "epic": {"title": epic.title, "description": epic.description},
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "requirements": [statements[r] for r in task.requirement_ids if r in statements],
            }
            for task in epic.tasks
        ],
    }
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    return llm.complete(TASK, messages, epic_schema([task.id for task in epic.tasks]))


def criteria_problems(criteria: AcceptanceCriteria, plan: TaskPlan) -> list[str]:
    """Tasks of the plan with no criteria, and criteria for tasks the plan does not have."""
    planned = [task.id for task in plan.tasks]
    covered = {task.task_id for task in criteria.tasks}
    problems = [
        f"{task_id} has no acceptance criteria" for task_id in planned if task_id not in covered
    ]
    problems += [
        f"{task_id} is not a task of the plan" for task_id in sorted(covered - set(planned))
    ]
    return problems


def acceptance_stage(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> AcceptanceCriteria:
    if services.llm is None:
        raise ProductFoundryError("acceptance criteria need the LLM gateway")
    prd: Prd = earlier_outputs["s4_prd"]
    plan: TaskPlan = earlier_outputs["s5_tasks"]

    by_task: dict[str, TaskCriteria] = {}
    for epic in plan.epics:
        answer = criteria_for_epic(services.llm, epic, prd, run_input)
        for task in answer.tasks:
            by_task[task.task_id] = TaskCriteria(
                task_id=task.task_id,
                criteria=[Criterion(**criterion.model_dump()) for criterion in task.criteria],
            )
    # In plan order, whatever order the answers came in.
    criteria = AcceptanceCriteria(
        tasks=[by_task[task.id] for task in plan.tasks if task.id in by_task]
    )
    problems = criteria_problems(criteria, plan)
    if problems:
        raise StageOutputInvalid(
            "acceptance criteria do not match the plan: " + "; ".join(problems)
        )
    return criteria
