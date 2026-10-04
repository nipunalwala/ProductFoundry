"""Changelog sources, items and matches.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TIME = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "changelog_sources",
        sa.Column("product_id", sa.Text, sa.ForeignKey("products.id"), primary_key=True),
        sa.Column("kind", sa.Text, primary_key=True),
        sa.Column("target", sa.Text, primary_key=True),
        sa.Column("created_at", TIME, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "changelog_items",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("product_id", sa.Text, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("version", sa.Text),
        sa.Column("released_at", TIME),
        sa.Column("url", sa.Text),
        sa.Column("fetched_at", TIME, nullable=False),
    )
    op.create_index("ix_changelog_items_product_id", "changelog_items", ["product_id"])
    op.create_index("ix_changelog_items_released_at", "changelog_items", ["released_at"])
    op.create_table(
        "changelog_matches",
        sa.Column(
            "run_id", sa.Text, sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "item_id",
            sa.Text,
            sa.ForeignKey("changelog_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("match", JSONB, nullable=False),
        sa.Column("created_at", TIME, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("changelog_matches")
    op.drop_table("changelog_items")
    op.drop_table("changelog_sources")
