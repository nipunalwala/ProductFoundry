"""Local sentence embeddings. They never go through the LLM gateway."""

import hashlib
from collections.abc import Sequence
from typing import Protocol

import numpy as np

from productfoundry.core.reviews import ReviewStore
from productfoundry.ml.config import EmbeddingConfig


class Embedder(Protocol):
    name: str  # stored with each vector, so vectors of different models never mix

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """One L2-normalised row per text."""
        ...


class SentenceTransformerEmbedder:
    """A sentence-transformers model, loaded on first use and cached by the library
    in the user's Hugging Face cache, outside the repository."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self.name = config.model
        self._config = config
        self._model = None

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._config.model, device="cpu")
        return self._model.encode(
            [self._config.prefix + text for text in texts],
            batch_size=self._config.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).astype(np.float32)


class FakeEmbedder:
    """Deterministic vectors for tests: texts sharing a keyword land close together."""

    name = "fake-embedder"

    def __init__(self, topics: Sequence[Sequence[str]] = (), dimension: int = 16) -> None:
        self._topics = [tuple(word.lower() for word in topic) for topic in topics]
        self._dimension = max(dimension, len(self._topics) + 1)
        self.encoded: list[str] = []

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        self.encoded.extend(texts)
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            noise = np.frombuffer(digest, dtype=np.uint8)[: self._dimension] / 255.0 - 0.5
            row = 0.15 * np.resize(noise, self._dimension)
            for axis, words in enumerate(self._topics):
                if any(word in text.lower() for word in words):
                    row[axis] += 1.0
            rows.append(row / (np.linalg.norm(row) or 1.0))
        return np.asarray(rows, dtype=np.float32)


def embed_missing(store: ReviewStore, embedder: Embedder, product_ids: Sequence[str]) -> int:
    """Embed the stored reviews that have no vector from this model. Returns how many."""
    pending = store.without_embedding(product_ids, embedder.name)
    if not pending:
        return 0
    vectors = embedder.encode([review.text for review in pending])
    store.set_embeddings(
        {review.id: vector.tolist() for review, vector in zip(pending, vectors, strict=True)},
        embedder.name,
    )
    return len(pending)
