import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import run_input_data
from productfoundry.api import create_app
from productfoundry.api.app import Backend
from productfoundry.cli import openapi_text
from productfoundry.core.errors import QuotaExhausted
from productfoundry.jobs import RecordingQueue
from productfoundry.jobs.worker import advance_run, next_quota_reset, requeue_paused
from productfoundry.orchestrator import PIPELINE, InMemoryRunStore, Orchestrator
from productfoundry.stages.fakes import FAKE_STAGES
from productfoundry.stages.s3_pain_points import PainPointStage
from test_s3_pain_points import COMPETITORS, CONFIG, Labeller, gateway, services

S1, S3 = "s1_competitors", "s3_pain_points"


class Api:
    """The API on in-memory parts, and a stand-in for the worker that runs what was queued."""

    def __init__(self, stages=FAKE_STAGES, **backend) -> None:
        self.store = InMemoryRunStore()
        self.queue = RecordingQueue()
        self.orchestrator = Orchestrator(self.store, stages, services=backend.pop("used", None))
        self.backend = Backend(self.store, self.queue, self.orchestrator, **backend)
        self.client = TestClient(create_app(self.backend))

    def work(self) -> None:
        """What the worker does: run every queued job."""
        while self.queue.jobs:
            run_id, from_stage = self.queue.jobs.pop(0)
            self.orchestrator.resume(run_id, from_stage=from_stage)

    def start(self) -> str:
        response = self.client.post("/runs", json={"input": run_input_data(), "seed": 5})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def at_pain_points(self) -> str:
        run_id = self.start()
        self.work()
        assert self.client.post(f"/runs/{run_id}/approve").status_code == 200
        self.work()
        return run_id


@pytest.fixture
def api() -> Api:
    return Api(check_edits=False)


# Creating and reading runs


def test_health(api):
    assert api.client.get("/health").json() == {"status": "ok"}


def test_creating_a_run_stores_it_and_queues_it_without_running_a_stage(api):
    response = api.client.post("/runs", json={"input": run_input_data(), "seed": 5})
    run = response.json()
    assert response.status_code == 201
    assert (run["status"], run["seed"], run["idea"]) == ("pending", 5, run_input_data()["idea"])
    assert [stage["status"] for stage in run["stages"]] == ["pending"] * len(PIPELINE)
    assert api.queue.jobs == [(run["id"], None)]  # the API enqueues; the worker runs
    assert api.store.get(run["id"]).status == "pending"


def test_an_invalid_run_input_is_refused(api):
    response = api.client.post("/runs", json={"input": run_input_data(incumbent=None)})
    assert response.status_code == 422 and "requires an incumbent" in response.text
    assert api.client.post("/runs", json={"input": run_input_data(), "extra": 1}).status_code == 422
    assert api.queue.jobs == []


def test_a_run_shows_its_stage_statuses_and_the_open_checkpoint(api):
    run_id = api.start()
    api.work()
    run = api.client.get(f"/runs/{run_id}").json()
    assert (run["status"], run["awaiting_approval"]) == ("awaiting_approval", S1)
    first, second = run["stages"][0], run["stages"][1]
    assert (first["key"], first["number"], first["status"]) == (S1, 1, "awaiting_approval")
    assert first["checkpoint"] and first["has_output"] and not first["edited"]
    assert (second["status"], second["checkpoint"], second["has_output"]) == (
        "pending",
        False,
        False,
    )
    assert [s["checkpoint"] for s in run["stages"]] == [
        True,
        False,
        True,
        False,
        False,
        False,
        False,
    ]
    assert run["input"]["incumbent"]["name"] == "Walnut"

    assert api.client.get("/runs/run_missing").status_code == 404
    assert api.client.get("/runs/run_missing").json() == {"detail": "run run_missing not found"}


def test_runs_are_listed_newest_first(api):
    first, second = api.start(), api.start()
    listed = api.client.get("/runs").json()
    assert [run["id"] for run in listed] == [second, first]
    assert set(listed[0]) == {"id", "status", "idea", "created_at", "updated_at"}


def test_a_stage_output_is_read_with_the_user_s_edit_and_the_original(api):
    run_id = api.start()
    assert api.client.get(f"/runs/{run_id}/stages/{S1}").json()["output"] is None
    api.work()
    output = api.client.get(f"/runs/{run_id}/stages/{S1}").json()
    assert output["status"] == "awaiting_approval" and output["schema_version"] == 1
    assert [c["name"] for c in output["output"]["competitors"]] == ["Walnut", "Fake Rival"]
    assert output["output"] == output["original_output"] and not output["edited"]

    assert api.client.get(f"/runs/{run_id}/stages/s9_missing").status_code == 404
    assert api.client.get(f"/runs/run_missing/stages/{S1}").status_code == 404


# Checkpoints


def test_approving_records_the_approval_and_queues_the_rest(api):
    run_id = api.start()
    assert api.client.post(f"/runs/{run_id}/approve").status_code == 409  # nothing to approve yet
    api.work()

    response = api.client.post(f"/runs/{run_id}/approve")
    assert response.status_code == 200 and response.json()["status"] == "running"
    assert response.json()["stages"][0]["approved_at"] is not None
    assert api.queue.jobs == [(run_id, None)]
    assert api.store.get(run_id).stage("s2_reviews").status == "pending"  # not run by the API

    api.work()
    assert api.client.get(f"/runs/{run_id}").json()["awaiting_approval"] == S3


def test_the_competitor_checkpoint_takes_remove_and_add(api):
    run_id = api.start()
    api.work()
    added = {
        "name": "Money View",
        "url": "https://moneyview.example",
        "positioning": "Expense tracking and loans.",
        "target_users": "Salaried people",
    }
    body = {"competitors": {"remove": ["fake rival"], "add": [added]}}
    response = api.client.post(f"/runs/{run_id}/approve", json=body)
    assert response.status_code == 200 and response.json()["stages"][0]["edited"]

    output = api.client.get(f"/runs/{run_id}/stages/{S1}").json()
    assert [c["name"] for c in output["output"]["competitors"]] == ["Walnut", "Money View"]
    assert len(output["original_output"]["competitors"]) == 2 and output["edited"]
    api.work()
    reviews = api.client.get(f"/runs/{run_id}/stages/s2_reviews").json()["output"]
    assert len(reviews["products"]) == 2  # the edited list is what stage 2 received


def test_a_bad_edit_is_refused_and_the_run_still_waits(api):
    run_id = api.start()
    api.work()
    path = f"/runs/{run_id}/approve"

    response = api.client.post(path, json={"edited_output": {"competitors": []}})
    assert response.status_code == 422 and "edited CompetitorList is invalid" in response.text
    response = api.client.post(path, json={"competitors": {"remove": ["Nobody"]}})
    assert response.status_code == 400 and "no competitor named 'Nobody'" in response.text
    response = api.client.post(path, json={"pain_points": {"drop": ["1"]}})
    assert response.status_code == 400 and "pain-point checkpoint" in response.text
    both = {"edited_output": {"competitors": []}, "competitors": {"remove": ["Fake Rival"]}}
    assert api.client.post(path, json=both).status_code == 400
    assert api.client.post(path, json={"unknown": 1}).status_code == 422

    assert api.client.get(f"/runs/{run_id}").json()["status"] == "awaiting_approval"
    assert api.queue.jobs == []


def test_the_pain_point_checkpoint_takes_rename_merge_drop_and_rank(api):
    run_id = api.at_pain_points()
    body = {"pain_points": {"rename": {"1": "Money is lost"}}}
    assert api.client.post(f"/runs/{run_id}/approve", json=body).status_code == 200
    api.work()
    run = api.client.get(f"/runs/{run_id}").json()
    assert run["status"] == "completed" and run["awaiting_approval"] is None
    output = api.client.get(f"/runs/{run_id}/stages/{S3}").json()
    assert output["output"]["pain_points"][0]["label"] == "Money is lost"
    assert output["original_output"]["pain_points"][0]["label"] == "Payments fail"


@pytest.fixture
def real_stage_3() -> Api:
    """Stage 3 for real, on invented reviews held in memory, so edits and exports have evidence."""
    used = services(gateway(gemini=Labeller()))
    stages = FAKE_STAGES | {S1: lambda *_: COMPETITORS, S3: PainPointStage(CONFIG)}
    return Api(stages, used=used, reviews=used.reviews, clusters=used.clusters)


def test_an_edited_pain_point_report_is_checked_against_the_stored_evidence(real_stage_3):
    api = real_stage_3
    run_id = api.at_pain_points()
    report = api.client.get(f"/runs/{run_id}/stages/{S3}").json()["output"]
    report["pain_points"][0]["quote_review_ids"][0] = "rev_invented"
    report["pain_points"][0]["quote_glosses"] = {}

    response = api.client.post(f"/runs/{run_id}/approve", json={"edited_output": report})
    assert response.status_code == 422 and "does not match the stored evidence" in response.text
    assert api.client.get(f"/runs/{run_id}").json()["status"] == "awaiting_approval"

    body = {"pain_points": {"merge": [["1", "2"]], "drop": ["3"]}}
    assert api.client.post(f"/runs/{run_id}/approve", json=body).status_code == 200
    (point,) = api.client.get(f"/runs/{run_id}/stages/{S3}").json()["output"]["pain_points"]
    assert point["review_count"] == 26 and len(point["merged_cluster_ids"]) == 1


# Resume, pause and failure


def test_resume_queues_a_failed_run_and_refuses_what_makes_no_sense(api):
    run_id = api.start()
    api.work()
    waiting = api.client.post(f"/runs/{run_id}/resume")
    assert waiting.status_code == 409 and "approve it first" in waiting.text
    unknown = api.client.post(f"/runs/{run_id}/resume", json={"from_stage": "s9_missing"})
    assert unknown.status_code == 409 and "unknown stage" in unknown.text
    assert api.client.post("/runs/run_missing/resume").status_code == 404
    assert api.queue.jobs == []

    response = api.client.post(f"/runs/{run_id}/resume", json={"from_stage": S1})
    assert response.status_code == 202 and api.queue.jobs == [(run_id, S1)]
    api.work()

    api.client.post(f"/runs/{run_id}/approve")
    api.work()
    api.client.post(f"/runs/{run_id}/approve")
    api.work()
    done = api.client.post(f"/runs/{run_id}/resume")
    assert done.status_code == 409 and "name a stage" in done.text


def test_a_failed_run_says_why_and_can_be_queued_again():
    def broken(*_):
        raise RuntimeError("the store answered 500")

    api = Api(FAKE_STAGES | {"s2_reviews": broken}, check_edits=False)
    run_id = api.start()
    api.work()
    api.client.post(f"/runs/{run_id}/approve")
    api.work()
    run = api.client.get(f"/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["error"] == "s2_reviews: RuntimeError: the store answered 500"
    assert run["stages"][1]["error"] == "RuntimeError: the store answered 500"

    assert api.client.post(f"/runs/{run_id}/resume").status_code == 202
    assert api.queue.jobs == [(run_id, None)]


def test_a_run_out_of_quota_shows_as_paused_not_failed():
    def out_of_quota(*_):
        raise QuotaExhausted("out of quota for task review_sentiment: groq, gemini")

    api = Api(FAKE_STAGES | {"s2_reviews": out_of_quota}, check_edits=False)
    run_id = api.start()
    api.work()
    api.client.post(f"/runs/{run_id}/approve")
    api.work()

    run = api.client.get(f"/runs/{run_id}").json()
    assert run["status"] == "paused_quota" and run["error"] is None
    assert run["pause_reason"] == "out of quota for task review_sentiment: groq, gemini"
    assert run["stages"][1]["status"] == "pending" and run["stages"][1]["error"] is None
    assert api.client.get("/runs").json()[0]["status"] == "paused_quota"

    # The scheduled job queues it again once the quota has reset.
    other = api.start()
    api.queue.jobs.clear()
    assert requeue_paused(api.store, api.queue) == [run_id]
    assert api.queue.jobs == [(run_id, None)] and other != run_id


def test_paused_runs_are_tried_again_just_after_the_next_utc_midnight():
    assert next_quota_reset(datetime(2026, 10, 4, 23, 59, tzinfo=UTC)) == datetime(
        2026, 10, 5, 0, 5, tzinfo=UTC
    )
    assert next_quota_reset(datetime(2026, 10, 4, 0, 1, tzinfo=UTC)) == datetime(
        2026, 10, 5, 0, 5, tzinfo=UTC
    )


# Exports


def test_exports_are_downloaded_as_markdown_or_json(real_stage_3):
    api = real_stage_3
    run_id = api.at_pain_points()

    report = api.client.get(f"/runs/{run_id}/exports/report")
    assert report.status_code == 200 and report.text.startswith("# Pain-point report: Walnut\n")
    assert report.headers["content-type"] == "text/markdown; charset=utf-8"
    assert report.headers["content-disposition"] == f'attachment; filename="{run_id}-report.md"'

    as_json = api.client.get(f"/runs/{run_id}/exports/report", params={"format": "json"})
    assert as_json.headers["content-type"] == "application/json"
    assert len(as_json.json()["pain_points"]) == 3

    early = api.client.get(f"/runs/{run_id}/exports/tasks")
    assert early.status_code == 400 and "has no task plan yet" in early.text
    assert api.client.get(f"/runs/{run_id}/exports/roadmap").status_code == 422
    assert api.client.get("/runs/run_missing/exports/report").status_code == 404

    api.client.post(f"/runs/{run_id}/approve")
    api.work()
    assert api.client.get(f"/runs/{run_id}").json()["status"] == "completed"
    prd = api.client.get(f"/runs/{run_id}/exports/prd")
    assert prd.status_code == 200 and prd.text.startswith("# PRD: ")
    tasks = api.client.get(f"/runs/{run_id}/exports/tasks", params={"format": "json"})
    assert tasks.json()["epics"][0]["tasks"][0]["acceptance_criteria"]


def test_an_export_that_quotes_reviews_needs_the_stored_reviews(api):
    run_id = api.at_pain_points()
    response = api.client.get(f"/runs/{run_id}/exports/report")
    assert response.status_code == 400 and "stored reviews" in response.text


# The worker's job and the committed schema


def test_the_worker_job_advances_a_stored_run(sessions, db_engine, run_input, monkeypatch):
    from productfoundry.storage import PostgresRunStore

    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv("FAKE_STAGES", "true")
    store = PostgresRunStore(sessions)
    run = Orchestrator(store, FAKE_STAGES).create_run(run_input)

    assert advance_run(run.id) == "awaiting_approval"
    assert store.get(run.id).stage(S1).output is not None
    assert advance_run(run.id).startswith("skipped: ")  # still waiting; the job does not crash
    assert advance_run(run.id, S1) == "awaiting_approval"
    assert advance_run("run_missing") == "skipped: run run_missing not found"


def test_the_committed_openapi_schema_is_current():
    committed = Path(__file__).parent.parent / "openapi.json"
    assert committed.read_text(encoding="utf-8") == openapi_text(), (
        "run `py -m uv run productfoundry openapi --out openapi.json` and commit the result"
    )
    paths = json.loads(committed.read_text(encoding="utf-8"))["paths"]
    assert sorted(paths) == [
        "/health",
        "/runs",
        "/runs/{run_id}",
        "/runs/{run_id}/approve",
        "/runs/{run_id}/exports/{name}",
        "/runs/{run_id}/resume",
        "/runs/{run_id}/stages/{stage_key}",
    ]
