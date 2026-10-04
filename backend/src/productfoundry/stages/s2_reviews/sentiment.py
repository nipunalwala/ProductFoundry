"""Sentiment for stored reviews, through the gateway, in batches."""

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator

from productfoundry.core.reviews import Review, Sentiment
from productfoundry.llm.types import Completer

TASK = "review_sentiment"
PROMPT = (Path(__file__).parent.parent / "prompts" / "review_sentiment_v1.md").read_text(
    encoding="utf-8"
)


class Label(BaseModel):
    n: int
    sentiment: Sentiment
    language: Literal["en", "hinglish", "other"]


class SentimentBatch(BaseModel):
    labels: list[Label]


def batch_schema(size: int) -> type[SentimentBatch]:
    """`SentimentBatch` that must label reviews 1..size, each exactly once.

    An answer that skips or repeats a review fails validation, so the gateway
    retries the batch instead of the stage silently losing reviews.
    """

    class CompleteSentimentBatch(SentimentBatch):
        @model_validator(mode="after")
        def _complete(self) -> "CompleteSentimentBatch":
            numbers = sorted(label.n for label in self.labels)
            if numbers != list(range(1, size + 1)):
                raise ValueError(f"expected one label for each of reviews 1 to {size}")
            return self

    CompleteSentimentBatch.__name__ = SentimentBatch.__name__
    return CompleteSentimentBatch


def batches(reviews: Sequence[Review], size: int) -> Iterator[Sequence[Review]]:
    for start in range(0, len(reviews), size):
        yield reviews[start : start + size]


def label_batch(llm: Completer, reviews: Sequence[Review]) -> list[Label]:
    """One gateway call for the batch. Labels come back in the order of `reviews`."""
    payload = [{"n": n, "text": review.text} for n, review in enumerate(reviews, start=1)]
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    answer = llm.complete(TASK, messages, batch_schema(len(reviews)))
    return sorted(answer.labels, key=lambda label: label.n)
