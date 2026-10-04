import importlib.util
from pathlib import Path

import pytest

from productfoundry.core.acceptance import AcceptanceCriteria
from productfoundry.core.tasks import TaskPlan
from productfoundry.stages.s3_pain_points import run_reviews
from test_s3_pain_points import COMPETITORS, run_stage
from test_s4_prd import write_prd

GATE2 = Path(__file__).parent.parent / "eval" / "gate2"
spec = importlib.util.spec_from_file_location("gate2_check", GATE2 / "check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


@pytest.fixture(scope="module")
def run():
    """A PRD on the invented reviews, with the report, clusters and review ids behind it."""
    from conftest import run_input_data
    from productfoundry.core.run_input import RunInput

    report, used, _ = run_stage()
    prd, _ = write_prd(report, RunInput.model_validate(run_input_data()))
    _, reviews = run_reviews(COMPETITORS, used.reviews)
    return prd, report, used.clusters.for_run(used.run_id), set(reviews)


def test_every_requirement_walks_down_to_stored_reviews(run):
    prd, report, clusters, review_ids = run
    result = check.walk(prd, report, COMPETITORS, clusters, review_ids)

    assert (result["requirements"], result["grounded"], result["grounding"]) == (4, 4, 1.0)
    assert result["citations"] == {"cluster": 4, "market_gap": 1}
    assert result["pain_points_cited"] == 3 and result["broken_links"] == []
    assert result["reviews_behind_cited_pain_points"] == sum(
        point.review_count for point in report.pain_points
    )


def test_a_requirement_citing_only_what_is_not_in_the_run_is_not_grounded(run):
    prd, report, clusters, review_ids = run
    data = prd.model_dump(mode="json")
    data["requirements"][0]["evidence"] = ["cl_0000000000000000"]
    result = check.walk(type(prd).model_validate(data), report, COMPETITORS, clusters, review_ids)

    assert result["grounded"] == 3 and result["grounding"] == 0.75
    assert result["broken_links"] == [
        {
            "requirement": "req_001",
            "evidence": "cl_0000000000000000",
            "problem": "no approved pain point has this cluster",
        }
    ]


def test_a_cluster_whose_reviews_are_gone_is_a_broken_link_under_every_requirement_citing_it(run):
    prd, report, clusters, review_ids = run
    first = report.pain_points[0]
    gone = next(c for c in clusters if c.id == first.cluster_id).review_ids[0]
    quoted = first.quote_review_ids[0]
    result = check.walk(prd, report, COMPETITORS, clusters, review_ids - {gone, quoted})

    # Requirements 1 and 4 both cite the first pain point.
    assert {link["requirement"] for link in result["broken_links"]} == {"req_001", "req_004"}
    problems = {link["problem"] for link in result["broken_links"]}
    assert f"quote {quoted} is not a stored review" in problems
    assert any("reviews that are not stored" in problem for problem in problems)
    assert result["grounded"] == 4  # the citation exists; what is below it is broken


def test_a_cluster_that_was_never_saved_and_a_quote_from_elsewhere_are_broken_links(run):
    prd, report, clusters, review_ids = run
    first, second = report.pain_points[0], report.pain_points[1]
    kept = [cluster for cluster in clusters if cluster.id != second.cluster_id]
    moved = report.model_copy(deep=True)
    moved.pain_points[0].quote_review_ids[0] = second.quote_review_ids[0]
    result = check.walk(prd, moved, COMPETITORS, kept, review_ids)

    problems = {(link["evidence"], link["problem"]) for link in result["broken_links"]}
    assert (
        second.cluster_id,
        f"cluster {second.cluster_id} is not saved for this run",
    ) in problems
    assert (
        first.cluster_id,
        f"quote {second.quote_review_ids[0]} is not in the pain point's clusters",
    ) in problems


def test_a_market_gap_of_another_run_is_a_broken_link(run):
    prd, report, clusters, review_ids = run
    other = COMPETITORS.model_copy(update={"competitors": COMPETITORS.competitors[:1]})
    result = check.walk(prd, report, other, clusters, review_ids)

    (link,) = result["broken_links"]
    assert link["requirement"] == "req_004" and link["evidence"].startswith("gap_")
    assert result["grounded"] == 4  # req_004 also cites a pain point


PLAN = TaskPlan.model_validate(
    {
        "epics": [
            {
                "id": "epic_01",
                "title": "Payments",
                "description": "Make payments reliable.",
                "tasks": [
                    {
                        "id": f"task_{n:03d}",
                        "title": f"Task {n}",
                        "description": "Do it.",
                        "requirement_ids": ["req_001"],
                        "effort": "S",
                    }
                    for n in range(1, 7)
                ],
            }
        ]
    }
)
CRITERIA = AcceptanceCriteria.model_validate(
    {
        "tasks": [
            {
                "task_id": task.id,
                "criteria": [
                    {"kind": "happy_path", "given": "a", "when": "b", "then": "c"},
                    {"kind": "failure_state", "given": "d", "when": "e", "then": "f"},
                ],
            }
            for task in PLAN.tasks
        ]
    }
)


def test_the_rating_sample_is_repeatable_in_plan_order_and_empty():
    sample = check.rating_sample(PLAN, CRITERIA, 4, seed=7)

    assert sample == check.rating_sample(PLAN, CRITERIA, 4, seed=7)
    ids = [task["task_id"] for task in sample]
    assert len(ids) == 4 and ids == sorted(ids)
    assert all(task["rating"] is None and len(task["criteria"]) == 2 for task in sample)
    assert all(c["testable"] is None for task in sample for c in task["criteria"])
    assert len(check.rating_sample(PLAN, CRITERIA, 10, seed=7)) == 6


def test_only_what_the_owner_filled_in_counts():
    sample = check.rating_sample(PLAN, CRITERIA, 6, seed=1)
    assert check.rating_summary(sample)["average_rating"] is None

    sample[0]["rating"], sample[1]["rating"] = 5, 2
    sample[0]["criteria"][0]["testable"] = True
    sample[0]["criteria"][1]["testable"] = False
    assert check.rating_summary(sample) == {
        "tasks": 6,
        "rated": 2,
        "average_rating": 3.5,
        "criteria": 12,
        "criteria_marked": 2,
        "untestable": 1,
    }


@pytest.mark.parametrize("rating", [0, 6, 4.5, "good", True])
def test_a_rating_outside_the_scale_is_refused(rating):
    sample = check.rating_sample(PLAN, CRITERIA, 1, seed=1)
    sample[0]["rating"] = rating
    with pytest.raises(ValueError, match="1 to 5"):
        check.rating_summary(sample)
