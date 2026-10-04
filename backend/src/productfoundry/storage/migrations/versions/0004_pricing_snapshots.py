"""Pricing snapshots.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pricing_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.Text, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text_hash", sa.Text, nullable=False),
        sa.Column("schema_version", sa.SmallInteger, nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
    )
    op.create_index("ix_pricing_snapshots_product_id", "pricing_snapshots", ["product_id"])
    op.create_index("ix_pricing_snapshots_fetched_at", "pricing_snapshots", ["fetched_at"])


def downgrade() -> None:
    op.drop_table("pricing_snapshots")
