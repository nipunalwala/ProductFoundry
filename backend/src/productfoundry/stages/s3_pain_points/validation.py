"""The evidence check: a report may only say what the stored clusters and reviews show."""

from collections.abc import Iterator, Mapping, Sequence

from productfoundry.core.clusters import Cluster
from productfoundry.core.errors import StageOutputInvalid
from productfoundry.core.pain_points import (
    LanguageCounts,
    PainPoint,
    PainPointReport,
    pain_point_score,
)
from productfoundry.core.reviews import Review, Sentiment

CLUSTERED = (Sentiment.NEGATIVE, Sentiment.MIXED)
_TOLERANCE = 1e-9


def language_counts(reviews: Sequence[Review]) -> LanguageCounts:
    english = sum(review.language == "en" for review in reviews)
    hinglish = sum(review.language == "hinglish" for review in reviews)
    return LanguageCounts(
        english=english, hinglish=hinglish, not_analysed=len(reviews) - english - hinglish
    )


def report_problems(
    report: PainPointReport,
    clusters: Sequence[Cluster],
    reviews: Mapping[str, Review],
    *,
    ranked_by_score: bool = True,
) -> list[str]:
    """Everything in the report that the run's clusters and stored reviews do not back.

    `clusters` are the clusters saved for the run and `reviews` every stored
    review of its products, by id. After a checkpoint edit the user's order
    stands, so `ranked_by_score` is false there.
    """
    known = {cluster.id: cluster for cluster in clusters}
    problems: list[str] = []

    for point in report.pain_points:
        problems += [f"{point.cluster_id}: {problem}" for problem in _point(point, known, reviews)]
    for junk in report.junk_clusters:
        cluster = known.get(junk.cluster_id)
        if cluster is None:
            problems.append(f"{junk.cluster_id}: no such cluster in this run")
        elif junk.review_count != cluster.size:
            problems.append(
                f"{junk.cluster_id}: review_count is {junk.review_count}, "
                f"the cluster holds {cluster.size}"
            )

    if report.language_counts != language_counts(list(reviews.values())):
        problems.append("language_counts do not match the stored reviews")
    clustered = sum(review.sentiment in CLUSTERED for review in reviews.values())
    if report.clustered_reviews != clustered:
        problems.append(
            f"clustered_reviews is {report.clustered_reviews}, "
            f"{clustered} stored reviews are negative or mixed"
        )
    noise = clustered - sum(cluster.size for cluster in clusters)
    if report.noise_reviews != noise:
        problems.append(
            f"noise_reviews is {report.noise_reviews}, {noise} reviews are in no cluster"
        )
    if ranked_by_score:
        order = sorted(report.pain_points, key=lambda p: (-p.score, -p.review_count, p.cluster_id))
        if order != report.pain_points:
            problems.append("pain points are not ordered by score")
    return problems


def _point(
    point: PainPoint, known: Mapping[str, Cluster], reviews: Mapping[str, Review]
) -> Iterator[str]:
    unknown = [cluster_id for cluster_id in point.cluster_ids if cluster_id not in known]
    if unknown:
        yield f"no such cluster in this run: {', '.join(unknown)}"
        return
    members = {
        review_id for cluster_id in point.cluster_ids for review_id in known[cluster_id].review_ids
    }
    missing = sorted(members - reviews.keys())
    if missing:
        yield f"reviews of the cluster are not stored: {', '.join(missing[:5])}"
        return
    stored = [reviews[review_id] for review_id in members]

    outside = [review_id for review_id in point.quote_review_ids if review_id not in members]
    if outside:
        yield f"quotes reviews that are not in the cluster: {', '.join(outside)}"
    if point.review_count != len(members):
        yield f"review_count is {point.review_count}, the cluster holds {len(members)}"
    negative = sum(review.sentiment == Sentiment.NEGATIVE for review in stored) / len(stored)
    if abs(point.negative_share - negative) > _TOLERANCE:
        yield f"negative_share is {point.negative_share}, the reviews give {negative}"
    products = {review.product_id for review in stored}
    if set(point.product_ids) != products:
        yield f"product_ids are {point.product_ids}, the reviews are of {sorted(products)}"
    expected = pain_point_score(point.review_count, point.negative_share, point.severity)
    if abs(point.score - expected) > _TOLERANCE:
        yield f"score is {point.score}, the ranking formula gives {expected}"
    hinglish = {
        review_id
        for review_id in point.quote_review_ids
        if review_id in reviews and reviews[review_id].language == "hinglish"
    }
    if set(point.quote_glosses) != hinglish:
        yield "quote_glosses must translate the Hinglish quotes, all of them and nothing else"


def check_report(
    report: PainPointReport,
    clusters: Sequence[Cluster],
    reviews: Mapping[str, Review],
    *,
    ranked_by_score: bool = True,
) -> None:
    """Reject a report with any claim the evidence does not back."""
    problems = report_problems(report, clusters, reviews, ranked_by_score=ranked_by_score)
    if problems:
        raise StageOutputInvalid(
            "the pain-point report does not match the stored evidence: " + "; ".join(problems)
        )
