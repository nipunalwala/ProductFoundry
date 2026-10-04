"""A pricing page becomes a `PricingSnapshot`: fetch, extract through the gateway, check, store."""

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, PositiveInt, model_validator

from productfoundry.core.errors import ProductFoundryError, QuotaExhausted
from productfoundry.core.pricing import (
    AlertStore,
    Plan,
    PricingAlert,
    PricingSnapshot,
    PricingStore,
    diff_snapshots,
    pricing_problems,
)
from productfoundry.llm.types import Completer
from productfoundry.sources import SourceError
from productfoundry.sources.pricing import PageFetcher, PricingPage, read_page
from productfoundry.sources.robots import Robots

TASK = "pricing_extraction"
PROMPT = (
    Path(__file__).parent.parent / "stages" / "prompts" / "pricing_extraction_v1.md"
).read_text(encoding="utf-8")
# What is sent to the LLM. A longer page is cut; a price below the cut is then not on
# "the page" as far as the check is concerned, so it cannot be extracted by accident.
MAX_PAGE_CHARS = 14_000


class PricingDraft(BaseModel):
    """What the LLM returns."""

    plans: list[Plan] = []
    free_trial: bool = False
    trial_days: PositiveInt | None = None


def draft_schema(page_text: str) -> type[PricingDraft]:
    """`PricingDraft` whose every figure must be written in `page_text`.

    An invented price fails validation, so the gateway asks again and then
    falls back; it never reaches a snapshot.
    """

    class GroundedPricingDraft(PricingDraft):
        @model_validator(mode="after")
        def _grounded(self) -> "GroundedPricingDraft":
            names = [plan.name.casefold() for plan in self.plans]
            if len(set(names)) != len(names):
                raise ValueError("plan names must be unique")
            problems = pricing_problems(self.plans, self.trial_days, page_text)
            if problems:
                raise ValueError("; ".join(problems))
            return self

    GroundedPricingDraft.__name__ = PricingDraft.__name__
    return GroundedPricingDraft


def extract(
    llm: Completer, product_id: str, product_name: str, page: PricingPage
) -> PricingSnapshot:
    """The plans on the page. Raises `SourceError` when the page lists none."""
    text = page.text[:MAX_PAGE_CHARS]
    payload = {"product": product_name, "url": page.url, "text": text}
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    draft = llm.complete(TASK, messages, draft_schema(text))
    if not draft.plans:
        raise SourceError(f"{page.url} lists no plan with a public price")
    trial = draft.free_trial or draft.trial_days is not None
    return PricingSnapshot(
        product_id=product_id,
        url=page.url,
        fetched_at=page.fetched_at,
        text_hash=page.text_hash,
        plans=draft.plans,
        free_tier=any(plan.is_free for plan in draft.plans),
        free_trial=trial,
        trial_days=draft.trial_days,
    )


@dataclass(frozen=True)
class PricingOutcome:
    """A snapshot, or why there is none. A skipped page is reported, not an error."""

    url: str
    snapshot: PricingSnapshot | None = None
    skipped: str | None = None
    unchanged: bool = False  # the page text is what the latest snapshot was read from
    alert: PricingAlert | None = None  # what changed since the previous snapshot


def snapshot_pricing(
    product_id: str,
    product_name: str,
    url: str,
    *,
    fetcher: PageFetcher,
    robots: Robots,
    llm: Completer,
    store: PricingStore,
    alerts: AlertStore | None = None,
) -> PricingOutcome:
    """Fetch the page if robots.txt allows it, extract its plans and store the snapshot.

    A page whose text has not changed since the latest snapshot is not extracted
    again: no LLM call is made and nothing is stored. When the plans differ from
    the previous snapshot's, one alert listing the changes goes to `alerts`.
    """
    previous = store.latest(product_id, url)
    try:
        page = read_page(url, fetcher, robots)
        if previous is not None and previous.text_hash == page.text_hash:
            return PricingOutcome(url=url, snapshot=previous, unchanged=True)
        snapshot = extract(llm, product_id, product_name, page)
    except SourceError as exc:
        return PricingOutcome(url=url, skipped=str(exc))
    store.add(snapshot, product_name)
    alert = None
    changes = diff_snapshots(previous, snapshot) if previous is not None else []
    if changes:
        alert = PricingAlert(
            product_id=product_id,
            product_name=product_name,
            url=url,
            previous_fetched_at=previous.fetched_at,
            detected_at=snapshot.fetched_at,
            changes=changes,
        )
        if alerts is not None:
            alerts.add(alert)
    return PricingOutcome(url=url, snapshot=snapshot, alert=alert)


def refresh_tracked(
    *,
    fetcher: PageFetcher,
    robots: Robots,
    llm: Completer,
    store: PricingStore,
    alerts: AlertStore,
) -> list[PricingOutcome]:
    """The weekly pass: read every tracked pricing page again.

    One page that fails does not stop the others. When every LLM provider is
    out of quota the rest are skipped; next week's pass picks them up.
    """
    outcomes = []
    out_of_quota = False
    for page in store.tracked():
        if out_of_quota:
            outcomes.append(PricingOutcome(url=page.url, skipped="LLM quota exhausted"))
            continue
        try:
            outcome = snapshot_pricing(
                page.product_id,
                page.product_name,
                page.url,
                fetcher=fetcher,
                robots=robots,
                llm=llm,
                store=store,
                alerts=alerts,
            )
        except QuotaExhausted:
            out_of_quota = True
            outcome = PricingOutcome(url=page.url, skipped="LLM quota exhausted")
        except ProductFoundryError as exc:
            outcome = PricingOutcome(url=page.url, skipped=str(exc))
        outcomes.append(outcome)
    return outcomes


def describe_change(change) -> str:
    """One change as a sentence."""
    plan, kind = change.plan, str(change.kind)
    if kind in ("plan_added", "plan_removed"):
        return f"plan {plan} was {kind.removeprefix('plan_')}"
    if kind == "limits_changed":
        old = "; ".join(change.old_limits) or "none"
        new = "; ".join(change.new_limits) or "none"
        return f"{plan}: limits changed from [{old}] to [{new}]"
    period = str(change.period).replace("one_time", "purchase")
    which = f"{change.currency} per {period}" + (
        ", billed annually" if change.billed_annually else ""
    )
    if kind == "price_added":
        return f"{plan}: new price {change.new_amount} {which}"
    if kind == "price_removed":
        return f"{plan}: price {change.old_amount} {which} is gone"
    word = "rose" if kind == "price_increased" else "fell"
    return f"{plan}: price {word} from {change.old_amount} to {change.new_amount} {which}"


def render_alert(alert: PricingAlert) -> str:
    lines = [
        f"{alert.product_name}  {alert.url}",
        f"changed between {alert.previous_fetched_at:%Y-%m-%d} and {alert.detected_at:%Y-%m-%d}",
    ]
    lines.extend(f"  {describe_change(change)}" for change in alert.changes)
    return "\n".join(lines)


def render_snapshot(snapshot: PricingSnapshot, product_name: str) -> str:
    """The snapshot as text for the terminal."""
    lines = [
        f"{product_name}  {snapshot.url}",
        f"fetched {snapshot.fetched_at:%Y-%m-%d %H:%M} UTC, page hash {snapshot.text_hash[:12]}",
    ]
    for plan in snapshot.plans:
        if plan.is_free:
            cost = "free"
        elif plan.prices:
            cost = ", ".join(
                f"{price.currency} {price.amount} per {'seat per ' if price.per_seat else ''}"
                f"{str(price.period).replace('one_time', 'purchase')}"
                f"{' (billed annually)' if price.billed_annually else ''}"
                for price in plan.prices
            )
        else:
            cost = "contact sales"
        lines.append(f"  {plan.name}: {cost}")
        lines.extend(f"    limit: {limit}" for limit in plan.limits)
        lines.extend(f"    {feature}" for feature in plan.features)
    trial = "no"
    if snapshot.free_trial:
        trial = f"{snapshot.trial_days} days" if snapshot.trial_days else "yes"
    lines.append(f"free tier: {'yes' if snapshot.free_tier else 'no'}, free trial: {trial}")
    return "\n".join(lines)
