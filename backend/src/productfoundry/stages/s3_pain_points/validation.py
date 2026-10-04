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
from productfoundry.ml.trends import monthly_totals, trend
from productfoundry.stages.s3_pain_points.switching import _plain, switching_table

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
    product_names: Mapping[str, str],
    *,
    ranked_by_score: bool = True,
) -> list[str]:
    """Everything in the report that the run's clusters and stored reviews do not back.

    `clusters` are the clusters saved for the run, `reviews` every stored review
    of its products by id, and `product_names` those products by id. After a
    checkpoint edit the user's order stands, so `ranked_by_score` is false there.
    """
    known = {cluster.id: cluster for cluster in clusters}
    problems: list[str] = []
    totals = monthly_totals(review.reviewed_at for review in reviews.values())
    about_switching = {item.review_id for item in report.switching_reviews}

    for point in report.pain_points:
        found = _point(point, known, reviews)
        found = [*found, *_over_time(point, known, reviews, totals, report, about_switching)]
        problems += [f"{point.cluster_id}: {problem}" for problem in found]

    for item in report.switching_reviews:
        review = reviews.get(item.review_id)
        if review is None:
            problems.append(f"switching review {item.review_id} is not stored")
        elif item.other_product and _plain(item.other_product) not in _plain(review.text):
            problems.append(
                f"switching review {item.review_id} does not name {item.other_product!r}"
            )
    if all(item.review_id in reviews for item in report.switching_reviews):
        expected = switching_table(report.switching_reviews, reviews, product_names)
        if report.switching_table != expected:
            problems.append("the switching table does not match the switching reviews")
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


def _over_time(
    point: PainPoint,
    known: Mapping[str, Cluster],
    reviews: Mapping[str, Review],
    totals: Mapping[str, int],
    report: PainPointReport,
    about_switching: set[str],
) -> Iterator[str]:
    """The trend and the switching reviews of a pain point, against its stored reviews."""
    if any(cluster_id not in known for cluster_id in point.cluster_ids):
        return  # already reported
    members = [
        review_id for cluster_id in point.cluster_ids for review_id in known[cluster_id].review_ids
    ]
    if any(review_id not in reviews for review_id in members):
        return  # already reported
    dates = (reviews[review_id].reviewed_at for review_id in members)
    if point.trend != trend(dates, totals, report.trend_settings):
        yield "trend does not match the dates of the stored reviews"
    if set(point.switching_review_ids) != about_switching.intersection(members):
        yield "switching_review_ids must be the cluster's reviews that are about switching"


def check_report(
    report: PainPointReport,
    clusters: Sequence[Cluster],
    reviews: Mapping[str, Review],
    product_names: Mapping[str, str],
    *,
    ranked_by_score: bool = True,
) -> None:
    """Reject a report with any claim the evidence does not back."""
    problems = report_problems(
        report, clusters, reviews, product_names, ranked_by_score=ranked_by_score
    )
    if problems:
        raise StageOutputInvalid(
            "the pain-point report does not match the stored evidence: " + "; ".join(problems)
        )
