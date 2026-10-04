"""Pin the embedding dimension, record the model, and rank representative reviews.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No vector was stored before this migration, so the cast cannot fail.
    op.execute("ALTER TABLE reviews ALTER COLUMN embedding TYPE vector(384)")
    op.add_column("reviews", sa.Column("embedding_model", sa.Text))
    op.drop_column("cluster_reviews", "representative")
    op.add_column("cluster_reviews", sa.Column("representative_rank", sa.SmallInteger))


def downgrade() -> None:
    op.drop_column("cluster_reviews", "representative_rank")
    op.add_column(
        "cluster_reviews",
        sa.Column("representative", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.drop_column("reviews", "embedding_model")
    op.execute("ALTER TABLE reviews ALTER COLUMN embedding TYPE vector")
