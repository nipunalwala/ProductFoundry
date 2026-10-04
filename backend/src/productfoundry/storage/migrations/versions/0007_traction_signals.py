"""Traction signals.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "traction_signals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.Text, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("signal", sa.Text, nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_traction_signals_product_id", "traction_signals", ["product_id"])
    op.create_index("ix_traction_signals_observed_at", "traction_signals", ["observed_at"])


def downgrade() -> None:
    op.drop_table("traction_signals")
