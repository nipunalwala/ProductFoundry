"""Switching intent: which reviews say the user is leaving, has left, came from, or may leave.

A keyword and an embedding filter pick candidates without an LLM. Only the
candidates go to the gateway, in batches, to be classified.
"""

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, model_validator

from productfoundry.core.pain_points import SwitchingIntent, SwitchingReview, SwitchingRow
from productfoundry.core.reviews import Review
from productfoundry.llm.types import Completer
from productfoundry.ml.clustering import centre_by_language
from productfoundry.ml.config import MlConfig
from productfoundry.ml.embeddings import Embedder

TASK = "switching_intent"
PROMPT = (Path(__file__).parent.parent / "prompts" / "switching_intent_v1.md").read_text(
    encoding="utf-8"
)
SAMPLE_CHARS = 600  # a longer review is cut for the prompt; the stored text is untouched
TOP_REASONS = 3

# Words and phrases that reviews about switching use, in English and in Hinglish.
KEYWORDS = re.compile(
    r"\b(switch\w*|alternativ\w*|uninstall\w*|moved? to|moving to|shift\w* to|migrat\w*|"
    r"going back to|went back to|replac\w*|instead of|better than|leaving|"
    r"delet\w* (the|this) app|look\w* for (an)?other|other apps?|"
    r"chhod\w*|chod diya|hata diya|dusr[ae] app)\b",
    re.IGNORECASE,
)
# What a switching review sounds like. A candidate sits close to one of these.
SEEDS = (
    "I am switching to another app",
    "I uninstalled this and moved to a different app",
    "I switched from another app to this one",
    "Looking for an alternative to this app",
)


def find_candidates(
    embedded: Sequence[tuple[Review, Sequence[float]]], embedder: Embedder, config: MlConfig
) -> list[Review]:
    """Reviews worth asking the LLM about: a keyword match, or close to a seed sentence.

    Vectors are centred by language first, as for clustering, so a Hinglish
    review is compared on its meaning and not on its language.
    """
    if not embedded:
        return []
    reviews = [review for review, _ in embedded]
    vectors = np.asarray([vector for _, vector in embedded], dtype=np.float32)
    languages = [review.language for review in reviews]
    centred = centre_by_language(vectors, languages, config.clustering.min_language_group)

    english = np.asarray([language == "en" for language in languages])
    enough = english.sum() >= config.clustering.min_language_group
    seeds = embedder.encode(SEEDS) - (vectors[english] if enough else vectors).mean(axis=0)
    norms = np.linalg.norm(seeds, axis=1, keepdims=True)
    seeds = seeds / np.where(norms == 0, 1.0, norms)
    similarity = (centred @ seeds.T).max(axis=1)

    chosen = [
        n
        for n, review in enumerate(reviews)
        if KEYWORDS.search(review.text) or similarity[n] >= config.switching.similarity
    ]
    # Keyword matches first, then the most similar: what is kept when there are too many.
    chosen.sort(key=lambda n: (not KEYWORDS.search(reviews[n].text), -similarity[n], reviews[n].id))
    return [reviews[n] for n in chosen[: config.switching.max_candidates]]


class Label(BaseModel):
    n: int
    intent: SwitchingIntent | Literal["none"]
    other_product: str | None = None
    reason: str | None = None


class SwitchingBatch(BaseModel):
    labels: list[Label]


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def batch_schema(reviews: Sequence[Review]) -> type[SwitchingBatch]:
    """`SwitchingBatch` that labels every review once and names only products the review names.

    A product that is not in the review's text is an invention, so the answer
    fails validation and the gateway asks again.
    """
    texts = [_plain(review.text) for review in reviews]

    class GroundedSwitchingBatch(SwitchingBatch):
        @model_validator(mode="after")
        def _grounded(self) -> "GroundedSwitchingBatch":
            numbers = sorted(label.n for label in self.labels)
            if numbers != list(range(1, len(texts) + 1)):
                raise ValueError(f"expected one label for each of reviews 1 to {len(texts)}")
            for label in self.labels:
                named = _plain(label.other_product or "")
                if named and named not in texts[label.n - 1]:
                    raise ValueError(
                        f"review {label.n} does not name the product {label.other_product!r}"
                    )
            return self

    GroundedSwitchingBatch.__name__ = SwitchingBatch.__name__
    return GroundedSwitchingBatch


def classify(
    llm: Completer, candidates: Sequence[Review], product_names: Mapping[str, str], batch_size: int
) -> list[SwitchingReview]:
    """One gateway call per batch. Reviews the LLM calls `none` are left out."""
    found = []
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        payload = {
            "reviews": [
                {
                    "n": n,
                    "product": product_names.get(review.product_id, review.product_id),
                    "text": review.text[:SAMPLE_CHARS],
                }
                for n, review in enumerate(batch, start=1)
            ]
        }
        messages = [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        answer = llm.complete(TASK, messages, batch_schema(batch))
        for label in sorted(answer.labels, key=lambda label: label.n):
            if label.intent == "none":
                continue
            review = batch[label.n - 1]
            other = (label.other_product or "").strip() or None
            # A review that names only its own product names no other product.
            own = _plain(product_names.get(review.product_id, ""))
            if other and _plain(other) == own:
                other = None
            found.append(
                SwitchingReview(
                    review_id=review.id,
                    intent=label.intent,
                    other_product=other,
                    reason=(label.reason or "").strip() or None,
                )
            )
    return found


def switching_table(
    switching: Sequence[SwitchingReview],
    reviews: Mapping[str, Review],
    product_names: Mapping[str, str],
) -> list[SwitchingRow]:
    """The switching reviews by direction (from, to), the most common direction first.

    Pure: the same reviews always give the same table. Spellings of the other
    product that differ only in case or punctuation share a row.
    """
    groups: dict[tuple[str | None, str | None], list[SwitchingReview]] = {}
    spelling: dict[str, Counter[str]] = {}
    for item in switching:
        review = reviews[item.review_id]
        own = product_names.get(review.product_id, review.product_id)
        other = _plain(item.other_product) if item.other_product else None
        if item.other_product:
            spelling.setdefault(other, Counter())[item.other_product] += 1
        arrived = item.intent == SwitchingIntent.SWITCHED_TO
        key = (other, own) if arrived else (own, other)
        groups.setdefault(key, []).append(item)

    def shown(name: str | None) -> str | None:
        if name is None or name not in spelling:
            return name  # the run's own product, already as it is written
        return sorted(spelling[name].items(), key=lambda pair: (-pair[1], pair[0]))[0][0]

    rows = []
    for (start, end), items in groups.items():
        reasons = Counter(item.reason for item in items if item.reason)
        arrived = items[0].intent == SwitchingIntent.SWITCHED_TO
        rows.append(
            SwitchingRow(
                from_product=shown(start) if arrived else start,
                to_product=end if arrived else shown(end),
                count=len(items),
                reasons=[
                    reason
                    for reason, _ in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))
                ][:TOP_REASONS],
                review_ids=sorted(item.review_id for item in items),
            )
        )
    rows.sort(key=lambda row: (-row.count, row.from_product or "", row.to_product or ""))
    return rows
