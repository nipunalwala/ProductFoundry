"""Schemas, ids, errors and clock. No I/O, and no imports from the rest of the project."""

from productfoundry.core.market_panel import (
    Availability,
    ChangelogSection,
    CombinedSignal,
    Confidence,
    DifferentiationOpportunity,
    MarketGapWithEvidence,
    MarketPanel,
    PricingSection,
    SwitchingSection,
    TractionSection,
    build_differentiation,
    build_market_gaps,
    compute_combined_signals,
)

__all__ = [
    "Availability",
    "ChangelogSection",
    "CombinedSignal",
    "Confidence",
    "DifferentiationOpportunity",
    "MarketGapWithEvidence",
    "MarketPanel",
    "PricingSection",
    "SwitchingSection",
    "TractionSection",
    "build_differentiation",
    "build_market_gaps",
    "compute_combined_signals",
]
