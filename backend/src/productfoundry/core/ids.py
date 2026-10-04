"""Prefixed string ids. Evidence is always a list of these, never free text."""

from hashlib import sha256
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import StringConstraints

IdPrefix = Literal["run_", "prod_", "rev_", "cl_", "gap_", "req_", "epic_", "task_"]


def new_id(prefix: IdPrefix) -> str:
    return f"{prefix}{uuid4().hex[:16]}"


def product_id(key: str) -> str:
    """A stable id from what identifies the product: a store id, a host or a name."""
    return f"prod_{sha256(key.encode()).hexdigest()[:16]}"


def review_id(source: str, source_review_id: str) -> str:
    """The same review from the same source always gets the same id."""
    digest = sha256(f"{source}\x00{source_review_id}".encode()).hexdigest()
    return f"rev_{digest[:20]}"


def market_gap_id(product: str) -> str:
    """The id of the competitor fact stage 1 recorded about a product."""
    return f"gap_{sha256(product.encode()).hexdigest()[:16]}"


RunId = Annotated[str, StringConstraints(pattern=r"^run_[A-Za-z0-9]+$")]
ProductId = Annotated[str, StringConstraints(pattern=r"^prod_[A-Za-z0-9]+$")]
ReviewId = Annotated[str, StringConstraints(pattern=r"^rev_[A-Za-z0-9]+$")]
ClusterId = Annotated[str, StringConstraints(pattern=r"^cl_[A-Za-z0-9]+$")]
GapId = Annotated[str, StringConstraints(pattern=r"^gap_[A-Za-z0-9]+$")]
# What a requirement may cite: a pain-point cluster or a market gap.
EvidenceId = Annotated[str, StringConstraints(pattern=r"^(cl|gap)_[A-Za-z0-9]+$")]
RequirementId = Annotated[str, StringConstraints(pattern=r"^req_[A-Za-z0-9]+$")]
EpicId = Annotated[str, StringConstraints(pattern=r"^epic_[A-Za-z0-9]+$")]
TaskId = Annotated[str, StringConstraints(pattern=r"^task_[A-Za-z0-9]+$")]
