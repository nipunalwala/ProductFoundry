from sqlalchemy import select

from productfoundry.core.pricing import PricingSnapshot
from productfoundry.storage.db import Sessions
from productfoundry.storage.models import PricingSnapshotRow, ProductRow


class PricingRepository:
    """Pricing snapshots on `pricing_snapshots`, append-only."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    def add(self, snapshot: PricingSnapshot, product_name: str) -> None:
        with self._sessions.begin() as session:
            # A product that no run has stored yet (it has no reviews) gets its row here.
            if session.get(ProductRow, snapshot.product_id) is None:
                session.add(ProductRow(id=snapshot.product_id, name=product_name, urls=[]))
                session.flush()
            session.add(
                PricingSnapshotRow(
                    product_id=snapshot.product_id,
                    url=snapshot.url,
                    fetched_at=snapshot.fetched_at,
                    text_hash=snapshot.text_hash,
                    schema_version=snapshot.schema_version,
                    snapshot=snapshot.model_dump(mode="json"),
                )
            )

    def latest(self, product_id: str, url: str | None = None) -> PricingSnapshot | None:
        statement = select(PricingSnapshotRow).where(PricingSnapshotRow.product_id == product_id)
        if url is not None:
            statement = statement.where(PricingSnapshotRow.url == url)
        statement = statement.order_by(
            PricingSnapshotRow.fetched_at.desc(), PricingSnapshotRow.id.desc()
        ).limit(1)
        with self._sessions() as session:
            row = session.scalars(statement).first()
            return None if row is None else PricingSnapshot.model_validate(row.snapshot)

    def history(self, product_id: str) -> list[PricingSnapshot]:
        statement = (
            select(PricingSnapshotRow)
            .where(PricingSnapshotRow.product_id == product_id)
            .order_by(PricingSnapshotRow.fetched_at, PricingSnapshotRow.id)
        )
        with self._sessions() as session:
            return [
                PricingSnapshot.model_validate(row.snapshot) for row in session.scalars(statement)
            ]
