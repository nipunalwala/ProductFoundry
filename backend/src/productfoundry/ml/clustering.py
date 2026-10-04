"""UMAP then HDBSCAN, and the pure functions that turn labels into clusters.

No LLM is involved. The same vectors and seed give the same clusters.
"""

import hashlib
import warnings
from collections import Counter
from collections.abc import Sequence

import numpy as np

from productfoundry.core.clusters import Cluster
from productfoundry.core.reviews import Review, Sentiment
from productfoundry.ml.config import ClusteringConfig

NOISE = -1
_NEAREST_POOL = 3  # representatives are chosen among the (3 x wanted) nearest reviews


def cluster_vectors(vectors: np.ndarray, config: ClusteringConfig, seed: int) -> np.ndarray | None:
    """A cluster label per row, -1 for noise. None when there are too few rows to cluster."""
    count = len(vectors)
    if count < config.min_cluster_size:
        return None
    # Imported here: UMAP compiles on import and only this function needs it.
    import umap
    from sklearn.cluster import HDBSCAN

    components = min(config.umap_components, max(count - 2, 2))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # UMAP warns that a fixed seed makes it single-threaded
        reduced = umap.UMAP(
            n_neighbors=min(config.umap_neighbors, count - 1),
            n_components=components,
            metric=config.umap_metric,
            min_dist=0.0,
            init="spectral" if count > components + 2 else "random",
            random_state=seed,
        ).fit_transform(vectors)
    return HDBSCAN(
        min_cluster_size=config.min_cluster_size,
        min_samples=min(config.min_samples, count - 1),
        copy=True,
    ).fit_predict(reduced)


def centre_by_language(
    vectors: np.ndarray, languages: Sequence[str | None], min_group: int
) -> np.ndarray:
    """Remove what the vectors of one language have in common, then renormalise.

    A multilingual model encodes the language of a text as well as its meaning,
    so clusters would form by language first. Languages with fewer than
    `min_group` rows are centred on the mean of all rows.
    """
    if not len(vectors):
        return vectors
    centred = vectors.astype(np.float32, copy=True)
    labels = np.asarray([language or "" for language in languages])
    overall = vectors.mean(axis=0)
    for language in sorted(set(labels)):
        rows = labels == language
        centred[rows] -= vectors[rows].mean(axis=0) if rows.sum() >= min_group else overall
    norms = np.linalg.norm(centred, axis=1, keepdims=True)
    return centred / np.where(norms == 0, 1.0, norms)


def cluster_id(salt: str, review_ids: Sequence[str]) -> str:
    """Stable for the same run and members, different between runs."""
    digest = hashlib.sha256("\x00".join([salt, *sorted(review_ids)]).encode()).hexdigest()
    return f"cl_{digest[:16]}"


def negative_share(reviews: Sequence[Review]) -> float:
    return sum(review.sentiment == Sentiment.NEGATIVE for review in reviews) / len(reviews)


def noise_share(labels: Sequence[int]) -> float:
    return sum(label == NOISE for label in labels) / len(labels) if len(labels) else 0.0


def representatives(reviews: Sequence[Review], vectors: np.ndarray, wanted: int) -> list[str]:
    """Ids of the reviews closest to the cluster's centre, varied by product and month.

    The nearest review always comes first. After it, a review from a product and
    month not yet shown is preferred over a slightly nearer one that repeats.
    """
    centre = vectors.mean(axis=0)
    distance = np.linalg.norm(vectors - centre, axis=1)
    order = sorted(range(len(reviews)), key=lambda i: (distance[i], reviews[i].id))
    pool = order[: wanted * _NEAREST_POOL]

    chosen: list[int] = []
    seen: set[tuple[str, str]] = set()
    for index in pool:
        key = (reviews[index].product_id, f"{reviews[index].reviewed_at:%Y-%m}")
        if key not in seen:
            seen.add(key)
            chosen.append(index)
    chosen += [index for index in pool if index not in chosen]
    return [reviews[index].id for index in chosen[:wanted]]


def build_clusters(
    reviews: Sequence[Review],
    vectors: np.ndarray,
    labels: Sequence[int],
    config: ClusteringConfig,
    salt: str,
) -> tuple[list[Cluster], list[str]]:
    """Clusters, largest first, and the ids of the reviews left as noise."""
    members: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        members.setdefault(int(label), []).append(index)
    noise = [reviews[index].id for index in members.pop(NOISE, [])]

    clusters = []
    for indexes in members.values():
        group = [reviews[index] for index in indexes]
        products = Counter(review.product_id for review in group)
        clusters.append(
            Cluster(
                id=cluster_id(salt, [review.id for review in group]),
                size=len(group),
                negative_share=negative_share(group),
                product_ids=sorted(products, key=lambda product: (-products[product], product)),
                review_ids=sorted(review.id for review in group),
                representative_ids=representatives(group, vectors[indexes], config.representatives),
            )
        )
    clusters.sort(key=lambda cluster: (-cluster.size, cluster.id))
    return clusters, sorted(noise)
