"""Clusters of reviews: what the ML layer produces and stage 3 labels."""

from collections.abc import Sequence
from typing import Protocol

from pydantic import Field, NonNegativeInt, PositiveInt

from productfoundry.core.base import NonEmptyStr, Schema
from productfoundry.core.ids import ClusterId, ProductId, ReviewId


class Cluster(Schema):
    id: ClusterId
    size: PositiveInt
    negative_share: float = Field(ge=0, le=1)  # negative reviews / reviews in the cluster
    product_ids: list[ProductId]
    review_ids: list[ReviewId]
    representative_ids: list[ReviewId]  # closest to the centre, varied by product and date


class ClusteringResult(Schema):
    clusters: list[Cluster]  # largest first
    clustered_reviews: NonNegativeInt  # negative and mixed reviews that were given to clustering
    noise_review_ids: list[ReviewId]
    noise_share: float = Field(ge=0, le=1)
    positive_reviews: NonNegativeInt  # counted, not clustered
    neutral_reviews: NonNegativeInt  # counted, not clustered
    not_enough_reviews: bool = False  # too few reviews to form even one cluster
    seed: int
    embedding_model: NonEmptyStr


class ClusterStore(Protocol):
    def save(self, run_id: str, clusters: Sequence[Cluster]) -> None:
        """Replace the run's clusters and their memberships."""
        ...

    def for_run(self, run_id: str) -> list[Cluster]: ...
