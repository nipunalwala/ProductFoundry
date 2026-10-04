"""Changelog tracking: collect what products ship, match it to a run, raise alerts."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator

from productfoundry.core.base import NonEmptyStr
from productfoundry.core.changelog import (
    BODY_CHARS,
    ChangelogAlerts,
    ChangelogItem,
    ChangelogMatch,
    ChangelogSource,
    ChangelogStore,
    ReleaseKind,
    build_alerts,
    match_problems,
    release_id,
)
from productfoundry.core.clock import utcnow
from productfoundry.core.competitors import CompetitorList
from productfoundry.core.errors import StageOutputInvalid
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.prd import Prd
from productfoundry.llm.types import Completer
from productfoundry.sources import SourceError
from productfoundry.sources.changelog import RawRelease, ReleaseSource

TASK = "changelog_matching"
PROMPT = (
    Path(__file__).parent.parent / "stages" / "prompts" / "changelog_matching_v1.md"
).read_text(encoding="utf-8")
BATCH_SIZE = 10  # release items per LLM call
PROMPT_BODY_CHARS = 800  # a longer note is cut for the prompt; the stored body is not


# Collecting


@dataclass(frozen=True)
class SourceOutcome:
    """How reading one tracked source went. A skipped source is reported, not an error."""

    source: ChangelogSource
    found: int = 0
    new: int = 0
    skipped: str | None = None


def to_item(source: ChangelogSource, raw: RawRelease, fetched_at: datetime) -> ChangelogItem:
    return ChangelogItem(
        id=release_id(str(source.kind), raw.source_item_id),
        product_id=source.product_id,
        source=source.kind,
        title=raw.title.strip()[:300] or "Untitled release",
        body=raw.body.strip()[:BODY_CHARS],
        version=raw.version,
        released_at=raw.released_at,
        url=raw.url if raw.url and raw.url.startswith(("http://", "https://")) else None,
        fetched_at=fetched_at,
    )


def collect(
    adapters: Mapping[str, ReleaseSource],
    store: ChangelogStore,
    now: Callable[[], datetime] = utcnow,
) -> list[SourceOutcome]:
    """Read every tracked source and store the release items that are new.

    One source that fails, or that robots.txt disallows, does not stop the others.
    """
    outcomes = []
    for source in store.sources():
        adapter = adapters.get(str(source.kind))
        if adapter is None:
            outcomes.append(SourceOutcome(source, skipped=f"no adapter for {source.kind}"))
            continue
        try:
            raw = adapter.fetch(source.target)
        except SourceError as exc:
            outcomes.append(SourceOutcome(source, skipped=str(exc)))
            continue
        fetched_at = now()
        new = store.add_items([to_item(source, release, fetched_at) for release in raw])
        outcomes.append(SourceOutcome(source, found=len(raw), new=new))
    return outcomes


# Matching


class ItemMatch(BaseModel):
    n: int
    kind: Literal["fix", "feature", "other"]
    cluster_ids: list[str] = []
    requirement_ids: list[str] = []
    reason: NonEmptyStr


class MatchBatch(BaseModel):
    matches: list[ItemMatch]


def batch_schema(
    count: int, cluster_ids: Sequence[str], requirement_ids: Sequence[str]
) -> type[MatchBatch]:
    """`MatchBatch` that answers every release once and cites only ids of this run.

    An invented cluster or requirement id fails validation, so the gateway asks
    again and falls back.
    """
    clusters, requirements = set(cluster_ids), set(requirement_ids)

    class GroundedMatchBatch(MatchBatch):
        @model_validator(mode="after")
        def _grounded(self) -> "GroundedMatchBatch":
            if sorted(match.n for match in self.matches) != list(range(1, count + 1)):
                raise ValueError(f"expected one entry for each of releases 1 to {count}")
            for match in self.matches:
                unknown = [c for c in match.cluster_ids if c not in clusters]
                unknown += [r for r in match.requirement_ids if r not in requirements]
                if unknown:
                    raise ValueError(
                        f"release {match.n} cites ids that are not in the run: {unknown}"
                    )
            return self

    GroundedMatchBatch.__name__ = MatchBatch.__name__
    return GroundedMatchBatch


def match_items(
    llm: Completer,
    items: Sequence[ChangelogItem],
    report: PainPointReport,
    prd: Prd,
    product_names: Mapping[str, str],
) -> list[ChangelogMatch]:
    """One gateway call per batch of release items. Every item gets a match, even an empty one."""
    points = [
        {"id": point.cluster_id, "label": point.label, "description": point.description}
        for point in report.pain_points
    ]
    requirements = [{"id": r.id, "statement": r.statement} for r in prd.requirements]
    cluster_ids = [point["id"] for point in points]
    requirement_ids = [requirement["id"] for requirement in requirements]
    matches = []
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start : start + BATCH_SIZE]
        payload = {
            "pain_points": points,
            "requirements": requirements,
            "releases": [
                {
                    "n": n,
                    "product": product_names.get(item.product_id, item.product_id),
                    "title": item.title,
                    "version": item.version,
                    "date": f"{item.released_at:%Y-%m-%d}" if item.released_at else None,
                    "notes": item.body[:PROMPT_BODY_CHARS],
                }
                for n, item in enumerate(batch, start=1)
            ],
        }
        messages = [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        answer = llm.complete(
            TASK, messages, batch_schema(len(batch), cluster_ids, requirement_ids)
        )
        for match in sorted(answer.matches, key=lambda match: match.n):
            matches.append(
                ChangelogMatch(
                    item_id=batch[match.n - 1].id,
                    kind=ReleaseKind(match.kind),
                    cluster_ids=list(dict.fromkeys(match.cluster_ids)),
                    requirement_ids=list(dict.fromkeys(match.requirement_ids)),
                    reason=match.reason,
                )
            )
    return matches


def run_products(competitors: CompetitorList) -> dict[str, str]:
    return {competitor.id: competitor.name for competitor in competitors.competitors}


def match_run(
    run_id: str,
    competitors: CompetitorList,
    report: PainPointReport,
    prd: Prd,
    *,
    llm: Completer,
    store: ChangelogStore,
) -> list[ChangelogMatch]:
    """Match the release items of the run's products that the run has not seen yet.

    Returns the new matches. Items matched earlier are not sent again.
    """
    names = run_products(competitors)
    known = {match.item_id for match in store.matches(run_id)}
    items = [item for item in store.items(list(names)) if item.id not in known]
    if not items:
        return []
    matches = match_items(llm, items, report, prd, names)
    problems = match_problems(matches, {item.id: item for item in items}, report, prd)
    if problems:
        raise StageOutputInvalid("the changelog matches are not grounded: " + "; ".join(problems))
    store.save_matches(run_id, matches)
    return matches


def run_alerts(
    run_id: str,
    competitors: CompetitorList,
    report: PainPointReport,
    prd: Prd,
    store: ChangelogStore,
) -> ChangelogAlerts:
    """The run's changelog alerts, from its stored matches. No LLM call.

    A match that cites a pain point or a requirement the run no longer has (the
    checkpoint was edited, the PRD rewritten) is an error, not a silent gap.
    """
    names = run_products(competitors)
    items = {item.id: item for item in store.items(list(names))}
    matches = [match for match in store.matches(run_id) if match.item_id in items]
    problems = match_problems(matches, items, report, prd)
    if problems:
        raise StageOutputInvalid(
            "the stored changelog matches no longer fit the run (match it again): "
            + "; ".join(problems)
        )
    return ChangelogAlerts(
        run_id=run_id,
        alerts=build_alerts(matches, items, names, report),
        items_matched=len(matches),
    )


def render_alerts(alerts: ChangelogAlerts) -> str:
    """The alerts as text for the terminal."""
    lines = [f"{alerts.items_matched} release items compared with run {alerts.run_id}"]
    if not alerts.alerts:
        lines.append("no alert")
    for alert in alerts.alerts:
        item = alert.item
        when = f"{item.released_at:%Y-%m-%d}" if item.released_at else "undated"
        lines.append("")
        if str(alert.kind) == "shipped_fix":
            fixed = ", ".join(alert.pain_point_labels)
            lines.append(f"SHIPPED FIX  {alert.product_name} shipped a fix for: {fixed}")
        else:
            lines.append(f"NEW FEATURE  {alert.product_name} shipped something not in our roadmap")
        lines.append(f"  {item.title} ({when}, {item.source}){'  ' + item.url if item.url else ''}")
        lines.append(f"  why: {alert.reason}")
        for cluster, follow in alert.follow_ups.items():
            if follow.share_before is None or follow.share_after is None:
                lines.append(f"  {cluster}: too few reviews around the release to see a trend")
                continue
            change = "" if follow.change is None else f" ({follow.change:+.0%})"
            lines.append(
                f"  {cluster}: {follow.share_before:.0%} of reviews in the {follow.months} months "
                f"before, {follow.share_after:.0%} after{change}"
            )
    return "\n".join(lines)
