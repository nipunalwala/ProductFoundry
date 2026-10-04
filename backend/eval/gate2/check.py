"""Gate 2: do requirements cite real evidence, and are the tasks any good?

Run from backend/:

    py -m uv run python eval/gate2/check.py grounding SLUG RUN_ID
    py -m uv run python eval/gate2/check.py sample SLUG RUN_ID [--tasks 10]
    py -m uv run python eval/gate2/check.py summary

`grounding` walks every requirement of the run's PRD to the pain points or market
gaps it cites, every pain point to its saved clusters, and every cluster to its
stored reviews. It writes eval/gate2/results/SLUG.json with every broken link.
`sample` writes eval/gate2/ratings/SLUG.json: tasks with their acceptance criteria
and empty `rating` (1 to 5) and `testable` (true or false) fields for the owner.
`summary` prints the gate's numbers for every product.

No LLM call and no source request is made.
"""

import argparse
import json
import random
import sys
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from productfoundry.core.acceptance import AcceptanceCriteria
from productfoundry.core.clusters import Cluster
from productfoundry.core.competitors import CompetitorList
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.prd import Prd, market_gaps
from productfoundry.core.tasks import TaskPlan

HERE = Path(__file__).parent
RESULTS = HERE / "results"
RATINGS = HERE / "ratings"


def walk(
    prd: Prd,
    report: PainPointReport,
    competitors: CompetitorList,
    clusters: Sequence[Cluster],
    review_ids: Collection[str],
) -> dict[str, Any]:
    """Follow every citation of the PRD down to stored reviews and list what breaks.

    A requirement is grounded when at least one thing it cites exists in the run:
    the cluster of an approved pain point, or a market gap. A broken link is any
    step on the way down that does not resolve, whether or not the requirement
    has other evidence.
    """
    points = {point.cluster_id: point for point in report.pain_points}
    gaps = {gap.id for gap in market_gaps(competitors)}
    saved = {cluster.id: cluster for cluster in clusters}
    broken: list[dict[str, str]] = []

    def cluster_problems(cluster_id: str) -> list[str]:
        """What is wrong below an approved pain point: its clusters, reviews and quotes."""
        point = points[cluster_id]
        problems = []
        members: set[str] = set()
        for part in point.cluster_ids:
            if part not in saved:
                problems.append(f"cluster {part} is not saved for this run")
                continue
            members.update(saved[part].review_ids)
            missing = sorted(set(saved[part].review_ids).difference(review_ids))
            if missing:
                problems.append(f"cluster {part} holds {len(missing)} reviews that are not stored")
        for quote in point.quote_review_ids:
            if quote not in review_ids:
                problems.append(f"quote {quote} is not a stored review")
            elif quote not in members:
                problems.append(f"quote {quote} is not in the pain point's clusters")
        return problems

    checked: dict[str, list[str]] = {}
    grounded = 0
    kinds = {"cluster": 0, "market_gap": 0}
    for requirement in prd.requirements:
        exists = False
        for evidence in requirement.evidence:
            if evidence.startswith("gap_"):
                if evidence in gaps:
                    exists = True
                    kinds["market_gap"] += 1
                else:
                    broken.append(
                        {
                            "requirement": requirement.id,
                            "evidence": evidence,
                            "problem": "market gap is not a competitor fact of this run",
                        }
                    )
                continue
            if evidence not in points:
                broken.append(
                    {
                        "requirement": requirement.id,
                        "evidence": evidence,
                        "problem": "no approved pain point has this cluster",
                    }
                )
                continue
            exists = True
            kinds["cluster"] += 1
            if evidence not in checked:
                checked[evidence] = cluster_problems(evidence)
            broken.extend(
                {"requirement": requirement.id, "evidence": evidence, "problem": problem}
                for problem in checked[evidence]
            )
        grounded += exists
    cited = [points[cluster_id] for cluster_id in checked]
    return {
        "requirements": len(prd.requirements),
        "grounded": grounded,
        "grounding": round(grounded / len(prd.requirements), 4),
        "citations": kinds,
        "pain_points_cited": len(cited),
        "reviews_behind_cited_pain_points": sum(point.review_count for point in cited),
        "broken_links": broken,
    }


def rating_sample(
    plan: TaskPlan, criteria: AcceptanceCriteria, count: int, seed: int
) -> list[dict[str, Any]]:
    """`count` tasks chosen at random (all of them when there are fewer), in plan order,
    each with its criteria and the empty fields the owner fills."""
    by_task = {item.task_id: item.criteria for item in criteria.tasks}
    tasks = plan.tasks
    chosen = set(random.Random(seed).sample(range(len(tasks)), min(count, len(tasks))))
    return [
        {
            "task_id": task.id,
            "title": task.title,
            "description": task.description,
            "effort": str(task.effort),
            "depends_on": task.depends_on,
            "requirement_ids": task.requirement_ids,
            "rating": None,
            "criteria": [
                {
                    "kind": str(criterion.kind),
                    "given": criterion.given,
                    "when": criterion.when,
                    "then": criterion.then,
                    "testable": None,
                }
                for criterion in by_task.get(task.id, [])
            ],
        }
        for n, task in enumerate(tasks)
        if n in chosen
    ]


def rating_summary(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The owner's ratings so far. A rating outside 1 to 5 is an error, not a number."""
    ratings = [task["rating"] for task in tasks if task["rating"] is not None]
    wrong = [r for r in ratings if isinstance(r, bool) or not isinstance(r, int) or not 1 <= r <= 5]
    if wrong:
        raise ValueError(f"a rating is a whole number from 1 to 5; found {wrong}")
    marks = [criterion["testable"] for task in tasks for criterion in task["criteria"]]
    if any(mark not in (None, True, False) for mark in marks):
        raise ValueError("testable is true, false or null")
    return {
        "tasks": len(tasks),
        "rated": len(ratings),
        "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "criteria": len(marks),
        "criteria_marked": sum(mark is not None for mark in marks),
        "untestable": sum(mark is False for mark in marks),
    }


def _load(run_id: str, stages: Sequence[str]):
    """The run and the validated outputs of `stages`, plus its clusters and review ids."""
    from productfoundry import storage
    from productfoundry.stages.s3_pain_points import run_reviews

    models = {
        "s1_competitors": CompetitorList,
        "s3_pain_points": PainPointReport,
        "s4_prd": Prd,
        "s5_tasks": TaskPlan,
        "s7_acceptance": AcceptanceCriteria,
    }
    engine = storage.make_engine()
    try:
        storage.check_ready(engine)
        sessions = storage.make_sessions(engine)
        run = storage.PostgresRunStore(sessions).get(run_id)
        outputs = {}
        for key in stages:
            output = run.stage(key).effective_output
            if output is None:
                raise SystemExit(f"error: run {run_id} has no output for {key} yet")
            outputs[key] = models[key].model_validate(output)
        clusters = storage.PostgresClusterStore(sessions).for_run(run_id)
        reviews: dict[str, Any] = {}
        if "s1_competitors" in outputs:
            _, reviews = run_reviews(outputs["s1_competitors"], storage.ReviewRepository(sessions))
    finally:
        engine.dispose()
    return run, outputs, clusters, set(reviews)


def _write(folder: Path, slug: str, data: Any) -> Path:
    folder.mkdir(exist_ok=True)
    target = folder / f"{slug}.json"
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def _grounding(slug: str, run_id: str) -> int:
    run, outputs, clusters, review_ids = _load(
        run_id, ["s1_competitors", "s3_pain_points", "s4_prd"]
    )
    result = walk(
        outputs["s4_prd"],
        outputs["s3_pain_points"],
        outputs["s1_competitors"],
        clusters,
        review_ids,
    )
    name = run.input.incumbent.name if run.input.incumbent else slug
    print(_write(RESULTS, slug, {"product": name, "run_id": run_id} | result))
    print(f"  requirements grounded: {result['grounded']} of {result['requirements']}")
    print(f"  broken links: {len(result['broken_links'])}")
    for link in result["broken_links"]:
        print(f"    {link['requirement']} -> {link['evidence']}: {link['problem']}")
    return 1 if result["broken_links"] or result["grounded"] < result["requirements"] else 0


def _sample(slug: str, run_id: str, count: int) -> int:
    run, outputs, _, _ = _load(run_id, ["s5_tasks", "s7_acceptance"])
    target = RATINGS / f"{slug}.json"
    if target.exists():
        print(f"error: {target} exists; delete it to draw a new sample", file=sys.stderr)
        return 1
    tasks = rating_sample(outputs["s5_tasks"], outputs["s7_acceptance"], count, run.seed)
    print(_write(RATINGS, slug, {"run_id": run_id, "owner_notes": None, "tasks": tasks}))
    print(f"  {len(tasks)} tasks: set each `rating` (1 to 5) and each criterion's `testable`")
    return 0


def _summary() -> int:
    files = sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []
    if not files:
        print("no results yet: run `grounding` first")
        return 1
    passed = True
    for path in files:
        result = json.loads(path.read_text(encoding="utf-8"))
        print(f"{result['product']}  ({result['run_id']})")
        print(
            f"  requirements grounded: {result['grounded']} of {result['requirements']} "
            f"({result['citations']['cluster']} cluster citations, "
            f"{result['citations']['market_gap']} market gap citations)"
        )
        print(f"  broken links: {len(result['broken_links'])}")
        passed &= result["grounded"] == result["requirements"] and not result["broken_links"]
        ratings = RATINGS / path.name
        if not ratings.exists():
            print("  owner ratings: no sample yet (run `sample`)")
            continue
        try:
            row = rating_summary(json.loads(ratings.read_text(encoding="utf-8"))["tasks"])
        except ValueError as exc:
            print(f"error: {ratings}: {exc}", file=sys.stderr)
            return 1
        average = "not rated" if row["average_rating"] is None else row["average_rating"]
        print(f"  task rating: {average} ({row['rated']} of {row['tasks']} tasks rated)")
        print(
            f"  untestable criteria: {row['untestable']} "
            f"({row['criteria_marked']} of {row['criteria']} marked)"
        )
    print(f"{len(files)} of 3 products checked; grounding {'passes' if passed else 'FAILS'}")
    return 0 if passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    grounding = commands.add_parser("grounding", help="walk every requirement down to reviews")
    sample = commands.add_parser("sample", help="write tasks and criteria for the owner to rate")
    for command in (grounding, sample):
        command.add_argument("slug", help="a short name for the product, used as the file name")
        command.add_argument("run_id")
    sample.add_argument("--tasks", type=int, default=10)
    commands.add_parser("summary", help="print the gate's numbers for every product")
    args = parser.parse_args(argv)
    if args.command == "grounding":
        return _grounding(args.slug, args.run_id)
    return _sample(args.slug, args.run_id, args.tasks) if args.command == "sample" else _summary()


if __name__ == "__main__":
    raise SystemExit(main())
