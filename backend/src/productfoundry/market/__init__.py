"""Market intelligence (ARCHITECTURE.md section 10): pricing, changelogs, traction.

This is not a pipeline stage. Each module takes adapters and stores as arguments,
like a stage does, and never touches run state.
"""

from productfoundry.market.market_panel import (
    assemble_full_market_panel,
    assemble_market_panel,
)

__all__ = [
    "assemble_full_market_panel",
    "assemble_market_panel",
]
