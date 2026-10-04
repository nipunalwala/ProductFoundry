"""In-memory stores for tests and `--memory` runs. Nothing survives the process."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from productfoundry.core.changelog import ChangelogItem, ChangelogMatch, ChangelogSource
from productfoundry.core.clusters import Cluster
from productfoundry.core.competitors import Competitor
from productfoundry.core.pricing import PricingAlert, PricingSnapshot, TrackedPage
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


class InMemoryPricingStore:
    def __init__(self) -> None:
        self._snapshots: list[PricingSnapshot] = []
        self.names: dict[str, str] = {}

    def add(self, snapshot: PricingSnapshot, product_name: str) -> None:
        self._snapshots.append(snapshot)
        self.names.setdefault(snapshot.product_id, product_name)

    def tracked(self) -> list[TrackedPage]:
        pages = {(s.product_id, s.url) for s in self._snapshots}
        return [
            TrackedPage(product_id=product, product_name=self.names[product], url=url)
            for product, url in sorted(pages, key=lambda page: (self.names[page[0]], page[1]))
        ]

    def latest(self, product_id: str, url: str | None = None) -> PricingSnapshot | None:
        found = [s for s in self.history(product_id) if url is None or s.url == url]
        return found[-1] if found else None

    def history(self, product_id: str) -> list[PricingSnapshot]:
        found = [s for s in self._snapshots if s.product_id == product_id]
        return sorted(found, key=lambda snapshot: snapshot.fetched_at)


class InMemoryAlertStore:
    def __init__(self) -> None:
        self._alerts: list[PricingAlert] = []

    def add(self, alert: PricingAlert) -> None:
        self._alerts.append(alert)

    def list(self, product_id: str | None = None) -> list[PricingAlert]:
        found = [a for a in self._alerts if product_id is None or a.product_id == product_id]
        return sorted(found, key=lambda alert: alert.detected_at, reverse=True)


class InMemoryChangelogStore:
    def __init__(self) -> None:
        self._sources: list[ChangelogSource] = []
        self._names: dict[str, str] = {}
        self._items: dict[str, ChangelogItem] = {}
        self._matches: dict[str, dict[str, ChangelogMatch]] = {}

    def track(self, source: ChangelogSource, product_name: str) -> None:
        if source not in self._sources:
            self._sources.append(source)
        self._names.setdefault(source.product_id, product_name)

    def sources(self) -> list[ChangelogSource]:
        return sorted(self._sources, key=lambda s: (s.product_id, str(s.kind), s.target))

    def product_names(self) -> dict[str, str]:
        return dict(self._names)

    def add_items(self, items: Sequence[ChangelogItem]) -> int:
        new = [item for item in items if item.id not in self._items]
        for item in new:
            self._items.setdefault(item.id, item)
        return len({item.id for item in new})

    def items(self, product_ids: Sequence[str]) -> list[ChangelogItem]:
        found = [item for item in self._items.values() if item.product_id in product_ids]
        dated = sorted(
            (item for item in found if item.released_at),
            key=lambda item: (item.released_at, item.id),
            reverse=True,
        )
        return dated + sorted((item for item in found if not item.released_at), key=lambda i: i.id)

    def save_matches(self, run_id: str, matches: Sequence[ChangelogMatch]) -> None:
        self._matches.setdefault(run_id, {}).update({match.item_id: match for match in matches})

    def matches(self, run_id: str) -> list[ChangelogMatch]:
        return [match for _, match in sorted(self._matches.get(run_id, {}).items())]

    def clear_matches(self, run_id: str) -> None:
        self._matches.pop(run_id, None)
