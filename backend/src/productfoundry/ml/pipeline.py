"""Stored reviews of a run's products -> clusters saved for the run."""

from collections.abc import Sequence

import numpy as np

from productfoundry.core.clusters import ClusteringResult, ClusterStore
from productfoundry.core.reviews import ReviewStore, Sentiment
from productfoundry.ml.clustering import (
    build_clusters,
    centre_by_language,
    cluster_vectors,
    noise_share,
)
from productfoundry.ml.config import MlConfig
from productfoundry.ml.embeddings import Embedder, embed_missing

CLUSTERED = (Sentiment.NEGATIVE, Sentiment.MIXED)


def cluster_run(
    run_id: str,
    product_ids: Sequence[str],
    *,
    reviews: ReviewStore,
    clusters: ClusterStore,
    embedder: Embedder,
    config: MlConfig,
    seed: int,
) -> ClusteringResult:
    """Embed what is not embedded yet, cluster the negative and mixed reviews, save the result."""
    product_ids = list(dict.fromkeys(product_ids))
    embed_missing(reviews, embedder, product_ids)
    embedded = reviews.with_embeddings(product_ids, embedder.name)
    # A stable order, so the same reviews and seed always give the same clusters.
    embedded.sort(key=lambda pair: pair[0].id)
    # Centring uses every embedded review, positive ones too: more rows, steadier means.
    centred = centre_by_language(
        np.asarray([vector for _, vector in embedded], dtype=np.float32),
        [review.language for review, _ in embedded],
        config.clustering.min_language_group,
    )
    keep = [n for n, (review, _) in enumerate(embedded) if review.sentiment in CLUSTERED]
    selected = [embedded[n][0] for n in keep]
    vectors = centred[keep] if keep else np.empty((0, 0), dtype=np.float32)

    labels = cluster_vectors(vectors, config.clustering, seed) if selected else None
    if labels is None:
        found, noise = [], [review.id for review in selected]
    else:
        found, noise = build_clusters(selected, vectors, labels, config.clustering, run_id)
    clusters.save(run_id, found)

    sentiments = [review.sentiment for review, _ in embedded]
    return ClusteringResult(
        clusters=found,
        clustered_reviews=len(selected),
        noise_review_ids=noise,
        noise_share=noise_share(labels) if labels is not None else 0.0,
        positive_reviews=sentiments.count(Sentiment.POSITIVE),
        neutral_reviews=sentiments.count(Sentiment.NEUTRAL),
        not_enough_reviews=labels is None,
        seed=seed,
        embedding_model=embedder.name,
    )
