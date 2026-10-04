"""Gate 1: line up the pain points the owner expects against the ones a run reported.

Run from backend/:

    py -m uv run python eval/gate1/line_up.py input SLUG
    py -m uv run python eval/gate1/line_up.py propose SLUG RUN_ID
    py -m uv run python eval/gate1/line_up.py summary

`input` writes the run input of eval/gate1/expected/SLUG.json to
eval/gate1/inputs/SLUG.json, for `productfoundry run --input`.
`propose` reads eval/gate1/expected/SLUG.json (written by the owner before seeing
any output) and the run's pain-point report, and writes eval/gate1/results/SLUG.json:
for each expected theme the nearest reported pain points by embedding similarity,
with `confirmed: null`. The owner sets each `confirmed` to true or false; nothing
counts as found until then. `summary` prints the gate's numbers for every result.

No LLM call and no source request is made. Embeddings are local.
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).parent
EXPECTED = HERE / "expected"
INPUTS = HERE / "inputs"
RESULTS = HERE / "results"
CANDIDATES = 3  # reported pain points offered for each expected theme


def propose_matches(
    expected: Sequence[str],
    pain_points: Sequence[Mapping[str, Any]],
    expected_vectors: np.ndarray,
    pain_point_vectors: np.ndarray,
) -> list[dict[str, Any]]:
    """For each expected theme, the nearest pain points. Vectors are L2-normalised rows.

    The best candidate is only a proposal: `confirmed` stays null until the owner decides.
    """
    matches = []
    for theme, vector in zip(expected, expected_vectors, strict=True):
        similarity = pain_point_vectors @ vector if len(pain_points) else np.empty(0)
        nearest = sorted(range(len(pain_points)), key=lambda i: (-similarity[i], i))[:CANDIDATES]
        candidates = [
            {
                "rank": pain_points[i]["rank"],
                "label": pain_points[i]["label"],
                "similarity": round(float(similarity[i]), 3),
            }
            for i in nearest
        ]
        matches.append(
            {
                "expected": theme,
                "proposed_rank": candidates[0]["rank"] if candidates else None,
                "candidates": candidates,
                "confirmed": None,
            }
        )
    return matches


def summarise(result: Mapping[str, Any]) -> dict[str, Any]:
    """The gate's numbers for one product, from its result file."""
    matches = result["matches"]
    return {
        "product": result["product"],
        "run_id": result["run_id"],
        "expected": len(matches),
        "found": sum(match["confirmed"] is True for match in matches),
        "undecided": sum(match["confirmed"] is None for match in matches),
        "pain_points": result["pain_points"],
        "junk_clusters": result["junk_clusters"],
        "reviews": result["reviews"],
        "llm": result["llm"],
        "wall_seconds": result["wall_seconds"],
        "unresolved_quotes": result["unresolved_quotes"],
        "owner_verdict": result.get("owner_verdict"),
    }


def wall_seconds(stages: Sequence[Any]) -> float:
    """Time the stages spent running, without the wait at checkpoints."""
    spent = sum(
        (stage.finished_at - stage.started_at for stage in stages if stage.finished_at),
        timedelta(),
    )
    return round(spent.total_seconds(), 1)


def _input(slug: str) -> int:
    from productfoundry.core.run_input import RunInput

    source = EXPECTED / f"{slug}.json"
    if not source.exists():
        print(f"error: {source} does not exist; the owner writes it first", file=sys.stderr)
        return 1
    given = json.loads(source.read_text(encoding="utf-8"))
    run_input = RunInput.model_validate(given["run_input"])
    INPUTS.mkdir(exist_ok=True)
    target = INPUTS / f"{slug}.json"
    target.write_text(run_input.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(target)
    return 0


def _propose(slug: str, run_id: str) -> int:
    from productfoundry import storage
    from productfoundry.core.competitors import CompetitorList
    from productfoundry.core.pain_points import PainPointReport
    from productfoundry.ml.config import load_config
    from productfoundry.ml.embeddings import SentenceTransformerEmbedder
    from productfoundry.stages.s3_pain_points import run_reviews

    source = EXPECTED / f"{slug}.json"
    if not source.exists():
        print(f"error: {source} does not exist; the owner writes it first", file=sys.stderr)
        return 1
    given = json.loads(source.read_text(encoding="utf-8"))
    expected = [theme for theme in given["expected"] if theme.strip()]

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
        _, reviews = run_reviews(competitors, storage.ReviewRepository(sessions))
        llm = storage.PostgresCallStore(sessions).run_totals(run_id)
    finally:
        engine.dispose()

    points = [point.model_dump(mode="json") for point in report.pain_points]
    embedder = SentenceTransformerEmbedder(load_config().embeddings)
    texts = [f"{point['label']}. {point['description']}" for point in points]
    vectors = embedder.encode([*expected, *texts])
    result = {
        "product": given["product"],
        "run_id": run_id,
        "matches": propose_matches(
            expected, points, vectors[: len(expected)], vectors[len(expected) :]
        ),
        "pain_points": len(points),
        "junk_clusters": len(report.junk_clusters),
        "reviews": report.language_counts.model_dump()
        | {"clustered": report.clustered_reviews, "noise": report.noise_reviews},
        "llm": llm,
        "wall_seconds": wall_seconds(run.stages),
        "unresolved_quotes": sorted(
            quote
            for point in report.pain_points
            for quote in point.quote_review_ids
            if quote not in reviews
        ),
        "owner_verdict": None,
    }
    RESULTS.mkdir(exist_ok=True)
    target = RESULTS / f"{slug}.json"
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(target)
    for match in result["matches"]:
        best = match["candidates"][0] if match["candidates"] else None
        shown = f"{best['rank']}. {best['label']} ({best['similarity']})" if best else "nothing"
        print(f"  {match['expected']}\n    -> {shown}")
    print("Set each `confirmed` to true or false in that file, then run `summary`.")
    return 0


def _summary() -> int:
    files = sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []
    if not files:
        print("no results yet: run `propose` first")
        return 1
    for path in files:
        row = summarise(json.loads(path.read_text(encoding="utf-8")))
        reviews, llm = row["reviews"], row["llm"]
        print(f"{row['product']}  ({row['run_id']})")
        print(
            f"  expected themes found: {row['found']} of {row['expected']}"
            + (f"  ({row['undecided']} not yet confirmed)" if row["undecided"] else "")
        )
        print(f"  pain points: {row['pain_points']}, junk clusters: {row['junk_clusters']}")
        print(
            f"  reviews: {reviews['english']} English, {reviews['hinglish']} Hinglish, "
            f"{reviews['not_analysed']} not analysed; {reviews['clustered']} clustered, "
            f"{reviews['noise']} noise"
        )
        print(
            f"  LLM: {llm['attempts']} attempts, {llm['answered']} answered, "
            f"{llm['cache_hits']} cache hits, {llm['input_tokens'] + llm['output_tokens']} tokens"
        )
        print(f"  wall time: {row['wall_seconds']} s")
        print(f"  quotes that do not resolve to a stored review: {len(row['unresolved_quotes'])}")
        print(f"  owner's verdict: {row['owner_verdict'] or 'not given'}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    write_input = commands.add_parser("input", help="write a product's run input file")
    write_input.add_argument("slug")
    propose = commands.add_parser("propose", help="propose matches for one product's run")
    propose.add_argument("slug", help="name of the file in eval/gate1/expected, without .json")
    propose.add_argument("run_id")
    commands.add_parser("summary", help="print the gate's numbers for every result file")
    args = parser.parse_args(argv)
    if args.command == "input":
        return _input(args.slug)
    return _propose(args.slug, args.run_id) if args.command == "propose" else _summary()


if __name__ == "__main__":
    raise SystemExit(main())
