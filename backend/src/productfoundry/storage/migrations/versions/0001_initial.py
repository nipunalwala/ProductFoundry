"""Runs, stage outputs, products, reviews, clusters and LLM bookkeeping.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "runs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("input", JSONB, nullable=False),
        sa.Column("seed", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("pause_reason", sa.Text),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
    )
    op.create_index("ix_runs_status", "runs", ["status"])

    op.create_table(
        "stage_outputs",
        sa.Column(
            "run_id", sa.Text, sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("schema_version", sa.Integer),
        sa.Column("output", JSONB),
        sa.Column("edited_output", JSONB),
        sa.Column("error", sa.Text),
        sa.Column("started_at", TIMESTAMP),
        sa.Column("finished_at", TIMESTAMP),
        sa.Column("approved_at", TIMESTAMP),
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("urls", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("google_play_id", sa.Text, unique=True),
        sa.Column("app_store_id", sa.Text, unique=True),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "reviews",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("product_id", sa.Text, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_review_id", sa.Text, nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("reviewed_at", TIMESTAMP, nullable=False),
        sa.Column("rating", sa.SmallInteger),
        sa.Column("language", sa.Text),
        sa.Column("sentiment", sa.Text),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector()),
        sa.Column("fetched_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source", "source_review_id"),
    )
    op.create_index("ix_reviews_product_id", "reviews", ["product_id"])
    op.create_index("ix_reviews_reviewed_at", "reviews", ["reviewed_at"])

    op.create_table(
        "clusters",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("negative_share", sa.Float, nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_clusters_run_id", "clusters", ["run_id"])

    op.create_table(
        "cluster_reviews",
        sa.Column(
            "cluster_id",
            sa.Text,
            sa.ForeignKey("clusters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("review_id", sa.Text, sa.ForeignKey("reviews.id"), primary_key=True),
        sa.Column("representative", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_cluster_reviews_review_id", "cluster_reviews", ["review_id"])

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("task", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("prompt_hash", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("cache_hit", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_llm_calls_run_id", "llm_calls", ["run_id"])
    op.create_index("ix_llm_calls_prompt_hash", "llm_calls", ["prompt_hash"])
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])

    op.create_table(
        "provider_usage",
        sa.Column("provider", sa.Text, primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("requests", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    for table in (
        "provider_usage",
        "llm_calls",
        "cluster_reviews",
        "clusters",
        "reviews",
        "stage_outputs",
        "products",
        "runs",
    ):
        op.drop_table(table)
