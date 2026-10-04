"""Pricing alerts.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pricing_alerts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.Text, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("alert", JSONB, nullable=False),
    )
    op.create_index("ix_pricing_alerts_product_id", "pricing_alerts", ["product_id"])
    op.create_index("ix_pricing_alerts_detected_at", "pricing_alerts", ["detected_at"])


def downgrade() -> None:
    op.drop_table("pricing_alerts")
