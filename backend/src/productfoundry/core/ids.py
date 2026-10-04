"""Prefixed string ids. Evidence is always a list of these, never free text."""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import StringConstraints

IdPrefix = Literal["run_", "prod_", "rev_", "cl_", "req_", "task_"]


def new_id(prefix: IdPrefix) -> str:
    return f"{prefix}{uuid4().hex[:16]}"


RunId = Annotated[str, StringConstraints(pattern=r"^run_[A-Za-z0-9]+$")]
ProductId = Annotated[str, StringConstraints(pattern=r"^prod_[A-Za-z0-9]+$")]
ReviewId = Annotated[str, StringConstraints(pattern=r"^rev_[A-Za-z0-9]+$")]
ClusterId = Annotated[str, StringConstraints(pattern=r"^cl_[A-Za-z0-9]+$")]
