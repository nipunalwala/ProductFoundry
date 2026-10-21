"""Phase 21: Assemble the market opportunity panel from stored data."""

from collections.abc import Callable
from datetime import datetime

from productfoundry.core.changelog import ChangelogStore
from productfoundry.core.competitors import CompetitorList
from productfoundry.core.market_panel import (
    Availability,
    ChangelogSection,
    MarketPanel,
    PricingSection,
    SwitchingSection,
    TractionSection,
    build_differentiation,
    build_market_gaps,
    compute_combined_signals,
)
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.pricing import AlertStore, PricingStore
from productfoundry.core.traction import TractionStore


def _product_names(competitors: CompetitorList) -> dict[str, str]:
    return {competitor.id: competitor.name for competitor in competitors.competitors}


def assemble_market_panel(
    run_id: str,
    run_mode: str,
    incumbent_id: str | None,
    competitors: CompetitorList,
    report: PainPointReport,
    *,
    pricing_store: PricingStore,
    pricing_alerts: AlertStore | None,
    traction_store: TractionStore,
    changelog_store: ChangelogStore | None,
    now: Callable[[], datetime],
) -> MarketPanel:
    """Build the market panel for a run from all available stored data.

    Reads from stores; makes no external requests. Missing data is represented
    explicitly with Availability reasons.
    """
    names = _product_names(competitors)
    product_ids = list(names.keys())
    incumbent_name = names.get(incumbent_id) if incumbent_id else None

    # ----- Pricing section -----
    pricing_snapshots = []
    for product_id in product_ids:
        latest = pricing_store.latest(product_id)
        if latest:
            pricing_snapshots.append(latest)

    pricing_alerts_list = []
    if pricing_alerts:
        for product_id in product_ids:
            pricing_alerts_list.extend(pricing_alerts.list(product_id))
    # Sort newest first
    pricing_alerts_list.sort(key=lambda a: a.detected_at, reverse=True)

    tracked_pages = pricing_store.tracked()
    tracked_pages = [p for p in tracked_pages if p.product_id in product_ids]

    pricing_availability = None
    if not pricing_snapshots and not pricing_alerts_list:
        pricing_availability = Availability.NOT_COLLECTED

    pricing = PricingSection(
        snapshots=pricing_snapshots,
        alerts=pricing_alerts_list,
        tracked_pages=tracked_pages,
        availability=pricing_availability,
    )

    # ----- Traction section -----
    # Traction scores are computed on demand in the API layer where we have
    # access to ReviewStore
    for _product_id in product_ids:
        pass

    traction = TractionSection(
        scores=[],
        availability=Availability.NOT_COLLECTED,
    )

    # ----- Switching section -----
    switching = SwitchingSection(
        rows=report.switching_table,
        total_reviews=len(report.switching_reviews),
        availability=None if report.switching_table else Availability.NO_HISTORY,
    )

    # ----- Changelog section -----
    changelog_alerts: list = []
    items_matched = 0
    changelog_availability = None
    if changelog_store:
        # This requires the run's PRD and competitors, which we don't have here
        # The API layer will call run_alerts which does the matching
        changelog_availability = Availability.NOT_COLLECTED
    else:
        changelog_availability = Availability.NOT_COLLECTED

    changelog = ChangelogSection(
        alerts=changelog_alerts,
        items_matched=items_matched,
        availability=changelog_availability,
    )

    # ----- Market gaps -----
    # We need pricing alerts and changelog alerts for gap building
    # For now, build with what we have; the API layer will enhance
    market_gaps = build_market_gaps(
        competitors=competitors,
        pricing_alerts=pricing_alerts_list,
        changelog_alerts=changelog_alerts,
        report=report,
    )

    # ----- Combined signals -----
    combined_signals = compute_combined_signals(
        pricing_alerts=pricing_alerts_list,
        report=report,
        product_names=names,
    )

    # ----- Differentiation (mode 2) -----
    differentiation = build_differentiation(
        run_mode=run_mode,
        incumbent_id=incumbent_id,
        incumbent_name=incumbent_name,
        report=report,
        market_gaps=market_gaps,
    )

    return MarketPanel(
        run_id=run_id,
        mode=run_mode,  # type: ignore
        incumbent_id=incumbent_id,
        generated_at=now(),
        pricing=pricing,
        traction=traction,
        switching=switching,
        changelog=changelog,
        market_gaps=market_gaps,
        combined_signals=combined_signals,
        differentiation=differentiation,
    )


def assemble_full_market_panel(
    run_id: str,
    run_mode: str,
    incumbent_id: str | None,
    competitors: CompetitorList,
    report: PainPointReport,
    prd,  # Prd type, avoiding circular import
    *,
    pricing_store: PricingStore,
    pricing_alerts: AlertStore | None,
    traction_store: TractionStore,
    changelog_store: ChangelogStore | None,
    reviews_store,  # ReviewStore, avoiding circular import
    now: Callable[[], datetime] = datetime.now,
) -> MarketPanel:
    """Build the complete market panel including traction scores and changelog alerts.

    This version computes traction scores and fetches changelog alerts.
    """
    from productfoundry.market.changelog import run_alerts
    from productfoundry.market.traction import score_product

    names = _product_names(competitors)
    product_ids = list(names.keys())
    incumbent_name = names.get(incumbent_id) if incumbent_id else None

    # ----- Pricing section -----
    pricing_snapshots = []
    for product_id in product_ids:
        latest = pricing_store.latest(product_id)
        if latest:
            pricing_snapshots.append(latest)

    pricing_alerts_list = []
    if pricing_alerts:
        for product_id in product_ids:
            pricing_alerts_list.extend(pricing_alerts.list(product_id))
    pricing_alerts_list.sort(key=lambda a: a.detected_at, reverse=True)

    tracked_pages = pricing_store.tracked()
    tracked_pages = [p for p in tracked_pages if p.product_id in product_ids]

    pricing_availability = None
    if not pricing_snapshots and not pricing_alerts_list:
        pricing_availability = Availability.NOT_COLLECTED

    pricing = PricingSection(
        snapshots=pricing_snapshots,
        alerts=pricing_alerts_list,
        tracked_pages=tracked_pages,
        availability=pricing_availability,
    )

    # ----- Traction section -----
    traction_scores = []
    for product_id in product_ids:
        score = score_product(
            product_id,
            store=traction_store,
            reviews=reviews_store,
        )
        traction_scores.append(score)

    traction = TractionSection(
        scores=traction_scores,
        availability=None if traction_scores else Availability.NOT_COLLECTED,
    )

    # ----- Switching section -----
    switching = SwitchingSection(
        rows=report.switching_table,
        total_reviews=len(report.switching_reviews),
        availability=None if report.switching_table else Availability.NO_HISTORY,
    )

    # ----- Changelog section -----
    changelog_alerts_list = []
    items_matched = 0
    changelog_availability = None
    if changelog_store:
        try:
            alerts_result = run_alerts(
                run_id=run_id,
                competitors=competitors,
                report=report,
                prd=prd,
                store=changelog_store,
            )
            changelog_alerts_list = alerts_result.alerts
            items_matched = alerts_result.items_matched
            changelog_availability = None if changelog_alerts_list else Availability.NO_HISTORY
        except Exception:
            # If matching hasn't been done yet, it's not an error
            changelog_availability = Availability.NOT_COLLECTED
    else:
        changelog_availability = Availability.NOT_COLLECTED

    changelog = ChangelogSection(
        alerts=changelog_alerts_list,
        items_matched=items_matched,
        availability=changelog_availability,
    )

    # ----- Market gaps -----
    market_gaps = build_market_gaps(
        competitors=competitors,
        pricing_alerts=pricing_alerts_list,
        changelog_alerts=changelog_alerts_list,
        report=report,
    )

    # ----- Combined signals -----
    combined_signals = compute_combined_signals(
        pricing_alerts=pricing_alerts_list,
        report=report,
        product_names=names,
    )

    # ----- Differentiation (mode 2) -----
    differentiation = build_differentiation(
        run_mode=run_mode,
        incumbent_id=incumbent_id,
        incumbent_name=incumbent_name,
        report=report,
        market_gaps=market_gaps,
    )

    return MarketPanel(
        run_id=run_id,
        mode=run_mode,  # type: ignore
        incumbent_id=incumbent_id,
        generated_at=now(),
        pricing=pricing,
        traction=traction,
        switching=switching,
        changelog=changelog,
        market_gaps=market_gaps,
        combined_signals=combined_signals,
        differentiation=differentiation,
    )