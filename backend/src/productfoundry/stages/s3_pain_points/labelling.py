"""A label, a severity and sample quotes for one cluster, through the gateway."""

import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from productfoundry.core.base import NonEmptyStr
from productfoundry.core.clusters import Cluster
from productfoundry.core.reviews import Review
from productfoundry.llm.types import Completer

TASK = "cluster_labelling"
PROMPT = (Path(__file__).parent.parent / "prompts" / "cluster_labelling_v1.md").read_text(
    encoding="utf-8"
)
MIN_QUOTES, MAX_QUOTES = 3, 5
SAMPLE_CHARS = 600  # a longer review is cut for the prompt; the stored text is untouched


class Gloss(BaseModel):
    n: int
    english: NonEmptyStr


class ClusterLabel(BaseModel):
    """What the LLM returns. `quotes` and `glosses` name sample reviews by their number."""

    junk: bool = False
    junk_reason: str | None = None
    label: str | None = Field(default=None, max_length=80)
    description: str | None = None
    severity: int | None = Field(default=None, ge=1, le=5)
    severity_reason: str | None = None
    quotes: list[int] = []
    glosses: list[Gloss] = []


def label_schema(sample: Sequence[Review]) -> type[ClusterLabel]:
    """`ClusterLabel` that must be complete and may only quote the reviews it was shown.

    An answer that cites a review outside the sample, or leaves a Hinglish quote
    without a translation, fails validation, so the gateway asks again.
    """
    numbers = range(1, len(sample) + 1)
    hinglish = {n for n, review in enumerate(sample, start=1) if review.language == "hinglish"}
    fewest = min(MIN_QUOTES, len(sample))

    class GroundedClusterLabel(ClusterLabel):
        @model_validator(mode="after")
        def _grounded(self) -> "GroundedClusterLabel":
            if self.junk:
                if not (self.junk_reason or "").strip():
                    raise ValueError("a junk cluster needs junk_reason")
                return self
            for name in ("label", "description", "severity_reason"):
                if not (getattr(self, name) or "").strip():
                    raise ValueError(f"{name} is required unless junk is true")
            if self.severity is None:
                raise ValueError("severity is required unless junk is true")
            unknown = [n for n in self.quotes if n not in numbers]
            if unknown:
                raise ValueError(f"quotes cite reviews that were not in the sample: {unknown}")
            if len(set(self.quotes)) != len(self.quotes):
                raise ValueError("quotes must not repeat")
            if not fewest <= len(self.quotes) <= MAX_QUOTES:
                raise ValueError(f"expected {fewest} to {MAX_QUOTES} quotes")
            glossed = {gloss.n for gloss in self.glosses}
            missing = sorted(hinglish.intersection(self.quotes) - glossed)
            if missing:
                raise ValueError(f"Hinglish quotes need an English gloss: {missing}")
            return self

    GroundedClusterLabel.__name__ = ClusterLabel.__name__
    return GroundedClusterLabel


def label_cluster(
    llm: Completer, cluster: Cluster, sample: Sequence[Review], product_names: Sequence[str]
) -> ClusterLabel:
    """One gateway call. Only the cluster's representative reviews are sent."""
    payload = {
        "products": list(product_names),
        "cluster_size": cluster.size,
        "reviews": [
            {
                "n": n,
                "language": review.language,
                "rating": review.rating,
                "text": review.text[:SAMPLE_CHARS],
            }
            for n, review in enumerate(sample, start=1)
        ],
    }
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    return llm.complete(TASK, messages, label_schema(sample))
