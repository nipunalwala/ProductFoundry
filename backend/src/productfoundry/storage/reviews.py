from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from sqlalchemy import Select, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from productfoundry.core.competitors import Competitor
from productfoundry.core.reviews import Review, ReviewSourceName, Sentiment
from productfoundry.storage.db import Sessions
from productfoundry.storage.models import ProductRow, ReviewRow

_CHUNK = 1000  # rows per INSERT, well under the driver's parameter limit
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
        values = list(rows.values())
        new = 0
        with self._sessions.begin() as session:
            for start in range(0, len(values), _CHUNK):
                statement = insert(ReviewRow).values(values[start : start + _CHUNK])
                statement = statement.on_conflict_do_update(
                    index_elements=[ReviewRow.source, ReviewRow.source_review_id],
                    set_={column: statement.excluded[column] for column in _REFRESHED},
                ).returning(literal_column("(xmax = 0)"))
                new += sum(session.scalars(statement))
        return new

    def ensure_product(self, competitor: Competitor) -> str:
        store_ids = competitor.store_ids
        same_app = [ProductRow.id == competitor.id]
        if store_ids.google_play:
            same_app.append(ProductRow.google_play_id == store_ids.google_play)
        if store_ids.app_store:
            same_app.append(ProductRow.app_store_id == store_ids.app_store)
        with self._sessions.begin() as session:
            known = session.scalars(select(ProductRow).where(or_(*same_app))).all()
            if known:
                # The same app under an earlier id: its reviews stay under that id.
                return next((row.id for row in known if row.id == competitor.id), known[0].id)
            session.add(
                ProductRow(
                    id=competitor.id,
                    name=competitor.name,
                    urls=[competitor.url],
                    google_play_id=store_ids.google_play,
                    app_store_id=store_ids.app_store,
                )
            )
            return competitor.id

    def latest_reviewed_at(self, product_id: str, source: ReviewSourceName) -> datetime | None:
        statement = select(func.max(ReviewRow.reviewed_at)).where(
            ReviewRow.product_id == product_id, ReviewRow.source == str(source)
        )
        with self._sessions() as session:
            latest = session.scalar(statement)
            return latest.astimezone(UTC) if latest else None

    def set_analysis(self, analysis: Mapping[str, tuple[str, Sentiment | None]]) -> None:
        if not analysis:
            return
        rows = [
            {"id": review_id, "language": language, "sentiment": sentiment and str(sentiment)}
            for review_id, (language, sentiment) in analysis.items()
        ]
        with self._sessions.begin() as session:
            session.execute(update(ReviewRow), rows)

    def for_product(self, product_id: str) -> list[Review]:
        return self._fetch(select(ReviewRow).where(ReviewRow.product_id == product_id))

    def newer_than(
        self, product_id: str, after: datetime, source: ReviewSourceName | None = None
    ) -> list[Review]:
        statement = select(ReviewRow).where(
            ReviewRow.product_id == product_id, ReviewRow.reviewed_at > after
        )
        if source is not None:
            statement = statement.where(ReviewRow.source == str(source))
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
