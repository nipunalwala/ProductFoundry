"""The PRD with its citations resolved, as JSON and as Markdown."""

from collections.abc import Mapping
from typing import Any

from productfoundry.core.errors import StageOutputInvalid
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.prd import Prd
from productfoundry.core.reviews import Review
from productfoundry.stages.s3_pain_points.export import quote_lines, resolve_quote


def export_prd(
    prd: Prd,
    report: PainPointReport,
    reviews: Mapping[str, Review],
    product_names: Mapping[str, str],
    *,
    title: str,
) -> dict[str, Any]:
    """The PRD as plain data. Each citation carries what it points to: a pain point with
    its counts and one quote, or a market gap with its competitor fact.

    A citation that does not resolve is an error, never a gap in the document.
    """
    points = {point.cluster_id: point for point in report.pain_points}
    gaps = {gap.id: gap for gap in prd.market_gaps}
    evidence: dict[str, dict[str, Any]] = {}
    for requirement in prd.requirements:
        for evidence_id in requirement.evidence:
            if evidence_id in evidence:
                continue
            if evidence_id in points:
                point = points[evidence_id]
                evidence[evidence_id] = {
                    "id": evidence_id,
                    "kind": "pain_point",
                    "label": point.label,
                    "description": point.description,
                    "severity": point.severity,
                    "review_count": point.review_count,
                    "negative_share": point.negative_share,
                    "quote": resolve_quote(
                        point.quote_review_ids[0], point, reviews, product_names
                    ),
                }
            elif evidence_id in gaps:
                gap = gaps[evidence_id]
                evidence[evidence_id] = {
                    "id": evidence_id,
                    "kind": "market_gap",
                    "product": gap.product_name,
                    "fact": gap.fact,
                }
            else:
                raise StageOutputInvalid(
                    f"{requirement.id} cites {evidence_id}, which is not in this run"
                )
    return {
        "title": title,
        **prd.model_dump(mode="json", exclude={"market_gaps"}),
        "evidence": list(evidence.values()),
    }


def render_prd_markdown(export: Mapping[str, Any]) -> str:
    """The Markdown view of `export_prd`'s result. Every citation links to its evidence."""
    evidence = {item["id"]: item for item in export["evidence"]}
    lines = [f"# PRD: {export['title']}", "", "## Problem", "", export["problem"]]
    lines += ["", "## Users", "", export["users"]]
    lines += ["", "## Goals", "", *(f"- {goal}" for goal in export["goals"])]
    if export["non_goals"]:
        lines += ["", "## Non-goals", "", *(f"- {item}" for item in export["non_goals"])]

    lines += ["", "## Requirements"]
    for requirement in export["requirements"]:
        lines += [
            "",
            f"### `{requirement['id']}` ({requirement['priority']})",
            "",
            requirement["statement"],
            "",
            "Evidence:",
        ]
        for evidence_id in requirement["evidence"]:
            item = evidence[evidence_id]
            if item["kind"] == "pain_point":
                lines += [
                    "",
                    f"- Pain point [{item['label']}](#{evidence_id}): "
                    f"{item['review_count']} reviews, {item['negative_share']:.0%} negative, "
                    f"severity {item['severity']}/5",
                    "",
                    *(f"  {line}" for line in quote_lines(item["quote"])),
                ]
            else:
                lines += ["", f"- Market gap [{item['product']}](#{evidence_id}): {item['fact']}"]

    lines += ["", "## Success metrics", "", *(f"- {m}" for m in export["success_metrics"])]

    lines += ["", "## Evidence"]
    for item in export["evidence"]:
        lines += ["", f'<a id="{item["id"]}"></a>']
        if item["kind"] == "pain_point":
            lines += [
                f"**{item['label']}** (`{item['id']}`): {item['description']} "
                f"{item['review_count']} reviews, {item['negative_share']:.0%} negative, "
                f"severity {item['severity']}/5.",
            ]
        else:
            lines += [f"**Market gap: {item['product']}** (`{item['id']}`): {item['fact']}"]
        cited_by = [r["id"] for r in export["requirements"] if item["id"] in r["evidence"]]
        lines += ["", "Cited by: " + ", ".join(f"`{requirement}`" for requirement in cited_by)]
    return "\n".join(lines) + "\n"
