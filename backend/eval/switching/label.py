"""Switching intent: prepare reviews for the owner to label, then report precision.

Run from backend/:

    py -m uv run python eval/switching/label.py prepare RUN_ID [--limit 200]
    py -m uv run python eval/switching/label.py precision

`prepare` writes two files in eval/switching/:
- `to_label.csv`: up to `--limit` reviews of the run's products, in a shuffled
  order, with an empty `owner_label` column. It holds review text, so it is not
  committed. It does not show what the model said: the owner labels blind.
- `model_labels.json`: the model's intent for each of those reviews, by review id.
  It belongs to that CSV, so it is not committed either.

The owner fills `owner_label` with one of: leaving, switched_from, switched_to,
considering, none. `precision` then reports, of the reviews the model called
switching, how many the owner also calls switching, and how many get the same
intent. No LLM call and no source request is made.
"""

import argparse
import csv
import json
import random
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
TO_LABEL = HERE / "to_label.csv"
MODEL_LABELS = HERE / "model_labels.json"
LABELS = ("leaving", "switched_from", "switched_to", "considering", "none")
COLUMNS = ("review_id", "product", "text", "owner_label")


def sample_ids(
    review_ids: Sequence[str], flagged: Sequence[str], limit: int, seed: int
) -> list[str]:
    """Which reviews the owner labels: every review the model called switching, then
    others chosen at random up to `limit`, all in a shuffled order."""
    rng = random.Random(seed)
    chosen = sorted(set(flagged).intersection(review_ids))[:limit]
    others = sorted(set(review_ids).difference(chosen))
    chosen += rng.sample(others, min(len(others), limit - len(chosen)))
    rng.shuffle(chosen)
    return chosen


def precision(owner: Mapping[str, str], model: Mapping[str, str]) -> dict[str, Any]:
    """Precision of the model's switching labels against the owner's.

    `owner` and `model` map review id to a label. Only reviews the owner has
    labelled count. `switching` asks whether the owner also sees switching;
    `same_intent` asks for the same kind of switching.
    """
    unknown = sorted({label for label in owner.values() if label and label not in LABELS})
    if unknown:
        raise ValueError(f"owner_label must be one of {', '.join(LABELS)}; found {unknown}")
    labelled = {review: label for review, label in owner.items() if label}
    flagged = [r for r in labelled if model.get(r, "none") != "none"]
    agreed = [r for r in flagged if labelled[r] != "none"]
    same = [r for r in flagged if labelled[r] == model[r]]
    missed = [r for r in labelled if labelled[r] != "none" and model.get(r, "none") == "none"]
    return {
        "labelled": len(labelled),
        "unlabelled": len(owner) - len(labelled),
        "model_switching": len(flagged),
        "switching_precision": len(agreed) / len(flagged) if flagged else None,
        "same_intent_precision": len(same) / len(flagged) if flagged else None,
        "missed_by_model": len(missed),
    }


def _prepare(run_id: str, limit: int) -> int:
    from productfoundry import storage
    from productfoundry.core.competitors import CompetitorList
    from productfoundry.core.pain_points import PainPointReport
    from productfoundry.core.reviews import ANALYSED_LANGUAGES
    from productfoundry.stages.s3_pain_points import run_reviews

    engine = storage.make_engine()
    try:
        storage.check_ready(engine)
        sessions = storage.make_sessions(engine)
        run = storage.PostgresRunStore(sessions).get(run_id)
        output = run.stage("s3_pain_points").effective_output
        if output is None:
            print(f"error: run {run_id} has no pain-point report yet", file=sys.stderr)
            return 1
        report = PainPointReport.model_validate(output)
        competitors = CompetitorList.model_validate(run.stage("s1_competitors").effective_output)
        names, reviews = run_reviews(competitors, storage.ReviewRepository(sessions))
    finally:
        engine.dispose()

    model = {item.review_id: str(item.intent) for item in report.switching_reviews}
    analysed = [r.id for r in reviews.values() if r.language in ANALYSED_LANGUAGES]
    chosen = sample_ids(analysed, list(model), limit, run.seed)
    with TO_LABEL.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(COLUMNS)
        for review_id in chosen:
            review = reviews[review_id]
            writer.writerow([review_id, names.get(review.product_id, ""), review.text, ""])
    labels = {review_id: model.get(review_id, "none") for review_id in chosen}
    MODEL_LABELS.write_text(
        json.dumps({"run_id": run_id, "labels": labels}, indent=2) + "\n", encoding="utf-8"
    )
    flagged = sum(label != "none" for label in labels.values())
    print(f"{TO_LABEL}: {len(chosen)} reviews to label ({flagged} the model calls switching)")
    if len(chosen) < limit:
        print(f"note: only {len(chosen)} analysed reviews are stored for this run, not {limit}")
    print(f"Fill the owner_label column with one of: {', '.join(LABELS)}")
    return 0


def _precision() -> int:
    if not TO_LABEL.exists() or not MODEL_LABELS.exists():
        print("error: run `prepare` first", file=sys.stderr)
        return 1
    with TO_LABEL.open(encoding="utf-8", newline="") as file:
        owner = {
            row["review_id"]: row["owner_label"].strip().lower() for row in csv.DictReader(file)
        }
    model = json.loads(MODEL_LABELS.read_text(encoding="utf-8"))["labels"]
    try:
        result = precision(owner, model)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if result["unlabelled"]:
        print(f"note: {result['unlabelled']} reviews are not labelled yet")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="write the reviews to label")
    prepare.add_argument("run_id")
    prepare.add_argument("--limit", type=int, default=200)
    commands.add_parser("precision", help="score the model against the owner's labels")
    args = parser.parse_args(argv)
    return _prepare(args.run_id, args.limit) if args.command == "prepare" else _precision()


if __name__ == "__main__":
    raise SystemExit(main())
