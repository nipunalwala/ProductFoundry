"""Prefixed string ids. Evidence is always a list of these, never free text."""

from hashlib import sha256
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import StringConstraints

IdPrefix = Literal["run_", "prod_", "rev_", "cl_", "req_", "task_"]


def new_id(prefix: IdPrefix) -> str:
    return f"{prefix}{uuid4().hex[:16]}"


def product_id(key: str) -> str:
    """A stable id from what identifies the product: a store id, a host or a name."""
    return f"prod_{sha256(key.encode()).hexdigest()[:16]}"


def review_id(source: str, source_review_id: str) -> str:
    """The same review from the same source always gets the same id."""
    digest = sha256(f"{source}\x00{source_review_id}".encode()).hexdigest()
    return f"rev_{digest[:20]}"


RunId = Annotated[str, StringConstraints(pattern=r"^run_[A-Za-z0-9]+$")]
ProductId = Annotated[str, StringConstraints(pattern=r"^prod_[A-Za-z0-9]+$")]
ReviewId = Annotated[str, StringConstraints(pattern=r"^rev_[A-Za-z0-9]+$")]
ClusterId = Annotated[str, StringConstraints(pattern=r"^cl_[A-Za-z0-9]+$")]
