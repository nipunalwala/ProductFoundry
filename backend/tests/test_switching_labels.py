import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "eval" / "switching" / "label.py"
spec = importlib.util.spec_from_file_location("switching_label", SCRIPT)
label = importlib.util.module_from_spec(spec)
spec.loader.exec_module(label)

IDS = [f"rev_{n:03d}" for n in range(30)]


def test_every_review_the_model_flagged_is_in_the_sample_and_the_rest_are_random():
    flagged = ["rev_003", "rev_017", "rev_999"]  # the last is not a review of this run
    chosen = label.sample_ids(IDS, flagged, limit=10, seed=1)
    assert len(chosen) == len(set(chosen)) == 10
    assert {"rev_003", "rev_017"} <= set(chosen) and "rev_999" not in chosen
    assert chosen == label.sample_ids(IDS, flagged, limit=10, seed=1)  # the seed fixes it
    assert chosen != label.sample_ids(IDS, flagged, limit=10, seed=2)
    assert chosen != sorted(chosen)  # shuffled: position says nothing about the model's label
    assert sorted(label.sample_ids(IDS, flagged, limit=200, seed=1)) == IDS  # fewer than asked


def test_precision_counts_only_what_the_owner_has_labelled():
    model = {"rev_000": "leaving", "rev_001": "considering", "rev_002": "switched_to",
             "rev_003": "leaving", "rev_004": "none", "rev_005": "none"}  # fmt: skip
    owner = {"rev_000": "leaving", "rev_001": "leaving", "rev_002": "none",
             "rev_003": "", "rev_004": "considering", "rev_005": "none"}  # fmt: skip
    assert label.precision(owner, model) == {
        "labelled": 5,
        "unlabelled": 1,
        "model_switching": 3,
        "switching_precision": 2 / 3,  # the owner sees switching in two of the three
        "same_intent_precision": 1 / 3,  # and the same kind in one
        "missed_by_model": 1,
    }
    nothing = label.precision({"rev_000": ""}, model)
    assert nothing["switching_precision"] is None and nothing["unlabelled"] == 1
    with pytest.raises(ValueError, match="owner_label must be one of"):
        label.precision({"rev_000": "maybe"}, model)
