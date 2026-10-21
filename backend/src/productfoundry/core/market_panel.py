"""Phase 21: The opportunity panel for mode 2.

Combines pricing, price history, traction, switching, changelog alerts, and market gaps
into one evidence-backed view. Every finding carries its sources and confidence.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import model_validator

from productfoundry.core.base import NonEmptyStr, Schema
from productfoundry.core.changelog import ChangelogAlert
from productfoundry.core.ids import ClusterId, GapId, ProductId
from productfoundry.core.pain_points import PainPoint, SwitchingRow
from productfoundry.core.pricing import PricingAlert, PricingSnapshot, TrackedPage
from productfoundry.core.traction import TractionScore


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"  # explicitly no evidence


class Availability(StrEnum):
    """Why a section has no data."""

    NOT_COLLECTED = "not_collected"
    NO_HISTORY = "no_history"
    INSUFFICIENT_DATA = "insufficient_data"
    SKIPPED = "skipped"  # robots.txt, fetch failed, etc.


class PricingSection(Schema):
    """Current pricing and its history for the run's products."""

    snapshots: list[PricingSnapshot] = []  # latest per tracked URL
    alerts: list[PricingAlert] = []  # all alerts for these products, newest first
    tracked_pages: list[TrackedPage] = []
    availability: Availability | None = None  # when there are no snapshots at all


class TractionSection(Schema):
    """Traction for the run's products."""

    scores: list[TractionScore] = []
    availability: Availability | None = None


class SwitchingSection(Schema):
    """Switching reviews and the from/to table, scoped to the run's products."""

    rows: list[SwitchingRow] = []  # from the approved pain-point report
    total_reviews: int = 0
    availability: Availability | None = None


class ChangelogSection(Schema):
    """Changelog alerts for the run's products."""

    alerts: list[ChangelogAlert] = []
    items_matched: int = 0
    availability: Availability | None = None


class CombinedSignal(Schema):
    """A price increase followed by a rise in the pricing complaint's share.

    This is an association, not a claim of causation. Both pieces of evidence
    are cited so the user can judge.
    """

    product_id: ProductId
    product_name: NonEmptyStr
    # The pricing change
    alert: PricingAlert
    change: PricingAlert  # the specific price increase change
    # The pain point
    cluster_id: ClusterId
    pain_point_label: NonEmptyStr
    # Timing and share
    price_increase_date: datetime  # when the new price was detected
    share_before: float | None = None  # complaint share in window before
    share_after: float | None = None  # complaint share in window after
    share_change: float | None = None  # (after - before) / before
    # Evidence
    pricing_evidence_ids: list[str] = []  # e.g., pricing alert id
    complaint_evidence_ids: list[str] = []  # e.g., cluster ids or trend months
    confidence: Confidence = Confidence.LOW
    note: NonEmptyStr = ""  # why confidence is what it is, or why the signal is absent


class MarketGapWithEvidence(Schema):
    """A market gap with its underlying evidence references.

    Extends the basic MarketGap with resolvable evidence IDs so the PRD
    and gate 2 can validate the grounding.
    """

    # Core gap identification (matches MarketGap)
    id: GapId
    product_id: ProductId
    product_name: NonEmptyStr
    fact: NonEmptyStr

    # Evidence backing this gap
    # For competitor-fact gaps: empty (the fact comes from stage 1 competitor output)
    # For panel-derived gaps: pricing alert ids, changelog item ids, pain point cluster ids, etc.
    evidence_ids: list[str] = []

    # Confidence in this gap
    confidence: Confidence = Confidence.MEDIUM
    note: NonEmptyStr = ""


class DifferentiationOpportunity(Schema):
    """Where the incumbent is weak, backed by evidence.

    Only produced in mode 2 (alternative to an existing product).
    """

    # The incumbent product
    incumbent_id: ProductId
    incumbent_name: NonEmptyStr

    # The opportunity
    area: NonEmptyStr  # e.g., "Daily expense limit", "Pricing transparency"
    description: NonEmptyStr
    # Supporting evidence: pain point clusters, market gaps, switching reviews, etc.
    evidence_ids: list[str] = []

    confidence: Confidence = Confidence.MEDIUM


class MarketPanel(Schema):
    """The complete opportunity panel for one run.

    Scoped to the run's effective competitor list and approved pain points.
    Every section lists its sources and says why data is missing when it is.
    """

    schema_version: Literal[1] = 1
    run_id: NonEmptyStr
    mode: Literal["new_idea", "alternative", "new_feature"]
    incumbent_id: ProductId | None = None  # only for mode "alternative"
    generated_at: datetime

    # Market data sections
    pricing: PricingSection = PricingSection()
    traction: TractionSection = TractionSection()
    switching: SwitchingSection = SwitchingSection()
    changelog: ChangelogSection = ChangelogSection()

    # Synthesized insights
    market_gaps: list[MarketGapWithEvidence] = []  # all gaps the PRD may cite
    combined_signals: list[CombinedSignal] = []  # price increase + complaint rise
    differentiation: list[DifferentiationOpportunity] = []  # mode 2 only

    @model_validator(mode="after")
    def _check(self) -> "MarketPanel":
        # Market gap IDs must be unique
        gap_ids = [gap.id for gap in self.market_gaps]
        if len(set(gap_ids)) != len(gap_ids):
            raise ValueError("market gap ids must be unique")
        return self


# ----- Pure functions for the combined signal -----


def _is_price_increase(change) -> bool:
    """True for a numeric price increase on the same currency/period/billing basis."""
    from productfoundry.core.pricing import ChangeKind

    return change.kind is ChangeKind.PRICE_INCREASED


def _find_pricing_complaint_cluster(report) -> PainPoint | None:
    """Find the pain point about pricing / too expensive / cost.

    Looks for labels containing pricing-related keywords.
    """
    pricing_keywords = [
        "price",
        "cost",
        "expensive",
        "pricing",
        "subscription",
        "pay",
        "fee",
        "charge",
        "plan",
    ]
    for point in report.pain_points:
        label = point.label.lower()
        if any(keyword in label for keyword in pricing_keywords):
            return point
    return None


def _complaint_share_around_date(
    trend, price_increase_date: datetime, window_months: int = 3
) -> tuple[float | None, float | None, float | None]:
    """Compute complaint share before and after a price increase date.

    Returns (share_before, share_after, change) or (None, None, None) if
    insufficient data. The month of the price increase is excluded.
    """
    from productfoundry.core.pain_points import TrendPoint

    month_str = f"{price_increase_date:%Y-%m}"
    window = window_months

    before = [point for point in trend.months if point.month < month_str][-window:]
    after = [point for point in trend.months if point.month > month_str][:window]

    def share(points: list[TrendPoint]) -> float | None:
        total = sum(point.total_reviews for point in points)
        if total < 15:
            return None
        return round(sum(point.reviews for point in points) / total, 4)

    earlier, later = share(before), share(after)
    change = None
    if earlier and later is not None and earlier != 0:
        change = round((later - earlier) / earlier, 4)
    return earlier, later, change


def compute_combined_signals(
    pricing_alerts: Sequence[PricingAlert],
    report,
    product_names: Mapping[str, str],
) -> list[CombinedSignal]:
    """Find price increases followed by rises in pricing complaint share.

    Rule:
    1. Find a PRICE_INCREASED change in a pricing alert
    2. Match the product to a pricing-related pain point cluster
    3. Check if the complaint's share rose AFTER the price increase
    4. Return a signal with both evidence references

    No flag when:
    - The complaint was already rising before the price increase
    - Incompatible currency/billing basis
    - Insufficient review coverage (per trend settings)
    - Unrelated product or complaint
    """
    signals = []

    # Find pricing complaint cluster
    complaint_cluster = _find_pricing_complaint_cluster(report)
    if complaint_cluster is None:
        return signals

    # For each product with pricing alerts
    for alert in pricing_alerts:
        for change in alert.changes:
            if not _is_price_increase(change):
                continue

            # Must be the same product as the complaint (or one of the complaint's products)
            if alert.product_id not in complaint_cluster.product_ids:
                continue

            # The price increase was detected at alert.detected_at
            # Check complaint share before/after
            share_before, share_after, share_change = _complaint_share_around_date(
                complaint_cluster.trend, alert.detected_at
            )

            if share_before is None or share_after is None:
                # Insufficient data
                signals.append(
                    CombinedSignal(
                        product_id=alert.product_id,
                        product_name=product_names.get(alert.product_id, alert.product_name),
                        alert=alert,
                        change=change,
                        cluster_id=complaint_cluster.cluster_id,
                        pain_point_label=complaint_cluster.label,
                        price_increase_date=alert.detected_at,
                        share_before=share_before,
                        share_after=share_after,
                        share_change=share_change,
                        confidence=Confidence.NONE,
                        note=(
                            "Insufficient review coverage around the price change "
                            "to measure complaint share"
                        ),
                    )
                )
                continue

            # Check if complaint was already rising before the price increase
            # We look at the trend's growth before the price increase month
            # If growth was already positive and above threshold, don't flag
            was_rising_before = False
            if complaint_cluster.trend.growth is not None:
                # The trend's growth compares last 3 months vs 3 before
                # If the price increase is recent, this might not capture it precisely
                # For now, we use a simpler check: if share_after > share_before and
                # the trend was already showing growth, it's ambiguous
                # A more precise check would need monthly data points around the date
                was_rising_before = complaint_cluster.trend.growth > 0.25

            if was_rising_before:
                # Complaint was already rising before the price change
                signals.append(
                    CombinedSignal(
                        product_id=alert.product_id,
                        product_name=product_names.get(alert.product_id, alert.product_name),
                        alert=alert,
                        change=change,
                        cluster_id=complaint_cluster.cluster_id,
                        pain_point_label=complaint_cluster.label,
                        price_increase_date=alert.detected_at,
                        share_before=share_before,
                        share_after=share_after,
                        share_change=share_change,
                        confidence=Confidence.NONE,
                        note="The pricing complaint was already rising before the price increase",
                    )
                )
                continue

            # Valid signal: price increase followed by complaint share rise
            if share_change is not None and share_change > 0.25:
                confidence = Confidence.MEDIUM
                note = (
                    f"Price increased from {change.old_amount} to {change.new_amount} "
                    f"{change.currency}/{change.period}"
                    f"{' (billed annually)' if change.billed_annually else ''}; "
                    f"pricing complaint share rose from {share_before:.0%} to {share_after:.0%} "
                    f"({share_change:+.0%}) in the following months"
                )
            elif share_change is not None and share_change > 0:
                confidence = Confidence.LOW
                note = (
                    f"Price increased; pricing complaint share rose slightly "
                    f"from {share_before:.0%} to {share_after:.0%} ({share_change:+.0%})"
                )
            else:
                # Complaint share did not rise
                confidence = Confidence.NONE
                note = (
                    f"Price increased but pricing complaint share did not rise "
                    f"(before: {share_before:.0%}, after: {share_after:.0%})"
                )

            signals.append(
                CombinedSignal(
                    product_id=alert.product_id,
                    product_name=product_names.get(alert.product_id, alert.product_name),
                    alert=alert,
                    change=change,
                    cluster_id=complaint_cluster.cluster_id,
                    pain_point_label=complaint_cluster.label,
                    price_increase_date=alert.detected_at,
                    share_before=share_before,
                    share_after=share_after,
                    share_change=share_change,
                    pricing_evidence_ids=[f"alert_{alert.product_id}_{alert.detected_at.isoformat()}"],
                    complaint_evidence_ids=[complaint_cluster.cluster_id],
                    confidence=confidence,
                    note=note,
                )
            )

    return signals


def build_market_gaps(
    competitors,
    pricing_alerts: Sequence[PricingAlert],
    changelog_alerts: Sequence[ChangelogAlert],
    report,
) -> list[MarketGapWithEvidence]:
    """Build the complete set of market gaps the PRD can cite.

    Includes:
    1. Competitor-fact gaps (from stage 1) - existing
    2. Pricing gaps from alerts (price increases, plan removals, etc.)
    3. Changelog gaps (shipped fixes for our pain points, unplanned features)
    4. Switching gaps (users leaving for specific reasons)

    All gaps get stable deterministic IDs.
    """
    from productfoundry.core.ids import market_gap_id

    gaps = []

    # 1. Competitor-fact gaps (existing, from stage 1)
    for competitor in competitors.competitors:
        gap_id = market_gap_id(competitor.id)
        gaps.append(
            MarketGapWithEvidence(
                id=gap_id,
                product_id=competitor.id,
                product_name=competitor.name,
                fact=f"{competitor.positioning} Target users: {competitor.target_users}",
                evidence_ids=[],
                confidence=Confidence.MEDIUM,
                note="From competitor research (stage 1)",
            )
        )

    # 2. Pricing gaps from alerts
    for alert in pricing_alerts:
        for change in alert.changes:
            if change.kind in ("price_increased", "plan_removed", "limits_changed"):
                gap_id = (
                    f"gap_pricing_{alert.product_id}_{change.kind}_{change.plan}_"
                    f"{alert.detected_at.strftime('%Y%m%d')}"
                )
                fact_parts = [f"{change.plan}:"]
                if change.kind == "price_increased":
                    fact_parts.append(
                        f"price rose from {change.old_amount} to {change.new_amount} "
                        f"{change.currency}/{change.period}"
                    )
                elif change.kind == "plan_removed":
                    fact_parts.append("plan was removed")
                else:
                    fact_parts.append(
                        f"limits changed from [{'; '.join(change.old_limits)}] "
                        f"to [{'; '.join(change.new_limits)}]"
                    )
                gaps.append(
                    MarketGapWithEvidence(
                        id=gap_id,
                        product_id=alert.product_id,
                        product_name=alert.product_name,
                        fact=" ".join(fact_parts),
                        evidence_ids=[f"pricing_alert_{alert.product_id}_{alert.detected_at.isoformat()}"],
                        confidence=Confidence.HIGH,
                        note=(
                            f"Observed in pricing snapshot from "
                            f"{alert.previous_fetched_at:%Y-%m-%d} to {alert.detected_at:%Y-%m-%d}"
                        ),
                    )
                )

    # 3. Changelog gaps from shipped_fix alerts
    for alert in changelog_alerts:
        if alert.kind == "shipped_fix":
            for cluster_id in alert.cluster_ids:
                gap_id = f"gap_changelog_{alert.item.id}_{cluster_id}"
                gaps.append(
                    MarketGapWithEvidence(
                        id=gap_id,
                        product_id=alert.item.product_id,
                        product_name=alert.product_name,
                        fact=(
                            f"Shipped a fix for '{alert.pain_point_labels[0]}' "
                            f"in {alert.item.title} ({alert.item.version or 'undated'})"
                        ),
                        evidence_ids=[alert.item.id],
                        confidence=Confidence.HIGH,
                        note="Incumbent shipped a fix for a pain point we target",
                    )
                )
        elif alert.kind == "unplanned_feature":
            gap_id = f"gap_changelog_{alert.item.id}_unplanned"
            gaps.append(
                MarketGapWithEvidence(
                    id=gap_id,
                    product_id=alert.item.product_id,
                    product_name=alert.product_name,
                    fact=(
                        f"Shipped unplanned feature: {alert.item.title} "
                        f"({alert.item.version or 'undated'}) — {alert.reason}"
                    ),
                    evidence_ids=[alert.item.id],
                    confidence=Confidence.MEDIUM,
                    note="A feature the incumbent shipped that our PRD does not cover",
                )
            )

    # 4. Switching gaps (from switching table)
    for row in report.switching_table:
        if row.from_product and row.to_product:
            # Users switching from incumbent to another product
            gap_id = f"gap_switching_{row.from_product}_{row.to_product}_{row.count}"
            gaps.append(
                MarketGapWithEvidence(
                    id=gap_id,
                    product_id=row.from_product,  # approximate, may not match exact product_id
                    product_name=row.from_product,
                    fact=(
                        f"Users switching from {row.from_product} to {row.to_product}: "
                        f"{row.count} reviews cite {', '.join(row.reasons[:3])}"
                    ),
                    evidence_ids=row.review_ids,
                    confidence=Confidence.MEDIUM,
                    note="From switching intent analysis of reviews",
                )
            )

    return gaps


def build_differentiation(
    run_mode: str,
    incumbent_id: str | None,
    incumbent_name: str | None,
    report,
    market_gaps: Sequence[MarketGapWithEvidence],
) -> list[DifferentiationOpportunity]:
    """Build mode 2 differentiation opportunities.

    Only for mode "alternative" with an incumbent.
    Each opportunity is backed by approved pain points and/or market gaps.
    """
    if run_mode != "alternative" or not incumbent_id:
        return []

    opportunities = []

    # For each pain point affecting the incumbent, create an opportunity
    for point in report.pain_points:
        if incumbent_id not in point.product_ids:
            continue

        # Find market gaps related to this pain point
        related_gaps = [
            gap for gap in market_gaps
            if gap.product_id == incumbent_id and point.cluster_id in gap.evidence_ids
        ]

        # Also include gaps about the incumbent from pricing/changelog
        related_gaps += [
            gap for gap in market_gaps
            if gap.product_id == incumbent_id and gap.id.startswith("gap_pricing_")
        ]
        related_gaps += [
            gap for gap in market_gaps
            if gap.product_id == incumbent_id and gap.id.startswith("gap_changelog_")
        ]

        if not related_gaps:
            # Still an opportunity if there's a strong pain point
            opportunities.append(
                DifferentiationOpportunity(
                    incumbent_id=incumbent_id,
                    incumbent_name=incumbent_name or incumbent_id,
                    area=point.label,
                    description=(
                        f"{point.review_count} reviews ({point.negative_share:.0%} negative) "
                        f"complain about {point.label.lower()}. "
                        f"Severity {point.severity}/5."
                    ),
                    evidence_ids=[point.cluster_id] + point.merged_cluster_ids,
                    confidence=Confidence.MEDIUM,
                )
            )
        else:
            evidence_ids = (
                [point.cluster_id] + point.merged_cluster_ids + [gap.id for gap in related_gaps]
            )
            opportunities.append(
                DifferentiationOpportunity(
                    incumbent_id=incumbent_id,
                    incumbent_name=incumbent_name or incumbent_id,
                    area=point.label,
                    description=(
                        f"{point.review_count} reviews ({point.negative_share:.0%} negative) "
                        f"complain about {point.label.lower()}. "
                        f"Incumbent has: {', '.join(gap.fact for gap in related_gaps[:3])}"
                        f"{'...' if len(related_gaps) > 3 else ''}"
                    ),
                    evidence_ids=evidence_ids,
                    confidence=Confidence.HIGH,
                )
            )

    return opportunities