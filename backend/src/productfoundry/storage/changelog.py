from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from productfoundry.core.changelog import ChangelogItem, ChangelogMatch, ChangelogSource
from productfoundry.storage.db import Sessions
from productfoundry.storage.models import (
    ChangelogItemRow,
    ChangelogMatchRow,
    ChangelogSourceRow,
    ProductRow,
)


class ChangelogRepository:
    """Tracked sources, release items and their matches to runs."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    def track(self, source: ChangelogSource, product_name: str) -> None:
        with self._sessions.begin() as session:
            # A product that no run has stored yet gets its row here.
            if session.get(ProductRow, source.product_id) is None:
                session.add(ProductRow(id=source.product_id, name=product_name, urls=[]))
                session.flush()
            session.execute(
                insert(ChangelogSourceRow)
                .values(product_id=source.product_id, kind=str(source.kind), target=source.target)
                .on_conflict_do_nothing()
            )

    def sources(self) -> list[ChangelogSource]:
        statement = select(ChangelogSourceRow).order_by(
            ChangelogSourceRow.product_id, ChangelogSourceRow.kind, ChangelogSourceRow.target
        )
        with self._sessions() as session:
            return [
                ChangelogSource(product_id=row.product_id, kind=row.kind, target=row.target)
                for row in session.scalars(statement)
            ]

    def product_names(self) -> dict[str, str]:
        statement = select(ProductRow.id, ProductRow.name).join(
            ChangelogSourceRow, ChangelogSourceRow.product_id == ProductRow.id
        )
        with self._sessions() as session:
            return {product: name for product, name in session.execute(statement)}

    def add_items(self, items: Sequence[ChangelogItem]) -> int:
        if not items:
            return 0
        rows = [item.model_dump(mode="python") for item in items]
        for row in rows:
            row["source"] = str(row["source"])
        statement = (
            insert(ChangelogItemRow)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(ChangelogItemRow.id)
        )
        with self._sessions.begin() as session:
            return len(session.execute(statement).all())

    def items(self, product_ids: Sequence[str]) -> list[ChangelogItem]:
        statement = (
            select(ChangelogItemRow)
            .where(ChangelogItemRow.product_id.in_(product_ids))
            .order_by(
                ChangelogItemRow.released_at.desc().nulls_last(),
                ChangelogItemRow.fetched_at.desc(),
                ChangelogItemRow.id,
            )
        )
        with self._sessions() as session:
            return [
                ChangelogItem(
                    id=row.id,
                    product_id=row.product_id,
                    source=row.source,
                    title=row.title,
                    body=row.body,
                    version=row.version,
                    released_at=row.released_at,
                    url=row.url,
                    fetched_at=row.fetched_at,
                )
                for row in session.scalars(statement)
            ]

    def save_matches(self, run_id: str, matches: Sequence[ChangelogMatch]) -> None:
        if not matches:
            return
        rows = [
            {"run_id": run_id, "item_id": match.item_id, "match": match.model_dump(mode="json")}
            for match in matches
        ]
        statement = insert(ChangelogMatchRow).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["run_id", "item_id"], set_={"match": statement.excluded.match}
        )
        with self._sessions.begin() as session:
            session.execute(statement)

    def matches(self, run_id: str) -> list[ChangelogMatch]:
        statement = (
            select(ChangelogMatchRow)
            .where(ChangelogMatchRow.run_id == run_id)
            .order_by(ChangelogMatchRow.item_id)
        )
        with self._sessions() as session:
            return [ChangelogMatch.model_validate(row.match) for row in session.scalars(statement)]

    def clear_matches(self, run_id: str) -> None:
        from sqlalchemy import delete

        with self._sessions.begin() as session:
            session.execute(delete(ChangelogMatchRow).where(ChangelogMatchRow.run_id == run_id))
