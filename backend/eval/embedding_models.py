"""Compare multilingual embedding models on English/Hinglish pairs (phase 6).

Run from backend/:  py -m uv run python eval/embedding_models.py

For each model: how much closer a Hinglish review sits to its English twin than
to the other English reviews, and how often the twin is its nearest neighbour.
Each model is scored twice: on its raw vectors, and after per-language centring
(`centre_by_language`), which is what the pipeline clusters on. The chosen model
is pinned in src/productfoundry/ml/config.toml.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

from productfoundry.ml.clustering import centre_by_language
from productfoundry.ml.config import EmbeddingConfig
from productfoundry.ml.embeddings import SentenceTransformerEmbedder

CANDIDATES = [
    ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 384, ""),
    ("intfloat/multilingual-e5-small", 384, "query: "),
    ("sentence-transformers/paraphrase-multilingual-mpnet-base-v2", 768, ""),
]
PAIRS = Path(__file__).parent.parent / "tests" / "fixtures" / "paired_reviews.json"
HEADER = ("model", "vectors", "match", "unrel", "separ", "top1", "margin")


def encode(model: str, dimension: int, prefix: str, pairs: list[dict]) -> np.ndarray:
    """English rows first, then the Hinglish rows in the same order."""
    config = EmbeddingConfig(model=model, dimension=dimension, batch_size=32, prefix=prefix)
    embedder = SentenceTransformerEmbedder(config)
    vectors = embedder.encode([p["en"] for p in pairs] + [p["hinglish"] for p in pairs])
    assert vectors.shape[1] == dimension
    return vectors


def score(vectors: np.ndarray, count: int) -> dict:
    english, hinglish = vectors[:count], vectors[count:]
    similarity = hinglish @ english.T  # rows: Hinglish reviews, columns: English reviews
    matching = np.diag(similarity)
    unrelated = similarity[~np.eye(count, dtype=bool)]
    return {
        "matching": float(matching.mean()),
        "unrelated": float(unrelated.mean()),
        "separation": float(matching.mean() - unrelated.mean()),
        # Share of Hinglish reviews whose nearest English review is their own twin.
        "top1": float((similarity.argmax(axis=1) == np.arange(count)).mean()),
        # Worst case: the weakest twin against the strongest wrong match.
        "margin": float(matching.min() - unrelated.max()),
    }


def main() -> None:
    pairs = json.loads(PAIRS.read_text(encoding="utf-8"))["pairs"]
    languages = ["en"] * len(pairs) + ["hinglish"] * len(pairs)
    print(f"{len(pairs)} pairs")
    print("{:<60} {:<8} {:>6} {:>6} {:>6} {:>5} {:>7}".format(*HEADER))
    for model, dimension, prefix in CANDIDATES:
        started = time.perf_counter()
        raw = encode(model, dimension, prefix, pairs)
        seconds = time.perf_counter() - started
        for name, vectors in (("raw", raw), ("centred", centre_by_language(raw, languages, 10))):
            row = score(vectors, len(pairs))
            print(
                f"{model:<60} {name:<8} {row['matching']:6.3f} {row['unrelated']:6.3f} "
                f"{row['separation']:6.3f} {row['top1']:5.2f} {row['margin']:7.3f}"
            )
        print(f"{'':<60} loaded and encoded in {seconds:.0f} s")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
