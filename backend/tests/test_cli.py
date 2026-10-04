import json

import pytest

from conftest import run_input_data
from productfoundry import __version__
from productfoundry.cli import main
from productfoundry.core.registry import SCHEMAS


def test_version_flag_prints_the_package_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"productfoundry {__version__}"


@pytest.fixture
def cli(tmp_path, capsys):
    """Runs a command against a state file in tmp_path; returns (exit code, stdout, stderr)."""
    state = tmp_path / "runs.json"

    def run(*args):
        code = main(["--memory", "--fake-stages", "--state-file", str(state), *args])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


@pytest.fixture
def input_file(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(run_input_data()), encoding="utf-8")
    return path


def started_run(cli, input_file) -> str:
    code, out, _ = cli("run", "--input", str(input_file))
    assert code == 0
    return out.split()[1]


def test_run_stops_at_the_first_checkpoint(cli, input_file):
    code, out, _ = cli("run", "--input", str(input_file), "--seed", "3")
    assert code == 0
    assert "status  awaiting_approval" in out
    assert "productfoundry approve run_" in out


def test_each_command_is_a_new_process_and_the_run_carries_on(cli, input_file):
    run_id = started_run(cli, input_file)

    code, out, _ = cli("status")
    assert code == 0 and run_id in out and "awaiting_approval" in out

    code, out, _ = cli("approve", run_id)
    assert code == 0 and "s3_pain_points   awaiting_approval" in out

    code, out, _ = cli("approve", run_id)
    assert code == 0 and "status  completed" in out

    code, out, _ = cli("resume", run_id, "--from-stage", "s2_reviews")
    assert code == 0 and "status  awaiting_approval" in out

    code, out, _ = cli("status", run_id)
    assert "s2_reviews       completed" in out


def test_approve_with_an_edited_file(cli, input_file, tmp_path):
    run_id = started_run(cli, input_file)
    _, out, _ = cli("status", run_id, "--output", "s1_competitors")
    edited = json.loads(out)
    edited["competitors"] = edited["competitors"][:1]
    edit_file = tmp_path / "edit.json"
    edit_file.write_text(json.dumps(edited), encoding="utf-8")

    code, out, _ = cli("approve", run_id, "--edit", str(edit_file))
    assert code == 0 and "s1_competitors   completed (edited)" in out

    _, out, _ = cli("status", run_id, "--output", "s2_reviews")
    assert len(json.loads(out)["products"]) == 1


def test_an_invalid_edit_is_reported_and_the_run_still_waits(cli, input_file, tmp_path):
    run_id = started_run(cli, input_file)
    edit_file = tmp_path / "edit.json"
    edit_file.write_text('{"competitors": []}', encoding="utf-8")

    code, _, err = cli("approve", run_id, "--edit", str(edit_file))
    assert code == 1 and "edited CompetitorList is invalid" in err

    _, out, _ = cli("status", run_id)
    assert "status  awaiting_approval" in out


def test_invalid_run_input_is_reported(cli, tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(run_input_data(incumbent=None)), encoding="utf-8")
    code, _, err = cli("run", "--input", str(path))
    assert code == 1 and "requires an incumbent" in err

    code, _, err = cli("run", "--input", str(tmp_path / "missing.json"))
    assert code == 1 and "cannot read" in err


def test_errors_are_one_line_not_a_traceback(cli, input_file):
    code, _, err = cli("status", "run_missing")
    assert code == 1 and err.strip() == "error: run run_missing not found"

    run_id = started_run(cli, input_file)
    code, _, err = cli("resume", run_id)
    assert code == 1 and "approve it first" in err


def test_status_with_no_runs(cli):
    assert cli("status") == (0, "no runs\n", "")


def test_schema_lists_prints_and_writes_json_schema(cli, tmp_path):
    code, out, _ = cli("schema")
    assert code == 0 and out.split() == [
        "RunInput", "v1", "CompetitorList", "v1", "ReviewSet", "v1", "PainPointReport", "v1",
    ]  # fmt: skip

    code, out, _ = cli("schema", "RunInput")
    assert code == 0 and json.loads(out)["title"] == "RunInput"

    out_dir = tmp_path / "schemas"
    code, _, _ = cli("schema", "--out", str(out_dir))
    assert code == 0
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(
        f"{name}.v1.schema.json" for name in SCHEMAS
    )
    assert json.loads((out_dir / "PainPointReport.v1.schema.json").read_text(encoding="utf-8"))


def test_the_competitor_checkpoint_prints_the_list_and_takes_remove_and_add(
    cli, input_file, tmp_path
):
    code, out, _ = cli("run", "--input", str(input_file))
    run_id = out.split()[1]
    assert "competitors\n  1. Walnut (incumbent)  https://walnut.example" in out
    assert "  2. Fake Rival  https://rival.example" in out
    assert "why: Solves the same problem for the same users." in out

    add_file = tmp_path / "add.json"
    added = {
        "name": "Money View",
        "url": "https://moneyview.example",
        "positioning": "Expense tracking and loans.",
        "target_users": "Salaried people",
    }
    add_file.write_text(json.dumps([added]), encoding="utf-8")

    code, _, err = cli("approve", run_id, "--remove", "Nobody")
    assert code == 1 and "no competitor named 'Nobody'" in err

    code, out, _ = cli("approve", run_id, "--remove", "fake rival", "--add", str(add_file))
    assert code == 0 and "s1_competitors   completed (edited)" in out

    _, out, _ = cli("status", run_id, "--output", "s1_competitors")
    assert [c["name"] for c in json.loads(out)["competitors"]] == ["Walnut", "Money View"]

    code, _, err = cli("approve", run_id, "--remove", "Walnut")
    assert code == 1 and "competitor checkpoint" in err
