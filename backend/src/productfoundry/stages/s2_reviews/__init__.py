"""Stage 2: collect, clean, deduplicate and label the reviews of every approved product."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel

from productfoundry.core.competitors import Competitor, CompetitorList
from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.ids import review_id
from productfoundry.core.reviews import (
    ANALYSED_LANGUAGES,
    ProductReviewCounts,
    Review,
    ReviewSet,
    ReviewSourceName,
)
from productfoundry.core.run_input import Platform, RunInput
from productfoundry.orchestrator.protocols import Services
from productfoundry.sources import RawReview
from productfoundry.stages.s2_reviews.cleaning import DUPLICATE, clean_text, dedup_key, drop_reason
from productfoundry.stages.s2_reviews.language import UNDETERMINED, detect_language
from productfoundry.stages.s2_reviews.sentiment import batches, label_batch

STORE_OF_PLATFORM = {
    Platform.ANDROID: ReviewSourceName.GOOGLE_PLAY,
    Platform.IOS: ReviewSourceName.APP_STORE,
}


@dataclass(frozen=True)
class ReviewSettings:
    cap_per_store: int = 2000  # reviews fetched per product per store in one run
    min_words: int = 3  # shorter reviews ("Good", "Nice app") are dropped
    sentiment_batch_size: int = 40  # reviews per LLM call


class ReviewStage:
    def __init__(self, settings: ReviewSettings | None = None) -> None:
        self._settings = settings or ReviewSettings()

    def __call__(
        self, run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
    ) -> ReviewSet:
        if services.reviews is None:
            raise ProductFoundryError("review collection needs a review store")
        if services.llm is None:
            raise ProductFoundryError("review collection needs the LLM gateway")
        competitors: CompetitorList = earlier_outputs["s1_competitors"]
        store = services.reviews

        dropped: Counter[str] = Counter()
        stored_ids: dict[str, str] = {}  # competitor id -> the id its reviews are kept under
        for competitor in competitors.competitors:
            stored_ids[competitor.id] = store.ensure_product(competitor)
            self._collect(competitor, stored_ids[competitor.id], run_input, services, dropped)
        for product_id in dict.fromkeys(stored_ids.values()):
            self._analyse(product_id, services)

        return ReviewSet(
            products=[
                _counts(competitor.id, store.for_product(stored_ids[competitor.id]))
                for competitor in competitors.competitors
            ],
            dropped=dict(dropped),
        )

    def _collect(
        self,
        competitor: Competitor,
        product_id: str,
        run_input: RunInput,
        services: Services,
        dropped: Counter[str],
    ) -> None:
        """Fetch what is new from each store, clean it, and store what is worth keeping."""
        store = services.reviews
        seen = {dedup_key(review.text) for review in store.for_product(product_id)}
        for platform in run_input.platforms:
            source_name = STORE_OF_PLATFORM.get(platform)
            source = services.review_sources.get(source_name) if source_name else None
            store_id = getattr(competitor.store_ids, source_name) if source_name else None
            if source is None or store_id is None:
                continue
            raw = source.fetch(
                store_id,
                run_input.region,
                since=store.latest_reviewed_at(product_id, source_name),
                limit=self._settings.cap_per_store,
            )
            kept = []
            for item in raw:
                review = self._clean(item, product_id, source_name, seen, dropped)
                if review is not None:
                    kept.append(review)
            store.upsert(kept)

    def _clean(
        self,
        raw: RawReview,
        product_id: str,
        source: ReviewSourceName,
        seen: set[str],
        dropped: Counter[str],
    ) -> Review | None:
        text = clean_text(raw.text)
        reason = drop_reason(text, self._settings.min_words)
        key = dedup_key(text)
        if reason is None and key in seen:
            reason = DUPLICATE
        if reason is not None:
            dropped[reason] += 1
            return None
        seen.add(key)
        detection = detect_language(text)
        return Review(
            id=review_id(source, raw.source_review_id),
            product_id=product_id,
            source=source,
            source_review_id=raw.source_review_id,
            url=raw.url,
            reviewed_at=raw.reviewed_at,
            rating=raw.rating,
            # A borderline review has no language yet: the LLM settles it with the sentiment.
            language=None if detection.borderline else detection.language,
            text=text,
        )

    def _analyse(self, product_id: str, services: Services) -> None:
        """Sentiment for every stored review that still lacks it.

        Each batch is saved as it comes back, so a run paused for quota resumes
        where it stopped.
        """
        store = services.reviews
        pending = [
            review
            for review in store.for_product(product_id)
            if review.sentiment is None
            and (review.language is None or review.language in ANALYSED_LANGUAGES)
        ]
        for batch in batches(pending, self._settings.sentiment_batch_size):
            analysis = {}
            for review, label in zip(batch, label_batch(services.llm, batch), strict=True):
                language = review.language or label.language
                if language == "other":
                    analysis[review.id] = (UNDETERMINED, None)  # stored, flagged, not analysed
                else:
                    analysis[review.id] = (language, label.sentiment)
            store.set_analysis(analysis)


def _counts(product_id: str, reviews: list[Review]) -> ProductReviewCounts:
    analysed = [review for review in reviews if review.language in ANALYSED_LANGUAGES]
    return ProductReviewCounts(
        product_id=product_id,
        total=len(reviews),
        by_source=Counter(review.source for review in reviews),
        by_language=Counter(review.language for review in reviews),
        by_sentiment=Counter(review.sentiment for review in analysed),
        not_analysed=len(reviews) - len(analysed),
    )


reviews_stage = ReviewStage()
