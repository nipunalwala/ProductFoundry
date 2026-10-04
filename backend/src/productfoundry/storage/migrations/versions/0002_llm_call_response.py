"""Keep the validated response of an LLM call, so `llm_calls` also serves as the cache.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_calls", sa.Column("response", JSONB))


def downgrade() -> None:
    op.drop_column("llm_calls", "response")
