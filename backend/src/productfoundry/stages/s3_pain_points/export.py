"""The report with its quotes resolved to stored reviews, as JSON and as Markdown."""

import re
from collections.abc import Mapping
from typing import Any

from productfoundry.core.errors import StageOutputInvalid
from productfoundry.core.pain_points import PainPoint, PainPointReport
from productfoundry.core.reviews import Review

QUOTE_CHARS = 280  # reviews are quoted briefly as evidence, never in bulk
SOURCE_NAMES = {
    "google_play": "Google Play",
    "app_store": "App Store",
    "reddit": "Reddit",
    "product_hunt": "Product Hunt",
}


def shorten(text: str, limit: int = QUOTE_CHARS) -> str:
    """The text, cut at a word boundary with an ellipsis when it is longer than `limit`."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def resolve_quote(
    review_id: str,
    point: PainPoint,
    reviews: Mapping[str, Review],
    product_names: Mapping[str, str],
) -> dict[str, Any]:
    """A quoted review as plain data. A quote that is not stored is an error, never a gap."""
    review = reviews.get(review_id)
    if review is None:
        raise StageOutputInvalid(f"quoted review {review_id} is not stored")
    return {
        "review_id": review.id,
        "text": shorten(review.text),
        "language": review.language,
        # A translation of the quote, written by the LLM. Not the review's text.
        "translation": point.quote_glosses.get(review.id),
        "product": product_names.get(review.product_id, review.product_id),
        "source": str(review.source),
        "url": review.url,
        "date": review.reviewed_at.date().isoformat(),
        "rating": review.rating,
    }


def export_report(
    report: PainPointReport,
    reviews: Mapping[str, Review],
    product_names: Mapping[str, str],
    *,
    title: str,
) -> dict[str, Any]:
    """The report as plain data, each quote with its text, source URL and date.

    A quote that does not resolve to a stored review is an error, never a gap.
    """
    switching = {item.review_id: item for item in report.switching_reviews}
    points = []
    for point in report.pain_points:
        quotes = [
            resolve_quote(review_id, point, reviews, product_names)
            for review_id in point.quote_review_ids
        ]
        points.append(
            point.model_dump(
                mode="json", exclude={"quote_review_ids", "quote_glosses", "switching_review_ids"}
            )
            | {
                "products": [product_names.get(p, p) for p in point.product_ids],
                "quotes": quotes,
                # The pain point's reviews that talk about switching, each with what it says.
                "switching": [
                    resolve_quote(review_id, point, reviews, product_names)
                    | switching[review_id].model_dump(mode="json", exclude={"review_id"})
                    for review_id in point.switching_review_ids
                ],
            }
        )
        # The share is shown only for months with enough reviews to mean something.
        for month in points[-1]["trend"]["months"]:
            enough = month["enough"] and month["total_reviews"] > 0
            month["share"] = month["reviews"] / month["total_reviews"] if enough else None
    return {
        "title": title,
        "schema_version": report.schema_version,
        "ranking_formula": report.ranking_formula,
        "language_counts": report.language_counts.model_dump(),
        "clustered_reviews": report.clustered_reviews,
        "noise_reviews": report.noise_reviews,
        "pain_points": points,
        "trend_settings": report.trend_settings.model_dump(),
        "switching_table": [row.model_dump(mode="json") for row in report.switching_table],
        "switching_review_count": len(report.switching_reviews),
        "junk_clusters": [junk.model_dump() for junk in report.junk_clusters],
    }


def _plain(text: str) -> str:
    """Review text as inert Markdown: nothing a reviewer typed becomes formatting or a link."""
    return re.sub(r"([\\`*_\[\]<>#|])", r"\\\1", text)


def quote_lines(quote: Mapping[str, Any]) -> list[str]:
    """A quote as a Markdown blockquote: its words, its translation if any, its source."""
    where = f"{SOURCE_NAMES.get(quote['source'], quote['source'])}, {quote['date']}"
    if quote["url"]:
        where = f"[{where}]({quote['url']})"
    stars = f", {quote['rating']}/5 stars" if quote["rating"] else ""
    lines = [f"> {_plain(quote['text'])}"]
    if quote["translation"]:
        lines += [">", f"> *Translation: {_plain(quote['translation'])}*"]
    return [*lines, ">", f"> {quote['product']}, {where}{stars} · `{quote['review_id']}`"]


def trend_line(trend: Mapping[str, Any], window: int) -> str:
    """One sentence on where a pain point is heading, or why that cannot be said."""
    recent, previous = trend["recent_share"], trend["previous_share"]
    if recent is None or previous is None:
        return "Trend: not enough reviews in the last months to say."
    change = "rising" if trend["rising"] else "not rising"
    shares = (
        f"{recent:.0%} of all reviews in the last {window} months, "
        f"{previous:.0%} in the {window} before"
    )
    growth = "" if trend["growth"] is None else f" ({trend['growth']:+.0%})"
    return f"Trend: {change}. {shares}{growth}."


def render_markdown(export: Mapping[str, Any]) -> str:
    """The Markdown view of `export_report`'s result."""
    counts = export["language_counts"]
    total = counts["english"] + counts["hinglish"] + counts["not_analysed"]
    lines = [
        f"# Pain-point report: {export['title']}",
        "",
        f"{total} reviews: {counts['english']} English, {counts['hinglish']} Hinglish, "
        f"{counts['not_analysed']} not analysed (other languages are stored, not analysed).",
        f"{export['clustered_reviews']} negative or mixed reviews were grouped into themes; "
        f"{export['noise_reviews']} fitted no theme.",
        "",
        f"Ranking: {export['ranking_formula']}.",
    ]
    if not export["pain_points"]:
        lines += ["", "No pain points: there were not enough negative reviews to form a theme."]
    for point in export["pain_points"]:
        merged = point["merged_cluster_ids"]
        clusters = ", ".join(f"`{cluster_id}`" for cluster_id in [point["cluster_id"], *merged])
        lines += [
            "",
            f"## {point['rank']}. {point['label']}",
            "",
            f"Severity {point['severity']}/5 · {point['review_count']} reviews · "
            f"{point['negative_share']:.0%} negative · score {point['score']:g}",
            "",
            point["description"],
            "",
            f"- Why this severity: {point['severity_reason']}",
            f"- Products: {', '.join(point['products'])}",
            f"- Cluster: {clusters}" + (" (merged at the checkpoint)" if merged else ""),
            f"- {trend_line(point['trend'], export['trend_settings']['window_months'])}",
            f"- Reviews about switching products: {len(point['switching'])}",
            "",
            "Quotes:",
        ]
        for quote in point["quotes"]:
            lines += ["", *quote_lines(quote)]
    if export["switching_table"]:
        lines += [
            "",
            "## Switching",
            "",
            f"{export['switching_review_count']} reviews talk about switching products. "
            "Reasons are the model's short summaries of what the reviews say.",
            "",
            "| From | To | Reviews | Top reasons |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {_plain(row['from_product'] or 'not said')} | "
            f"{_plain(row['to_product'] or 'not said')} | {row['count']} | "
            f"{_plain('; '.join(row['reasons']) or 'none given')} |"
            for row in export["switching_table"]
        ]
    if export["junk_clusters"]:
        lines += ["", "## Groups that are not a pain point", ""]
        lines += [
            f"- `{junk['cluster_id']}` ({junk['review_count']} reviews): {junk['reason']}"
            for junk in export["junk_clusters"]
        ]
    return "\n".join(lines) + "\n"
