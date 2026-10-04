"""Review trends as pure functions: a pain point's monthly share of all reviews, and its growth.

Shares are used, not raw counts, because a release or a promotion raises every
count at once. A month with too few reviews is marked instead of being read as
zero, and growth is given only when both windows hold enough reviews.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from productfoundry.core.pain_points import Trend, TrendPoint, TrendSettings


def month_of(moment: datetime) -> str:
    return f"{moment:%Y-%m}"


def months_ending(last: str, count: int) -> list[str]:
    """`count` consecutive months, the last one being `last` ("YYYY-MM")."""
    year, month = int(last[:4]), int(last[5:])
    months = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return months[::-1]


def monthly_totals(dates: Iterable[datetime]) -> Counter[str]:
    return Counter(month_of(moment) for moment in dates)


def trend_from_counts(
    reviews_by_month: Mapping[str, int], totals: Mapping[str, int], settings: TrendSettings
) -> Trend:
    """The trend of one pain point from its monthly counts and the monthly totals of all
    reviews. The series ends at the latest month that has any review at all."""
    if not totals:
        return Trend(months=[])
    series = [
        TrendPoint(
            month=month,
            reviews=reviews_by_month.get(month, 0),
            total_reviews=totals.get(month, 0),
            enough=totals.get(month, 0) >= settings.min_month_reviews,
        )
        for month in months_ending(max(totals), settings.months)
    ]
    window = settings.window_months
    recent = _share(series[-window:], settings) if len(series) >= window else None
    previous = (
        _share(series[-2 * window : -window], settings) if len(series) >= 2 * window else None
    )
    growth = None
    rising = False
    if recent is not None and previous is not None:
        if previous > 0:
            growth = round((recent - previous) / previous, 4)
            rising = growth > settings.rising_threshold
        else:
            rising = recent > 0  # a complaint that was absent and now appears
    return Trend(
        months=series,
        recent_share=None if recent is None else round(recent, 4),
        previous_share=None if previous is None else round(previous, 4),
        growth=growth,
        rising=rising,
    )


def _share(points: Sequence[TrendPoint], settings: TrendSettings) -> float | None:
    """The pooled share over a window, or None when the window holds too few reviews."""
    total = sum(point.total_reviews for point in points)
    if total < settings.min_window_reviews:
        return None
    return sum(point.reviews for point in points) / total


def trend(dates: Iterable[datetime], totals: Mapping[str, int], settings: TrendSettings) -> Trend:
    """The trend of a pain point from the dates of its reviews."""
    return trend_from_counts(monthly_totals(dates), totals, settings)


def merged_trend(trends: Sequence[Mapping], settings: TrendSettings) -> Trend:
    """The trend of pain points merged into one: their monthly counts add up, the totals
    of all reviews stay what they were. Takes trends as plain data (a checkpoint edit)."""
    reviews: Counter[str] = Counter()
    totals: dict[str, int] = {}
    for item in trends:
        for point in item["months"]:
            reviews[point["month"]] += point["reviews"]
            totals[point["month"]] = point["total_reviews"]
    return trend_from_counts(reviews, totals, settings)
