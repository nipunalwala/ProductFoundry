import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from productfoundry.core.clusters import Cluster
from productfoundry.core.ids import review_id
from productfoundry.core.reviews import Review
from productfoundry.ml.clustering import (
    NOISE,
    build_clusters,
    centre_by_language,
    cluster_id,
    cluster_vectors,
    negative_share,
    noise_share,
    representatives,
)
from productfoundry.ml.config import load_config
from productfoundry.ml.embeddings import FakeEmbedder, SentenceTransformerEmbedder, embed_missing
from productfoundry.ml.pipeline import cluster_run
from productfoundry.stages.s2_reviews.language import detect_language
from productfoundry.storage.memory import InMemoryClusterStore, InMemoryReviewStore

FIXTURES = Path(__file__).parent / "fixtures"
THEMES = json.loads((FIXTURES / "clustering_reviews.json").read_text("utf-8"))
CONFIG = load_config()
DAY = datetime(2026, 6, 1, tzinfo=UTC)


def review(n, text="The payment failed again", *, product="prod_a", sentiment="negative",
           language="en", days=0) -> Review:  # fmt: skip
    return Review(
        id=review_id("google_play", f"r{n}"),
        product_id=product,
        source="google_play",
        source_review_id=f"r{n}",
        reviewed_at=DAY + timedelta(days=days),
        language=language,
        sentiment=sentiment,
        text=text,
    )


def blobs(seed=0, per_theme=30, noise=8, dimension=64):
    """Three tight groups of vectors and a few scattered ones, with their true labels."""
    rng = np.random.default_rng(seed)
    centres = rng.normal(size=(3, dimension))
    rows, truth = [], []
    for theme, centre in enumerate(centres):
        rows.append(centre + 0.05 * rng.normal(size=(per_theme, dimension)))
        truth += [theme] * per_theme
    rows.append(3 * rng.normal(size=(noise, dimension)))
    truth += [NOISE] * noise
    return np.vstack(rows).astype(np.float32), truth


def purity(labels, truth) -> dict[int, float]:
    """For each true theme: the share of its reviews that sit in its biggest cluster."""
    shares = {}
    for theme in sorted(set(truth) - {NOISE}):
        found = [label for label, t in zip(labels, truth, strict=True) if t == theme]
        shares[theme] = Counter(found).most_common(1)[0][1] / len(found)
    return shares


# Config


def test_the_model_and_the_clustering_parameters_are_pinned_in_config():
    assert CONFIG.embeddings.model.count("/") == 1
    assert "multilingual" in CONFIG.embeddings.model.lower()
    assert CONFIG.embeddings.dimension > 0
    assert CONFIG.clustering.min_cluster_size >= 2


# Clustering


def test_three_obvious_themes_are_recovered_from_synthetic_vectors():
    vectors, truth = blobs()
    labels = cluster_vectors(vectors, CONFIG.clustering, seed=0)
    assert len(set(labels) - {NOISE}) == 3
    assert all(share >= 0.9 for share in purity(labels, truth).values())
    theme_clusters = {Counter(labels[i * 30 : (i + 1) * 30]).most_common(1)[0][0] for i in range(3)}
    assert len(theme_clusters) == 3 and NOISE not in theme_clusters


def test_the_same_input_and_seed_give_the_same_clusters_twice():
    vectors, _ = blobs(seed=3)
    first = cluster_vectors(vectors, CONFIG.clustering, seed=11)
    second = cluster_vectors(vectors.copy(), CONFIG.clustering, seed=11)
    assert first.tolist() == second.tolist()


def test_fewer_reviews_than_the_minimum_cluster_size_is_not_an_exception():
    few = CONFIG.clustering.min_cluster_size - 1
    assert cluster_vectors(blobs()[0][:few], CONFIG.clustering, seed=0) is None
    just_enough = cluster_vectors(blobs()[0][: few + 1], CONFIG.clustering, seed=0)
    assert len(just_enough) == few + 1


# Post-processing: pure functions


def test_negative_share_and_noise_share():
    group = [review(1), review(2, sentiment="mixed"), review(3), review(4, sentiment="mixed")]
    assert negative_share(group) == 0.5
    assert noise_share([0, 0, NOISE, 1]) == 0.25
    assert noise_share([]) == 0.0


def test_representatives_are_the_nearest_reviews_varied_by_product_and_month():
    reviews = [
        review(0, product="prod_a", days=0),
        review(1, product="prod_a", days=1),
        review(2, product="prod_a", days=2),
        review(3, product="prod_b", days=3),
        review(4, product="prod_a", days=40),
        review(5, product="prod_a", days=41),
    ]
    # Distances from the centre grow with the index; the centre is pulled to index 0.
    vectors = np.array([[0.0], [0.1], [0.2], [0.3], [0.4], [3.0]], dtype=np.float32)
    vectors -= vectors.mean() - 0.0
    order = np.argsort(np.abs(vectors[:, 0] - vectors.mean()))
    nearest = reviews[order[0]].id

    picked = representatives(reviews, vectors, wanted=3)
    assert picked[0] == nearest
    assert len(picked) == len(set(picked)) == 3
    keys = {(r.product_id, f"{r.reviewed_at:%Y-%m}") for r in reviews if r.id in picked}
    assert len(keys) == 3  # one per (product, month) before any repeat
    assert len(representatives(reviews[:2], vectors[:2], wanted=5)) == 2


def test_clusters_are_built_largest_first_with_stable_ids_and_noise_listed():
    reviews = [review(n, product="prod_b" if n % 4 == 0 else "prod_a") for n in range(10)]
    reviews[1] = review(1, sentiment="mixed")
    vectors = np.arange(20, dtype=np.float32).reshape(10, 2)
    labels = [0, 0, 0, 1, 1, 1, 1, 1, NOISE, NOISE]

    clusters, noise = build_clusters(reviews, vectors, labels, CONFIG.clustering, "run_a")
    assert [cluster.size for cluster in clusters] == [5, 3]
    assert noise == sorted([reviews[8].id, reviews[9].id])
    small = clusters[1]
    assert small.negative_share == pytest.approx(2 / 3)
    assert small.product_ids == ["prod_a", "prod_b"]
    assert small.review_ids == sorted(r.id for r in reviews[:3])
    assert set(small.representative_ids) == set(small.review_ids)

    again, _ = build_clusters(reviews, vectors, labels, CONFIG.clustering, "run_a")
    other_run, _ = build_clusters(reviews, vectors, labels, CONFIG.clustering, "run_b")
    assert [c.id for c in again] == [c.id for c in clusters]
    assert {c.id for c in other_run}.isdisjoint(c.id for c in clusters)
    assert cluster_id("run_a", ["rev_2", "rev_1"]) == cluster_id("run_a", ["rev_1", "rev_2"])


# Embeddings and the pipeline, with the fake embedder


def stores_with(reviews) -> tuple[InMemoryReviewStore, InMemoryClusterStore]:
    store = InMemoryReviewStore()
    store.upsert(reviews)
    return store, InMemoryClusterStore()


def test_already_embedded_reviews_are_skipped():
    store, _ = stores_with([review(1), review(2), review(3, language="hi", sentiment=None)])
    embedder = FakeEmbedder()
    assert embed_missing(store, embedder, ["prod_a"]) == 2
    assert embed_missing(store, embedder, ["prod_a"]) == 0
    assert len(embedder.encoded) == 2  # the Devanagari review is not analysed, so not embedded

    store.upsert([review(4)])
    assert embed_missing(store, embedder, ["prod_a"]) == 1
    assert len(store.with_embeddings(["prod_a"], embedder.name)) == 3

    other = FakeEmbedder()
    other.name = "another-model"
    assert embed_missing(store, other, ["prod_a"]) == 3


def themed_reviews() -> list[Review]:
    reviews, n = [], 0
    for word in ("payment", "ads", "login"):
        for i in range(12):
            n += 1
            sentiment = "mixed" if i % 4 == 0 else "negative"
            product = "prod_a" if i % 2 else "prod_b"
            reviews.append(review(n, f"{word} problem number {i}", product=product,
                                  sentiment=sentiment, days=i * 9))  # fmt: skip
    for i in range(10):
        n += 1
        reviews.append(review(n, f"I love the {i} colours", sentiment="positive"))
    reviews.append(review(n + 1, "How do I export?", sentiment="neutral"))
    return reviews


def run_pipeline(reviews, run_id="run_a", seed=5):
    store, clusters = stores_with(reviews)
    embedder = FakeEmbedder([["payment"], ["ads"], ["login"]])
    result = cluster_run(
        run_id,
        ["prod_a", "prod_b", "prod_a"],
        reviews=store,
        clusters=clusters,
        embedder=embedder,
        config=CONFIG,
        seed=seed,
    )
    return result, store, clusters


def test_negative_and_mixed_reviews_are_clustered_and_positive_ones_only_counted():
    result, store, clusters = run_pipeline(themed_reviews())

    assert result.clustered_reviews == 36
    assert (result.positive_reviews, result.neutral_reviews) == (10, 1)
    assert not result.not_enough_reviews
    assert (result.seed, result.embedding_model) == (5, "fake-embedder")
    assert [cluster.size for cluster in result.clusters] == [12, 12, 12]
    assert result.noise_review_ids == [] and result.noise_share == 0.0

    texts = {r.id: r.text for r in store.for_product("prod_a") + store.for_product("prod_b")}
    for cluster in result.clusters:
        words = {texts[review_id].split()[0] for review_id in cluster.review_ids}
        assert len(words) == 1  # one theme per cluster
        assert cluster.negative_share == 0.75
        assert cluster.product_ids == ["prod_a", "prod_b"]
        assert len(cluster.representative_ids) == CONFIG.clustering.representatives
        assert set(cluster.representative_ids) <= set(cluster.review_ids)
    assert not any("love" in texts[i] for c in result.clusters for i in c.review_ids)


def test_clusters_and_memberships_are_saved_per_run():
    result, _, clusters = run_pipeline(themed_reviews())
    assert clusters.for_run("run_a") == result.clusters
    assert clusters.for_run("run_other") == []


def test_the_pipeline_is_repeatable():
    first, _, _ = run_pipeline(themed_reviews())
    second, _, _ = run_pipeline(list(reversed(themed_reviews())))
    assert first == second


def test_too_few_reviews_give_a_clear_result():
    few = [review(n, f"payment problem {n}") for n in range(4)]
    result, _, clusters = run_pipeline([*few, review(9, "nice", sentiment="positive")])
    assert result.not_enough_reviews
    assert result.clusters == [] and clusters.for_run("run_a") == []
    assert result.clustered_reviews == 4 and result.positive_reviews == 1
    assert len(result.noise_review_ids) == 4

    empty, _, _ = run_pipeline([review(1, "great", sentiment="positive")])
    assert empty.not_enough_reviews and empty.clustered_reviews == 0


# The real model. Skipped when it has not been downloaded yet.


@pytest.fixture(scope="module")
def real_embedder():
    embedder = SentenceTransformerEmbedder(CONFIG.embeddings)
    try:
        embedder.encode(["warm up"])
    except Exception as exc:  # not in the local cache, and tests never download
        pytest.skip(
            f"embedding model {CONFIG.embeddings.model} is not cached; run "
            f"`py -m uv run python eval/embedding_models.py` once ({type(exc).__name__})"
        )
    return embedder


def themed_texts() -> tuple[list[str], list[int], list[str]]:
    texts, truth, languages = [], [], []
    for theme, variants in enumerate(THEMES["themes"].values()):
        for language in ("en", "hinglish"):
            texts += variants[language]
            truth += [theme] * len(variants[language])
            languages += [language] * len(variants[language])
    texts += THEMES["noise"]
    truth += [NOISE] * len(THEMES["noise"])
    languages += [detect_language(text).language for text in THEMES["noise"]]
    return texts, truth, languages


def centred(embedder, texts, languages) -> np.ndarray:
    return centre_by_language(
        embedder.encode(texts), languages, CONFIG.clustering.min_language_group
    )


def test_centring_is_what_brings_a_hinglish_review_to_its_english_twin(real_embedder):
    pairs = json.loads((FIXTURES / "paired_reviews.json").read_text("utf-8"))["pairs"]
    count = len(pairs)
    raw = real_embedder.encode([p["en"] for p in pairs] + [p["hinglish"] for p in pairs])
    fixed = centre_by_language(raw, ["en"] * count + ["hinglish"] * count, 10)

    def separation(vectors) -> float:
        similarity = vectors[count:] @ vectors[:count].T
        others = similarity[~np.eye(count, dtype=bool)]
        return float(np.diag(similarity).mean() - others.mean())

    def twin_is_nearest(vectors) -> float:
        similarity = vectors[count:] @ vectors[:count].T
        return float((similarity.argmax(axis=1) == np.arange(count)).mean())

    # 2026-10-04, multilingual-e5-small: separation 0.063 raw, 0.468 centred.
    assert separation(fixed) > separation(raw) + 0.2
    assert twin_is_nearest(fixed) >= 0.9


def test_centring_removes_what_a_language_has_in_common():
    rng = np.random.default_rng(0)
    topics = rng.normal(size=(2, 8))
    offset = {"en": np.zeros(8), "hinglish": 5 * np.ones(8)}
    rows, languages = [], []
    for language in ("en", "hinglish"):
        for topic in (0, 1):
            for _ in range(12):
                rows.append(topics[topic] + offset[language] + 0.01 * rng.normal(size=8))
                languages.append(language)
    vectors = np.asarray(rows, dtype=np.float32)
    fixed = centre_by_language(vectors, languages, min_group=10)

    assert np.allclose(np.linalg.norm(fixed, axis=1), 1.0, atol=1e-5)
    english_topic_0, hinglish_topic_0, hinglish_topic_1 = fixed[0], fixed[24], fixed[36]
    assert english_topic_0 @ hinglish_topic_0 > 0.9  # same topic, other language: now close
    assert english_topic_0 @ hinglish_topic_1 < 0  # the other topic stays apart

    small = centre_by_language(vectors[:30], languages[:30], min_group=10)
    assert small.shape == (30, 8)  # the 6 Hinglish rows share the overall mean
    assert centre_by_language(np.empty((0, 8), dtype=np.float32), [], 10).shape == (0, 8)
    assert np.allclose(centre_by_language(vectors[:1], ["en"], 10), 0.0)


def test_the_model_has_the_pinned_dimension_and_unit_vectors(real_embedder):
    vectors = real_embedder.encode(["app bahut slow hai", "the app is very slow"])
    assert vectors.shape == (2, CONFIG.embeddings.dimension)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)


def test_real_reviews_of_three_themes_land_mostly_in_one_cluster_each(real_embedder):
    texts, truth, languages = themed_texts()
    labels = cluster_vectors(centred(real_embedder, texts, languages), CONFIG.clustering, seed=0)
    shares = purity(labels, truth)
    assert all(share >= 0.7 for share in shares.values()), shares
    pairs = list(zip(labels, truth, strict=True))
    main = [
        Counter(label for label, t in pairs if t == theme).most_common(1)[0][0]
        for theme in range(3)
    ]
    assert len(set(main)) == 3 and NOISE not in main


def test_english_and_hinglish_of_the_same_complaint_share_a_cluster(real_embedder):
    texts, truth, languages = themed_texts()
    labels = cluster_vectors(centred(real_embedder, texts, languages), CONFIG.clustering, seed=0)
    for theme in range(3):
        by_language = {
            language: Counter(
                label
                for label, t, lang in zip(labels, truth, languages, strict=True)
                if t == theme and lang == language
            ).most_common(1)[0][0]
            for language in ("en", "hinglish")
        }
        # The cluster holding most English reviews of a theme also holds most Hinglish ones.
        assert by_language["en"] == by_language["hinglish"] != NOISE, (theme, by_language)

    for cluster in set(labels) - {NOISE}:
        members = {lang for label, lang in zip(labels, languages, strict=True) if label == cluster}
        assert members == {"en", "hinglish"}, f"cluster {cluster} holds one language only"


def test_the_model_cache_is_outside_the_repository():
    from huggingface_hub.constants import HF_HUB_CACHE

    repository = Path(__file__).resolve().parents[2]
    assert repository not in Path(HF_HUB_CACHE).resolve().parents


# Storage of clusters and vectors, on both implementations


@pytest.fixture(params=["memory", "postgres"])
def both_stores(request):
    if request.param == "memory":
        return InMemoryReviewStore(), InMemoryClusterStore(), None
    from productfoundry.storage import PostgresClusterStore, ReviewRepository

    sessions = request.getfixturevalue("sessions")
    return ReviewRepository(sessions), PostgresClusterStore(sessions), sessions


def seed_run(sessions, run_input, *run_ids):
    """Postgres needs the products and runs that reviews and clusters point to."""
    if sessions is None:
        return
    from productfoundry.orchestrator import RunRecord
    from productfoundry.storage import PostgresRunStore
    from productfoundry.storage.models import ProductRow

    with sessions.begin() as session:
        session.add_all([ProductRow(id="prod_a", name="A"), ProductRow(id="prod_b", name="B")])
    for run_id in run_ids:
        PostgresRunStore(sessions).create(
            RunRecord(id=run_id, input=run_input, seed=0, stages=[], created_at=DAY, updated_at=DAY)
        )


def test_vectors_and_clusters_round_trip_through_the_store(both_stores, run_input):
    reviews, clusters, sessions = both_stores
    seed_run(sessions, run_input, "run_a", "run_b")
    reviews.upsert(themed_reviews())
    embedder = FakeEmbedder([["payment"], ["ads"], ["login"]], CONFIG.embeddings.dimension)

    def run(run_id):
        return cluster_run(run_id, ["prod_a", "prod_b"], reviews=reviews, clusters=clusters,
                           embedder=embedder, config=CONFIG, seed=5)  # fmt: skip

    result = run("run_a")
    assert len(embedder.encoded) == 47
    assert [cluster.size for cluster in result.clusters] == [12, 12, 12]
    assert clusters.for_run("run_a") == result.clusters
    assert all(isinstance(cluster, Cluster) for cluster in clusters.for_run("run_a"))

    second = run("run_b")
    assert len(embedder.encoded) == 47  # nothing is embedded twice
    # Same members; the ids, and so the order among equal sizes, differ between runs.
    assert sorted(c.review_ids for c in second.clusters) == sorted(
        c.review_ids for c in result.clusters
    )
    assert clusters.for_run("run_a") == result.clusters  # another run does not disturb it

    clusters.save("run_a", result.clusters[:1])
    assert clusters.for_run("run_a") == result.clusters[:1]
    assert len(clusters.for_run("run_b")) == 3
