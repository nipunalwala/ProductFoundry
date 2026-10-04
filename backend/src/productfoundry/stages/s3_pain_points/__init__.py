"""Stage 3: the clusters of a run's negative reviews become a ranked, cited pain-point report."""

from collections.abc import Mapping

from pydantic import BaseModel

from productfoundry.core.competitors import CompetitorList
from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.pain_points import (
    RANKING_FORMULA,
    JunkCluster,
    PainPoint,
    PainPointReport,
    TrendSettings,
    pain_point_score,
)
from productfoundry.core.reviews import Review, ReviewStore
from productfoundry.core.run_input import RunInput
from productfoundry.ml.config import MlConfig, load_config
from productfoundry.ml.pipeline import cluster_run
from productfoundry.ml.trends import monthly_totals, trend
from productfoundry.orchestrator.protocols import Services
from productfoundry.stages.s3_pain_points.labelling import label_cluster
from productfoundry.stages.s3_pain_points.switching import (
    classify,
    find_candidates,
    switching_table,
)
from productfoundry.stages.s3_pain_points.validation import check_report, language_counts


def run_reviews(
    competitors: CompetitorList, store: ReviewStore
) -> tuple[dict[str, str], dict[str, Review]]:
    """The run's products as {stored product id: name}, and their stored reviews by id."""
    names: dict[str, str] = {}
    for competitor in competitors.competitors:
        names.setdefault(store.ensure_product(competitor), competitor.name)
    reviews = {review.id: review for product in names for review in store.for_product(product)}
    return names, reviews


class PainPointStage:
    def __init__(self, config: MlConfig | None = None) -> None:
        self._config = config

    def __call__(
        self, run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
    ) -> PainPointReport:
        needed = {
            "a review store": services.reviews,
            "a cluster store": services.clusters,
            "an embedder": services.embedder,
            "the LLM gateway": services.llm,
        }
        for name, service in needed.items():
            if service is None:
                raise ProductFoundryError(f"pain-point discovery needs {name}")
        config = self._config or load_config()
        competitors: CompetitorList = earlier_outputs["s1_competitors"]
        names, _ = run_reviews(competitors, services.reviews)

        result = cluster_run(
            services.run_id,
            list(names),
            reviews=services.reviews,
            clusters=services.clusters,
            embedder=services.embedder,
            config=config,
            seed=services.seed,
        )
        # Read after clustering, so the report is checked against what is stored now.
        _, reviews = run_reviews(competitors, services.reviews)

        # Switching intent: candidates are picked without an LLM, then classified in batches.
        embedded = services.reviews.with_embeddings(list(names), services.embedder.name)
        candidates = find_candidates(embedded, services.embedder, config)
        switching = classify(services.llm, candidates, names, config.switching.batch_size)
        about_switching = {item.review_id for item in switching}

        settings = TrendSettings(**config.trends.model_dump())
        totals = monthly_totals(review.reviewed_at for review in reviews.values())

        points, junk = [], []
        for cluster in result.clusters:
            sample = [reviews[review_id] for review_id in cluster.representative_ids]
            product_names = [names[product] for product in cluster.product_ids]
            answer = label_cluster(services.llm, cluster, sample, product_names)
            if answer.junk:
                junk.append(
                    JunkCluster(
                        cluster_id=cluster.id, review_count=cluster.size, reason=answer.junk_reason
                    )
                )
                continue
            quotes = [sample[n - 1] for n in answer.quotes]
            glosses = {sample[gloss.n - 1].id: gloss.english for gloss in answer.glosses}
            points.append(
                {
                    "cluster_id": cluster.id,
                    "label": answer.label,
                    "description": answer.description,
                    "severity": answer.severity,
                    "severity_reason": answer.severity_reason,
                    "review_count": cluster.size,
                    "negative_share": cluster.negative_share,
                    "product_ids": cluster.product_ids,
                    "quote_review_ids": [review.id for review in quotes],
                    # Only Hinglish quotes carry a translation, whatever the answer offered.
                    "quote_glosses": {
                        review.id: glosses[review.id]
                        for review in quotes
                        if review.language == "hinglish"
                    },
                    "score": pain_point_score(
                        cluster.size, cluster.negative_share, answer.severity
                    ),
                    "trend": trend(
                        (reviews[review_id].reviewed_at for review_id in cluster.review_ids),
                        totals,
                        settings,
                    ),
                    "switching_review_ids": [
                        review_id
                        for review_id in cluster.review_ids
                        if review_id in about_switching
                    ],
                }
            )
        points.sort(key=lambda p: (-p["score"], -p["review_count"], p["cluster_id"]))

        report = PainPointReport(
            pain_points=[PainPoint(rank=rank, **point) for rank, point in enumerate(points, 1)],
            junk_clusters=junk,
            ranking_formula=RANKING_FORMULA,
            language_counts=language_counts(list(reviews.values())),
            clustered_reviews=result.clustered_reviews,
            noise_reviews=len(result.noise_review_ids),
            trend_settings=settings,
            switching_reviews=switching,
            switching_table=switching_table(switching, reviews, names),
        )
        check_report(report, result.clusters, reviews, names)
        return report


pain_points_stage = PainPointStage()
