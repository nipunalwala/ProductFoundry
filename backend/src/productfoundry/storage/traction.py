from collections.abc import Sequence

from sqlalchemy import select

from productfoundry.core.traction import Observation, SignalKind, TractionTarget, targets_from
from productfoundry.storage.db import Sessions
from productfoundry.storage.models import ProductRow, TractionSignalRow


def _observation(row: TractionSignalRow) -> Observation:
    return Observation(
        product_id=row.product_id,
        signal=row.signal,
        value=row.value,
        source=row.source,
        observed_at=row.observed_at,
        detail=row.detail,
    )


class TractionRepository:
    """Traction signals on `traction_signals`, append-only."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    def add(self, observations: Sequence[Observation], product_name: str) -> None:
        with self._sessions.begin() as session:
            for product in {observation.product_id for observation in observations}:
                # A product that no run has stored yet gets its row here.
                if session.get(ProductRow, product) is None:
                    session.add(ProductRow(id=product, name=product_name, urls=[]))
            session.flush()
            session.add_all(
                TractionSignalRow(
                    product_id=o.product_id,
                    signal=str(o.signal),
                    value=o.value,
                    source=o.source,
                    observed_at=o.observed_at,
                    detail=o.detail,
                )
                for o in observations
            )

    def history(self, product_id: str, signal: SignalKind) -> list[Observation]:
        statement = (
            select(TractionSignalRow)
            .where(TractionSignalRow.product_id == product_id)
            .where(TractionSignalRow.signal == str(signal))
            .order_by(TractionSignalRow.observed_at, TractionSignalRow.id)
        )
        with self._sessions() as session:
            return [_observation(row) for row in session.scalars(statement)]

    def targets(self) -> list[TractionTarget]:
        with self._sessions() as session:
            rows = session.scalars(select(TractionSignalRow)).all()
            names = dict(session.execute(select(ProductRow.id, ProductRow.name)).all())
            return targets_from([_observation(row) for row in rows], names)
