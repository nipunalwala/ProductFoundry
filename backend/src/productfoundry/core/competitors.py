"""Stage 1 output: the competitor list."""

from typing import Literal

from pydantic import Field, model_validator

from productfoundry.core.base import NonEmptyStr, Schema, Url
from productfoundry.core.ids import ProductId
from productfoundry.core.run_input import StoreIds


class Competitor(Schema):
    id: ProductId
    name: NonEmptyStr
    url: Url
    positioning: NonEmptyStr
    target_users: NonEmptyStr
    store_ids: StoreIds = StoreIds()
    reason: NonEmptyStr  # why it is a competitor
    is_incumbent: bool = False


class RejectedCandidate(Schema):
    name: NonEmptyStr
    url: Url | None = None
    reason: NonEmptyStr


class CompetitorList(Schema):
    schema_version: Literal[1] = 1
    competitors: list[Competitor] = Field(min_length=1)
    rejected: list[RejectedCandidate] = []

    @model_validator(mode="after")
    def _check(self) -> "CompetitorList":
        ids = [c.id for c in self.competitors]
        if len(set(ids)) != len(ids):
            raise ValueError("competitor ids must be unique")
        names = [c.name.casefold() for c in self.competitors]
        if len(set(names)) != len(names):
            raise ValueError("competitor names must be unique")
        incumbents = [i for i, c in enumerate(self.competitors) if c.is_incumbent]
        if len(incumbents) > 1:
            raise ValueError("at most one competitor can be the incumbent")
        if incumbents and incumbents[0] != 0:
            raise ValueError("the incumbent must be first in the list")
        return self
