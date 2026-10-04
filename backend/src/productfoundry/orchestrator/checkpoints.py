"""Edits a user makes at a checkpoint, as pure functions on the stage output."""

from collections.abc import Mapping, Sequence
from typing import Any

from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.ids import product_id
from productfoundry.core.names import normalise_name
from productfoundry.core.pain_points import TrendSettings, pain_point_score
from productfoundry.ml.trends import merged_trend

_MAX_QUOTES = 5


def edit_competitors(
    output: Mapping[str, Any], *, remove: Sequence[str] = (), add: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """Remove competitors by name or id and add new ones. The result is validated on approval."""
    competitors = [dict(competitor) for competitor in output["competitors"]]
    for target in remove:
        key = normalise_name(target)
        kept = [c for c in competitors if c["id"] != target and normalise_name(c["name"]) != key]
        if len(kept) == len(competitors):
            names = ", ".join(c["name"] for c in competitors)
            raise ProductFoundryError(f"no competitor named {target!r}; the list has: {names}")
        competitors = kept
    for new in add:
        if not isinstance(new, Mapping) or not new.get("name"):
            raise ProductFoundryError("each added competitor is an object with at least a name")
        competitor = dict(new)
        store_ids = competitor.get("store_ids") or {}
        key = store_ids.get("google_play") or store_ids.get("app_store")
        competitor.setdefault("id", product_id(key or normalise_name(competitor["name"])))
        competitor.setdefault("reason", "Added by the user at the checkpoint.")
        competitors.append(competitor)
    return {**output, "competitors": competitors}


def edit_pain_points(
    output: Mapping[str, Any],
    *,
    rename: Mapping[str, str] | None = None,
    merge: Sequence[Sequence[str]] = (),
    drop: Sequence[str] = (),
    rank: Sequence[str] = (),
) -> dict[str, Any]:
    """Rename, merge, drop and re-rank pain points. The result is validated on approval.

    A pain point is named by its rank in `output` or by its cluster id. Every
    name refers to `output` as it was, so one edit does not renumber the next.
    A merge folds the later pain points of a group into the first; the merged
    pain point counts the reviews of all of them. Without `rank` the order is
    by score; with it, the named pain points come first, in that order.
    """
    points = {point["cluster_id"]: dict(point) for point in output["pain_points"]}
    by_rank = {str(point["rank"]): point["cluster_id"] for point in output["pain_points"]}

    def resolve(name: str) -> str:
        cluster = by_rank.get(str(name).strip(), str(name).strip())
        if cluster not in by_rank.values():
            raise ProductFoundryError(
                f"no pain point {name!r}; use a rank from 1 to {len(by_rank)} or a cluster id"
            )
        return cluster

    def present(cluster: str, action: str) -> dict[str, Any]:
        if cluster not in points:
            raise ProductFoundryError(f"cannot {action} {cluster}: it is dropped or merged away")
        return points[cluster]

    for name in drop:
        present(resolve(name), "drop")
        del points[resolve(name)]
    for group in merge:
        clusters = [resolve(name) for name in group]
        if len(clusters) < 2 or len(set(clusters)) != len(clusters):
            raise ProductFoundryError("a merge names two or more different pain points")
        merged = [present(cluster, "merge") for cluster in clusters]
        points[clusters[0]] = _merge(merged, output)
        for cluster in clusters[1:]:
            del points[cluster]
    for name, label in (rename or {}).items():
        present(resolve(name), "rename")["label"] = label

    first = [resolve(name) for name in rank]
    if len(set(first)) != len(first):
        raise ProductFoundryError("a pain point appears once in the new order")
    for cluster in first:
        present(cluster, "rank")
    rest = sorted(
        (point for cluster, point in points.items() if cluster not in first),
        key=lambda point: (-point["score"], -point["review_count"], point["cluster_id"]),
    )
    ordered = [points[cluster] for cluster in first] + rest
    return {**output, "pain_points": [point | {"rank": n} for n, point in enumerate(ordered, 1)]}


def _merge(points: Sequence[Mapping[str, Any]], report: Mapping[str, Any]) -> dict[str, Any]:
    """One pain point holding the reviews of all of them. The first gives the label."""
    settings = TrendSettings.model_validate(report["trend_settings"])
    switching = [review for point in points for review in point.get("switching_review_ids", [])]
    target = dict(points[0])
    count = sum(point["review_count"] for point in points)
    negative = sum(round(point["negative_share"] * point["review_count"]) for point in points)
    worst = max(points, key=lambda point: point["severity"])  # the first, when equal
    # Quotes are taken in turn from each, so the merged pain point shows all its parts.
    turns = [list(point["quote_review_ids"]) for point in points]
    quotes = [turn[n] for n in range(_MAX_QUOTES) for turn in turns if n < len(turn)]
    quotes = quotes[:_MAX_QUOTES]
    glosses = {k: v for point in points for k, v in point.get("quote_glosses", {}).items()}
    target.update(
        merged_cluster_ids=[
            cluster
            for n, point in enumerate(points)
            for cluster in ([point["cluster_id"]] if n else [])
            + list(point.get("merged_cluster_ids", []))
        ],
        review_count=count,
        negative_share=negative / count,
        severity=worst["severity"],
        severity_reason=worst["severity_reason"],
        product_ids=list(dict.fromkeys(p for point in points for p in point["product_ids"])),
        quote_review_ids=quotes,
        quote_glosses={review: glosses[review] for review in quotes if review in glosses},
        score=pain_point_score(count, negative / count, worst["severity"]),
        trend=merged_trend([point["trend"] for point in points], settings).model_dump(mode="json"),
        switching_review_ids=list(dict.fromkeys(switching)),
    )
    return target
