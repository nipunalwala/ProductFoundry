from collections import Counter
from collections.abc import Sequence

from sqlalchemy import delete, select

from productfoundry.core.clusters import Cluster
from productfoundry.storage.db import Sessions
from productfoundry.storage.models import ClusterReviewRow, ClusterRow, ReviewRow


class PostgresClusterStore:
    """Clusters of a run and the reviews in each, on `clusters` and `cluster_reviews`."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    def save(self, run_id: str, clusters: Sequence[Cluster]) -> None:
        with self._sessions.begin() as session:
            session.execute(delete(ClusterRow).where(ClusterRow.run_id == run_id))
            for cluster in clusters:
                rank = {review_id: n for n, review_id in enumerate(cluster.representative_ids)}
                session.add(
                    ClusterRow(
                        id=cluster.id,
                        run_id=run_id,
                        size=cluster.size,
                        negative_share=cluster.negative_share,
                    )
                )
                session.flush()
                session.add_all(
                    ClusterReviewRow(
                        cluster_id=cluster.id,
                        review_id=review_id,
                        representative_rank=rank.get(review_id),
                    )
                    for review_id in cluster.review_ids
                )

    def for_run(self, run_id: str) -> list[Cluster]:
        members = (
            select(
                ClusterReviewRow.cluster_id,
                ClusterReviewRow.review_id,
                ClusterReviewRow.representative_rank,
                ReviewRow.product_id,
            )
            .join(ReviewRow, ReviewRow.id == ClusterReviewRow.review_id)
            .join(ClusterRow, ClusterRow.id == ClusterReviewRow.cluster_id)
            .where(ClusterRow.run_id == run_id)
        )
        with self._sessions() as session:
            rows = session.scalars(select(ClusterRow).where(ClusterRow.run_id == run_id)).all()
            by_cluster: dict[str, list] = {row.id: [] for row in rows}
            for member in session.execute(members):
                by_cluster[member.cluster_id].append(member)
        clusters = []
        for row in rows:
            found = by_cluster[row.id]
            products = Counter(member.product_id for member in found)
            ranked = sorted(
                (m for m in found if m.representative_rank is not None),
                key=lambda m: m.representative_rank,
            )
            clusters.append(
                Cluster(
                    id=row.id,
                    size=row.size,
                    negative_share=row.negative_share,
                    product_ids=sorted(products, key=lambda product: (-products[product], product)),
                    review_ids=sorted(member.review_id for member in found),
                    representative_ids=[member.review_id for member in ranked],
                )
            )
        return sorted(clusters, key=lambda cluster: (-cluster.size, cluster.id))
