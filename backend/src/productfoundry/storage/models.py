"""Tables of ARCHITECTURE.md section 9 that milestone 1 needs."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: DateTime(timezone=True), str: Text, dict[str, Any]: JSONB}


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(primary_key=True)
    input: Mapped[dict[str, Any]]
    seed: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(index=True)
    error: Mapped[str | None]
    pause_reason: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    stages: Mapped[list["StageOutputRow"]] = relationship(
        order_by="StageOutputRow.position", cascade="all, delete-orphan", lazy="selectin"
    )


class StageOutputRow(Base):
    __tablename__ = "stage_outputs"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str]
    schema_version: Mapped[int | None]
    output: Mapped[dict[str, Any] | None]
    edited_output: Mapped[dict[str, Any] | None]
    error: Mapped[str | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    approved_at: Mapped[datetime | None]


class ProductRow(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    urls: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    google_play_id: Mapped[str | None] = mapped_column(unique=True)
    app_store_id: Mapped[str | None] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ReviewRow(Base):
    """One review. There is deliberately no column for a username or a profile link."""

    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("source", "source_review_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    source: Mapped[str]
    source_review_id: Mapped[str]
    url: Mapped[str | None]
    reviewed_at: Mapped[datetime] = mapped_column(index=True)
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    language: Mapped[str | None]
    sentiment: Mapped[str | None]
    text: Mapped[str]
    # The dimension is fixed when the embedding model is pinned (phase 6).
    embedding: Mapped[Any | None] = mapped_column(Vector())
    fetched_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ClusterRow(Base):
    __tablename__ = "clusters"

    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    size: Mapped[int]
    negative_share: Mapped[float]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ClusterReviewRow(Base):
    __tablename__ = "cluster_reviews"

    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), primary_key=True
    )
    review_id: Mapped[str] = mapped_column(ForeignKey("reviews.id"), primary_key=True, index=True)
    representative: Mapped[bool] = mapped_column(server_default=text("false"))


class LlmCallRow(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), index=True
    )
    task: Mapped[str]
    provider: Mapped[str]
    model: Mapped[str]
    prompt_hash: Mapped[str] = mapped_column(index=True)
    outcome: Mapped[str]  # ok, or the kind of failure
    cache_hit: Mapped[bool] = mapped_column(server_default=text("false"))
    input_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    latency_ms: Mapped[int] = mapped_column(server_default=text("0"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class ProviderUsageRow(Base):
    __tablename__ = "provider_usage"

    provider: Mapped[str] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    requests: Mapped[int] = mapped_column(server_default=text("0"))
    tokens: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
