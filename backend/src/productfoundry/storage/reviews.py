from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import Select, literal_column, select
from sqlalchemy.dialects.postgresql import insert

from productfoundry.core.reviews import Review, ReviewSourceName
from productfoundry.storage.db import Sessions
from productfoundry.storage.models import ReviewRow

_COLUMNS = tuple(Review.model_fields)
# What a source may change when it returns a review again. Our own analysis
# (language, sentiment, embedding) and the id are kept.
_REFRESHED = ("url", "reviewed_at", "rating", "text")


class ReviewRepository:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    def upsert(self, reviews: Iterable[Review]) -> int:
        """Store reviews, one row per (source, source review id). Returns how many were new."""
        rows = {
            (review.source, review.source_review_id): review.model_dump(mode="json")
            | {"reviewed_at": review.reviewed_at}
            for review in reviews
        }
        if not rows:
            return 0
        statement = insert(ReviewRow).values(list(rows.values()))
        statement = statement.on_conflict_do_update(
            index_elements=[ReviewRow.source, ReviewRow.source_review_id],
            set_={column: statement.excluded[column] for column in _REFRESHED},
        ).returning(literal_column("(xmax = 0)"))
        with self._sessions.begin() as session:
            return sum(session.scalars(statement))

    def for_product(self, product_id: str) -> list[Review]:
        return self._fetch(select(ReviewRow).where(ReviewRow.product_id == product_id))

    def newer_than(
        self, product_id: str, after: datetime, source: ReviewSourceName | None = None
    ) -> list[Review]:
        statement = select(ReviewRow).where(
            ReviewRow.product_id == product_id, ReviewRow.reviewed_at > after
        )
        if source is not None:
            statement = statement.where(ReviewRow.source == source)
        return self._fetch(statement)

    def _fetch(self, statement: Select) -> list[Review]:
        statement = statement.order_by(ReviewRow.reviewed_at, ReviewRow.id)
        with self._sessions() as session:
            return [
                Review(
                    **{column: getattr(row, column) for column in _COLUMNS}
                    | {"reviewed_at": row.reviewed_at.astimezone(UTC)}
                )
                for row in session.scalars(statement)
            ]
