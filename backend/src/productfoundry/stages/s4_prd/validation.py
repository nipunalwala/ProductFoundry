"""The grounding check: every requirement cites evidence that exists in this run."""

from productfoundry.core.competitors import CompetitorList
from productfoundry.core.errors import StageOutputInvalid
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.prd import MarketGap, Prd


def _collect_all_gaps(
    competitors: CompetitorList,
    report: PainPointReport,
    pricing_alerts: list | None = None,
    changelog_alerts: list | None = None,
) -> dict[str, MarketGap]:
    """Collect all possible market gaps for validation.

    This mirrors the logic in the PRD stage and market panel.
    """
    gaps = {}

    # 1. Competitor-fact gaps (existing)
    from productfoundry.core.prd import market_gaps as core_market_gaps

    for gap in core_market_gaps(competitors):
        gaps[gap.id] = gap

    # 2. Pricing gaps from alerts
    if pricing_alerts:
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
                    gaps[gap_id] = MarketGap(
                        id=gap_id,
                        product_id=alert.product_id,
                        product_name=alert.product_name,
                        fact=" ".join(fact_parts),
                    )

    # 3. Switching gaps from pain point report
    for row in report.switching_table:
        if row.from_product and row.to_product:
            gap_id = f"gap_switching_{row.from_product}_{row.to_product}_{row.count}"
            gaps[gap_id] = MarketGap(
                id=gap_id,
                product_id=row.from_product,
                product_name=row.from_product,
                fact=f"Users switching from {row.from_product} to {row.to_product}: "
                f"{row.count} reviews cite {', '.join(row.reasons[:3])}",
            )

    # 4. Changelog gaps from alerts
    if changelog_alerts:
        for alert in changelog_alerts:
            if alert.kind == "shipped_fix":
                for cluster_id in alert.cluster_ids:
                    gap_id = f"gap_changelog_{alert.item.id}_{cluster_id}"
                    gaps[gap_id] = MarketGap(
                        id=gap_id,
                        product_id=alert.item.product_id,
                        product_name=alert.product_name,
                        fact=(
                            f"Shipped a fix for '{alert.pain_point_labels[0]}' "
                            f"in {alert.item.title} ({alert.item.version or 'undated'})"
                        ),
                    )
            elif alert.kind == "unplanned_feature":
                gap_id = f"gap_changelog_{alert.item.id}_unplanned"
                gaps[gap_id] = MarketGap(
                    id=gap_id,
                    product_id=alert.item.product_id,
                    product_name=alert.product_name,
                    fact=(
                        f"Shipped unplanned feature: {alert.item.title} "
                        f"({alert.item.version or 'undated'}) — {alert.reason}"
                    ),
                )

    return gaps


def grounding_problems(
    prd: Prd,
    report: PainPointReport,
    competitors: CompetitorList,
    pricing_alerts: list | None = None,
    changelog_alerts: list | None = None,
) -> list[str]:
    """Every citation in the PRD that the run's approved pain points and competitors do not back.

    A requirement may cite the cluster of an approved pain point, or a market
    gap (competitor fact, pricing gap, switching gap, or changelog gap).
    """
    clusters = {point.cluster_id for point in report.pain_points}
    gaps = _collect_all_gaps(competitors, report, pricing_alerts, changelog_alerts)
    problems = []
    for requirement in prd.requirements:
        unknown = [e for e in requirement.evidence if e not in clusters and e not in gaps]
        if unknown:
            problems.append(
                f"{requirement.id} cites evidence that is not in this run: {', '.join(unknown)}"
            )
    for gap in prd.market_gaps:
        if gaps.get(gap.id) != gap:
            problems.append(f"market gap {gap.id} is not a valid gap of this run")
    return problems


def check_prd(
    prd: Prd,
    report: PainPointReport,
    competitors: CompetitorList,
    pricing_alerts: list | None = None,
    changelog_alerts: list | None = None,
) -> None:
    problems = grounding_problems(prd, report, competitors, pricing_alerts, changelog_alerts)
    if problems:
        raise StageOutputInvalid("the PRD is not grounded: " + "; ".join(problems))
