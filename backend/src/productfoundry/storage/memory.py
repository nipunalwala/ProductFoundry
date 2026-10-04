"""In-memory stores for tests and `--memory` runs. Nothing survives the process."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from productfoundry.core.clusters import Cluster
from productfoundry.core.competitors import Competitor
from productfoundry.core.reviews import ANALYSED_LANGUAGES, Review, ReviewSourceName, Sentiment


class InMemoryClusterStore:
    def __init__(self) -> None:
        self._clusters: dict[str, list[Cluster]] = {}

    def save(self, run_id: str, clusters: Sequence[Cluster]) -> None:
        self._clusters[run_id] = list(clusters)

    def for_run(self, run_id: str) -> list[Cluster]:
        return list(self._clusters.get(run_id, []))


class InMemoryReviewStore:
    def __init__(self) -> None:
        self._products: dict[str, Competitor] = {}
        self._reviews: dict[tuple[str, str], Review] = {}
        self._vectors: dict[str, tuple[str, list[float]]] = {}  # review id -> (model, vector)

    def ensure_product(self, competitor: Competitor) -> str:
        for product_id, known in self._products.items():
            same_play = known.store_ids.google_play and (
                known.store_ids.google_play == competitor.store_ids.google_play
            )
            same_ios = known.store_ids.app_store and (
                known.store_ids.app_store == competitor.store_ids.app_store
            )
            if product_id == competitor.id or same_play or same_ios:
                return product_id
        self._products[competitor.id] = competitor
        return competitor.id

    def latest_reviewed_at(self, product_id: str, source: ReviewSourceName) -> datetime | None:
        dates = [
            review.reviewed_at
            for review in self._reviews.values()
            if review.product_id == product_id and review.source == source
        ]
        return max(dates, default=None)

    def for_product(self, product_id: str) -> list[Review]:
        found = [review for review in self._reviews.values() if review.product_id == product_id]
        return sorted(found, key=lambda review: (review.reviewed_at, review.id))

    def upsert(self, reviews: Iterable[Review]) -> int:
        new = 0
        for review in reviews:
            key = (review.source, review.source_review_id)
            known = self._reviews.get(key)
            if known is None:
                new += 1
                self._reviews[key] = review
            else:
                refreshed = review.model_dump(include={"url", "reviewed_at", "rating", "text"})
                self._reviews[key] = known.model_copy(update=refreshed)
        return new

    def without_embedding(self, product_ids: Sequence[str], model: str) -> list[Review]:
        return [
            review
            for product_id in product_ids
            for review in self.for_product(product_id)
            if review.language in ANALYSED_LANGUAGES
            and self._vectors.get(review.id, (None, None))[0] != model
        ]

    def set_embeddings(self, vectors: Mapping[str, Sequence[float]], model: str) -> None:
        for review_id, vector in vectors.items():
            self._vectors[review_id] = (model, [float(value) for value in vector])

    def with_embeddings(
        self, product_ids: Sequence[str], model: str
    ) -> list[tuple[Review, list[float]]]:
        return [
            (review, self._vectors[review.id][1])
            for product_id in product_ids
            for review in self.for_product(product_id)
            if self._vectors.get(review.id, (None, None))[0] == model
        ]

    def set_analysis(self, analysis: Mapping[str, tuple[str, Sentiment | None]]) -> None:
        for key, review in self._reviews.items():
            if review.id in analysis:
                language, sentiment = analysis[review.id]
                self._reviews[key] = review.model_copy(
                    update={"language": language, "sentiment": sentiment}
                )
