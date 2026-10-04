import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from productfoundry.core.run_input import RunInput
from productfoundry.llm.types import LlmCall
from productfoundry.ml.embeddings import FakeEmbedder
from productfoundry.orchestrator import StageRecord

GATE1 = Path(__file__).parent.parent / "eval" / "gate1"
spec = importlib.util.spec_from_file_location("line_up", GATE1 / "line_up.py")
line_up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(line_up)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
POINTS = [
    {"rank": 1, "label": "Payments fail", "description": "A payment fails after the debit."},
    {"rank": 2, "label": "Too many ads", "description": "Full-screen ads after every tap."},
    {"rank": 3, "label": "Login code missing", "description": "The login code never arrives."},
]
EXPECTED = ["ads everywhere", "cannot login", "payment gets stuck", "dark mode is missing"]


def matches():
    embedder = FakeEmbedder([["payment"], ["ads"], ["login"]])
    texts = [f"{point['label']}. {point['description']}".lower() for point in POINTS]
    vectors = embedder.encode([*EXPECTED, *texts])
    return line_up.propose_matches(EXPECTED, POINTS, vectors[:4], vectors[4:])


def test_each_expected_theme_is_offered_its_nearest_pain_points_and_nothing_is_confirmed():
    found = matches()
    assert [match["expected"] for match in found] == EXPECTED
    assert [match["proposed_rank"] for match in found[:3]] == [2, 3, 1]
    assert all(match["confirmed"] is None for match in found)
    assert all(len(match["candidates"]) == 3 for match in found)
    similarities = [c["similarity"] for c in found[0]["candidates"]]
    assert similarities == sorted(similarities, reverse=True) and similarities[0] > 0.8
    # A theme nothing matches still gets candidates, with a low similarity for the owner to see.
    assert found[3]["candidates"][0]["similarity"] < 0.5


def test_no_pain_points_means_no_proposal():
    (match,) = line_up.propose_matches(["ads"], [], np.ones((1, 4)), np.empty((0, 4)))
    assert match["proposed_rank"] is None and match["candidates"] == []


def test_only_what_the_owner_confirmed_counts_as_found():
    found = matches()
    found[0]["confirmed"], found[1]["confirmed"], found[2]["confirmed"] = True, False, True
    result = {
        "product": "Splitly",
        "run_id": "run_a",
        "matches": found,
        "pain_points": 3,
        "junk_clusters": 1,
        "reviews": {"english": 29, "hinglish": 14, "not_analysed": 1, "clustered": 38, "noise": 0},
        "llm": {
            "attempts": 5,
            "answered": 3,
            "cache_hits": 0,
            "input_tokens": 9,
            "output_tokens": 4,
        },
        "wall_seconds": 12.5,
        "unresolved_quotes": [],
    }
    summary = line_up.summarise(result)
    assert (summary["expected"], summary["found"], summary["undecided"]) == (4, 2, 1)
    assert summary["owner_verdict"] is None and summary["junk_clusters"] == 1


def test_wall_time_is_the_time_stages_ran_not_the_wait_at_checkpoints():
    stages = [
        StageRecord(key="s1", started_at=NOW, finished_at=NOW + timedelta(seconds=30)),
        StageRecord(
            key="s2",
            started_at=NOW + timedelta(hours=2),
            finished_at=NOW + timedelta(hours=2, seconds=12.5),
        ),
        StageRecord(key="s3"),
    ]
    assert line_up.wall_seconds(stages) == 42.5


def test_the_template_holds_a_valid_run_input_and_room_for_the_expected_themes():
    template = json.loads((GATE1 / "expected" / "TEMPLATE.json").read_text("utf-8"))
    assert RunInput.model_validate(template["run_input"]).incumbent.name == template["product"]
    assert len(template["expected"]) >= 2


def test_a_run_s_llm_totals_count_attempts_answers_cache_hits_and_tokens(sessions, run_input):
    from productfoundry.orchestrator import RunRecord
    from productfoundry.storage import PostgresCallStore, PostgresRunStore

    PostgresRunStore(sessions).create(
        RunRecord(id="run_a", input=run_input, seed=0, stages=[], created_at=NOW, updated_at=NOW)
    )
    calls = PostgresCallStore(sessions)

    def call(outcome="ok", run_id="run_a", **extra):
        calls.record(LlmCall("t", "groq", "m", "hash", outcome, NOW, run_id=run_id, **extra))

    call(input_tokens=100, output_tokens=20)
    call("server_error")
    call(cache_hit=True)
    call(run_id=None, input_tokens=999)

    assert calls.run_totals("run_a") == {
        "attempts": 2, "answered": 1, "cache_hits": 1, "input_tokens": 100, "output_tokens": 20,
    }  # fmt: skip
    assert calls.run_totals("run_none")["attempts"] == 0
