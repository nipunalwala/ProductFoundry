import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from productfoundry.core.ids import review_id
from productfoundry.core.pain_points import PainPointReport, SwitchingReview, TrendSettings
from productfoundry.core.reviews import Review
from productfoundry.llm import LlmFailed, ProviderResponse
from productfoundry.llm.fakes import FakeProvider
from productfoundry.ml.embeddings import FakeEmbedder
from productfoundry.ml.trends import merged_trend, months_ending, trend, trend_from_counts
from productfoundry.orchestrator.checkpoints import edit_pain_points
from productfoundry.stages.s3_pain_points import PainPointStage, run_reviews
from productfoundry.stages.s3_pain_points.export import export_report, render_markdown
from productfoundry.stages.s3_pain_points.switching import (
    KEYWORDS,
    batch_schema,
    classify,
    find_candidates,
    switching_table,
)
from productfoundry.stages.s3_pain_points.validation import report_problems
from test_s3_pain_points import COMPETITORS, CONFIG, Labeller, gateway, services

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/llm/switching_intent.json").read_text("utf-8")
)
SETTINGS = TrendSettings(
    months=8, window_months=3, min_month_reviews=5, min_window_reviews=15, rising_threshold=0.25
)
MONTHS = [f"2026-{n:02d}" for n in range(1, 9)]
NAMES = {"prod_a": "Splitly", "prod_b": "Tabby"}
# The fake embedder is noisy, so only a review that shares a seed topic passes this bar.
STRICT = CONFIG.model_copy(
    update={"switching": CONFIG.switching.model_copy(update={"similarity": 0.5})}
)
S1 = "s1_competitors"


def series(reviews: list[int], totals: list[int]):
    return trend_from_counts(
        dict(zip(MONTHS, reviews, strict=True)), dict(zip(MONTHS, totals, strict=True)), SETTINGS
    )


# Trend maths


def test_growth_is_the_share_over_the_last_three_months_against_the_three_before():
    found = series([1, 1, 2, 2, 2, 4, 4, 4], [20] * 8)
    assert [point.month for point in found.months] == MONTHS
    assert (found.previous_share, found.recent_share) == (0.1, 0.2)
    assert found.growth == 1.0 and found.rising
    assert all(point.enough for point in found.months)

    flat = series([2] * 8, [20] * 8)
    assert (flat.growth, flat.rising) == (0.0, False)
    falling = series([4, 4, 4, 4, 4, 2, 2, 2], [20] * 8)
    assert (falling.growth, falling.rising) == (-0.5, False)
    slightly = series([5, 5, 5, 5, 5, 6, 6, 6], [50] * 8)
    assert slightly.growth == 0.2 and not slightly.rising  # under the 25% threshold


def test_a_release_spike_raises_raw_counts_but_not_share():
    # Three times the reviews in the last three months, and three times the complaints.
    spike = series([2, 2, 2, 2, 2, 6, 6, 6], [20, 20, 20, 20, 20, 60, 60, 60])
    assert [point.reviews for point in spike.months][-3:] == [6, 6, 6]
    assert (spike.previous_share, spike.recent_share) == (0.1, 0.1)
    assert (spike.growth, spike.rising) == (0.0, False)
    # The same counts with no spike in the totals would be a tripled share.
    assert series([2, 2, 2, 2, 2, 6, 6, 6], [20] * 8).growth == 2.0


def test_months_with_too_few_reviews_are_marked_not_read_as_zero():
    thin = series([0, 0, 2, 2, 2, 0, 3, 3], [3, 0, 20, 20, 20, 2, 20, 20])
    assert [point.enough for point in thin.months] == [
        False, False, True, True, True, False, True, True,
    ]  # fmt: skip
    assert thin.months[5].reviews == 0 and not thin.months[5].enough  # a gap, not a zero
    assert thin.recent_share == round(6 / 42, 4)

    too_thin = series([0, 0, 0, 1, 1, 1, 2, 2], [0, 0, 0, 4, 4, 4, 20, 20])
    assert too_thin.previous_share is None  # 12 reviews in the window: under 15
    assert (too_thin.growth, too_thin.rising) == (None, False)


def test_a_complaint_that_was_absent_and_now_appears_is_rising_without_a_growth_figure():
    new = series([0, 0, 0, 0, 0, 0, 3, 5], [20] * 8)
    assert (new.previous_share, new.growth, new.rising) == (0.0, None, True)
    absent = series([0] * 8, [20] * 8)
    assert (absent.recent_share, absent.rising) == (0.0, False)


def test_the_series_ends_at_the_month_of_the_latest_review():
    assert months_ending("2026-02", 4) == ["2025-11", "2025-12", "2026-01", "2026-02"]
    dates = [datetime(2026, 2, day, tzinfo=UTC) for day in (1, 2, 3)]
    totals = {"2016-05": 1, "2025-12": 30, "2026-02": 30}
    found = trend(dates, totals, SETTINGS)
    assert [p.month for p in found.months] == months_ending("2026-02", 8)
    assert found.months[-1].reviews == 3 and found.months[-3].total_reviews == 30
    assert trend([], {}, SETTINGS).months == []


def test_merged_pain_points_add_their_monthly_counts():
    first = series([1, 1, 1, 1, 1, 1, 1, 1], [20] * 8)
    second = series([0, 0, 0, 1, 1, 3, 3, 3], [20] * 8)
    merged = merged_trend([first.model_dump(), second.model_dump()], SETTINGS)
    assert merged == series([1, 1, 1, 2, 2, 4, 4, 4], [20] * 8)
    assert merged.rising and merged.growth == round((12 / 60 - 5 / 60) / (5 / 60), 4)


# Switching intent: finding candidates


@pytest.mark.parametrize(
    "text",
    [
        "I am switching to Tabby",
        "looking for an alternative",
        "Uninstalled it after the update",
        "moved to another app last month",
        "ye app chhod diya",
        "ab dusra app use karta hu",
        "Tabby is better than this",
    ],
)
def test_the_keyword_list_matches_english_and_hinglish_switching_phrases(text):
    assert KEYWORDS.search(text)


@pytest.mark.parametrize("text", ["payment failed again", "bahut slow hai", "worst app ever"])
def test_a_plain_complaint_is_not_a_keyword_match(text):
    assert not KEYWORDS.search(text)


def review(n: int, text: str, *, product="prod_a", language="en", sentiment="negative") -> Review:
    return Review(
        id=review_id("google_play", f"s{n}"),
        product_id=product,
        source="google_play",
        source_review_id=f"s{n}",
        reviewed_at=datetime(2026, 9, 1 + n % 28, tzinfo=UTC),
        language=language,
        sentiment=sentiment,
        text=text,
    )


def test_candidates_are_keyword_matches_and_reviews_close_to_a_seed_sentence():
    embedder = FakeEmbedder([["different app"]], dimension=384)
    texts = [f"payment problem number {n}" for n in range(12)]
    texts += ["I am switching to Tabby", "now I use a different app every day"]
    reviews = [review(n, text) for n, text in enumerate(texts)]
    embedded = list(zip(reviews, embedder.encode(texts).tolist(), strict=True))

    found = find_candidates(embedded, embedder, STRICT)
    # The keyword match comes first; the other has no keyword but sits next to a seed.
    assert [r.text for r in found] == [texts[12], texts[13]]
    assert not KEYWORDS.search(texts[13])
    assert find_candidates([], embedder, CONFIG) == []

    few = STRICT.model_copy(
        update={"switching": STRICT.switching.model_copy(update={"max_candidates": 1})}
    )
    assert [r.text for r in find_candidates(embedded, embedder, few)] == [texts[12]]


# Switching intent: classification


def fixture_reviews() -> list[Review]:
    languages = ["en", "hinglish", "en", "en", "en", "en"]
    return [
        review(n, text, language=language)
        for n, (text, language) in enumerate(zip(FIXTURE["reviews"], languages, strict=True))
    ]


def test_classification_with_the_recorded_response():
    reviews = fixture_reviews()
    provider = FakeProvider(FIXTURE["answer"])
    found = classify(gateway(groq=provider), reviews, NAMES, batch_size=20)

    assert [(item.intent, item.other_product) for item in found] == [
        ("leaving", "Tabby"),
        ("switched_from", "Tabby"),
        ("switched_to", "Tabby"),
        ("considering", None),
        ("leaving", None),  # it named only its own product
    ]
    assert [item.review_id for item in found] == [r.id for r in reviews[:4]] + [reviews[5].id]
    assert found[1].reason == "payments keep failing"  # a Hinglish review, summarised in English
    payload = json.loads(provider.requests[0].messages[1]["content"])
    assert payload["reviews"][0] == {"n": 1, "product": "Splitly", "text": FIXTURE["reviews"][0]}
    assert "Hinglish" in provider.requests[0].messages[0]["content"]


@pytest.mark.parametrize(("count", "size"), [(45, 20), (20, 20), (21, 20), (3, 50)])
def test_n_candidates_make_ceil_n_over_batch_calls(count, size):
    class NoneOfThem:
        calls = 0

        def complete(self, request):
            self.calls += 1
            sent = json.loads(request.messages[1]["content"])["reviews"]
            labels = [{"n": item["n"], "intent": "none"} for item in sent]
            return ProviderResponse(json.dumps({"labels": labels}))

    provider = NoneOfThem()
    reviews = [review(n, f"switching away, reason {n}") for n in range(count)]
    assert classify(gateway(groq=provider), reviews, NAMES, batch_size=size) == []
    assert provider.calls == math.ceil(count / size)


def test_an_invented_product_or_a_skipped_review_is_rejected():
    reviews = fixture_reviews()
    schema = batch_schema(reviews)
    assert len(schema.model_validate(FIXTURE["answer"]).labels) == 6

    invented = json.loads(json.dumps(FIXTURE["answer"]))
    invented["labels"][3]["other_product"] = "Tricount"  # review 4 names no product
    with pytest.raises(ValidationError, match="review 4 does not name the product 'Tricount'"):
        schema.model_validate(invented)
    skipped = {"labels": FIXTURE["answer"]["labels"][:5]}
    with pytest.raises(ValidationError, match="one label for each of reviews 1 to 6"):
        schema.model_validate(skipped)
    with pytest.raises(ValidationError, match="Input should be"):
        schema.model_validate({"labels": [FIXTURE["answer"]["labels"][0] | {"intent": "angry"}]})

    with pytest.raises(LlmFailed, match="does not name the product"):
        classify(gateway(groq=FakeProvider(invented)), reviews, NAMES, batch_size=20)


def test_the_switching_table_counts_each_direction_with_its_top_reasons():
    reviews = {
        r.id: r
        for r in [review(n, f"text {n}") for n in range(6)]
        + [review(9, "from the other side", product="prod_b")]
    }
    ids = list(reviews)

    def item(n, intent, other, reason=None):
        return SwitchingReview(review_id=ids[n], intent=intent, other_product=other, reason=reason)

    switching = [
        item(0, "leaving", "Tricount", "daily limit"),
        item(1, "switched_from", "tricount", "daily limit"),
        item(2, "considering", "TRICOUNT", "too many ads"),
        item(3, "switched_to", "Tricount", "simpler"),
        item(4, "considering", None, "daily limit"),
        item(5, "leaving", "Settle Up"),
        item(6, "leaving", "Splitly", "slow"),  # a Tabby review that leaves for Splitly
    ]
    table = switching_table(switching, reviews, NAMES)
    assert [(row.from_product, row.to_product, row.count) for row in table] == [
        ("Splitly", "Tricount", 3),  # three spellings, one row, the commonest spelling shown
        ("Splitly", None, 1),
        ("Splitly", "Settle Up", 1),
        ("Tabby", "Splitly", 1),
        ("Tricount", "Splitly", 1),
    ]
    assert table[0].reasons == ["daily limit", "too many ads"]  # most common first
    assert table[0].review_ids == sorted(ids[:3])
    assert table[2].reasons == []
    assert switching_table(switching, reviews, NAMES) == table  # the same input, the same table
    assert switching_table([], reviews, NAMES) == []


# In the stage


class Run:
    """Stage 3 on the invented themes, plus four reviews that are about switching."""

    EXTRA = [
        ("payment failed again so I am switching to Tabby", "en", "negative", "prod_a"),
        (
            "payment fail, ye app chhod diya, ab Tabby use karta hu",
            "hinglish",
            "negative",
            "prod_a",
        ),
        ("Moved to this from Tabby and it is simpler", "en", "positive", "prod_a"),
        ("ads everywhere, looking for an alternative", "en", "negative", "prod_b"),
    ]

    def __init__(self) -> None:
        self.switching_calls: list[dict] = []
        self.used = services(gateway(gemini=Labeller(), groq=self))
        self.used.reviews.upsert(
            review(100 + n, text, product=product, language=language, sentiment=sentiment)
            for n, (text, language, sentiment, product) in enumerate(self.EXTRA)
        )
        self.report = PainPointStage(STRICT)(None, {S1: COMPETITORS}, self.used)
        self.names, self.reviews = run_reviews(COMPETITORS, self.used.reviews)
        self.clusters = self.used.clusters.for_run("run_a")

    def complete(self, request) -> ProviderResponse:
        """Groq, the first provider for switching intent: answers from what each review says."""
        payload = json.loads(request.messages[1]["content"])
        self.switching_calls.append(payload)
        labels = []
        for item in payload["reviews"]:
            text = item["text"]
            if "switching to Tabby" in text:
                label = {"intent": "leaving", "other_product": "Tabby", "reason": "payments fail"}
            elif "chhod diya" in text:
                label = {
                    "intent": "switched_from",
                    "other_product": "Tabby",
                    "reason": "payments fail",
                }
            elif "Moved to this from" in text:
                label = {"intent": "switched_to", "other_product": "Tabby", "reason": "simpler"}
            elif "looking for an alternative" in text:
                label = {"intent": "considering", "other_product": None, "reason": "too many ads"}
            else:
                label = {"intent": "none"}
            labels.append({"n": item["n"], **label})
        return ProviderResponse(json.dumps({"labels": labels}))

    def problems(self, report: PainPointReport, **kwargs) -> str:
        found = report_problems(report, self.clusters, self.reviews, self.names, **kwargs)
        return "; ".join(found)


@pytest.fixture(scope="module")
def run() -> Run:
    return Run()


def test_the_report_carries_trends_and_switching_and_the_evidence_backs_them(run):
    report = run.report
    assert run.problems(report) == ""
    assert len(run.switching_calls) == 1 and len(run.switching_calls[0]["reviews"]) == 4

    assert sorted((s.intent, s.other_product or "") for s in report.switching_reviews) == [
        ("considering", ""), ("leaving", "Tabby"), ("switched_from", "Tabby"),
        ("switched_to", "Tabby"),
    ]  # fmt: skip
    assert [(row.from_product, row.to_product, row.count) for row in report.switching_table] == [
        ("Splitly", "Tabby", 2),
        ("Tabby", None, 1),
        ("Tabby", "Splitly", 1),
    ]
    payment, ads = report.pain_points[0], report.pain_points[2]
    assert payment.label.startswith("Payments fail") and len(payment.switching_review_ids) == 2
    assert len(ads.switching_review_ids) == 1
    # The review that came from Tabby is praise: it is in no pain point, but it is in the table.
    in_points = {r for point in report.pain_points for r in point.switching_review_ids}
    assert len(in_points) == 3 and len(report.switching_reviews) == 4

    assert report.trend_settings.months == CONFIG.trends.months
    for point in report.pain_points:
        assert len(point.trend.months) == 12
        assert sum(month.reviews for month in point.trend.months) == point.review_count
        assert all(month.total_reviews >= month.reviews for month in point.trend.months)


def test_a_trend_or_switching_claim_the_reviews_do_not_back_is_rejected(run):
    report = run.report
    first = report.pain_points[0]

    def changed(**changes) -> PainPointReport:
        point = first.model_copy(update=changes)
        return report.model_copy(update={"pain_points": [point, *report.pain_points[1:]]})

    rising = first.trend.model_copy(update={"rising": not first.trend.rising})
    assert "trend does not match the dates" in run.problems(changed(trend=rising))
    assert "switching_review_ids must be the cluster's reviews" in run.problems(
        changed(switching_review_ids=first.switching_review_ids[:1])
    )

    lying = report.switching_reviews[3].model_copy(update={"other_product": "Tricount"})
    forged = report.model_copy(update={"switching_reviews": [*report.switching_reviews[:3], lying]})
    assert "does not name 'Tricount'" in run.problems(forged)
    assert "switching table does not match" in run.problems(forged)
    fewer = report.model_copy(update={"switching_table": report.switching_table[:1]})
    assert "switching table does not match" in run.problems(fewer)


def test_a_merge_at_the_checkpoint_adds_trends_and_unites_switching_reviews(run):
    report = run.report
    payment, _, ads = report.pain_points
    merged = PainPointReport.model_validate(
        edit_pain_points(report.model_dump(mode="json"), merge=[["1", "3"]])
    )
    point = merged.pain_points[0]
    assert point.review_count == payment.review_count + ads.review_count
    assert sorted(point.switching_review_ids) == sorted(
        payment.switching_review_ids + ads.switching_review_ids
    )
    for month, a, b in zip(point.trend.months, payment.trend.months, ads.trend.months, strict=True):
        assert month.reviews == a.reviews + b.reviews and month.total_reviews == a.total_reviews
    assert merged.switching_table == report.switching_table
    assert run.problems(merged, ranked_by_score=False) == ""

    dropped = PainPointReport.model_validate(
        edit_pain_points(report.model_dump(mode="json"), drop=["1"])
    )
    assert len(dropped.switching_reviews) == 4  # the reviews are still about switching
    assert run.problems(dropped, ranked_by_score=False) == ""


def test_the_exports_show_the_trend_and_the_switching_table(run):
    export = export_report(run.report, run.reviews, run.names, title="Splitly")
    export = json.loads(json.dumps(export))
    point = export["pain_points"][0]
    assert sorted(s["intent"] for s in point["switching"]) == ["leaving", "switched_from"]
    leaving = next(s for s in point["switching"] if s["intent"] == "leaving")
    assert leaving["text"].startswith("payment failed again")
    assert (leaving["other_product"], leaving["reason"]) == ("Tabby", "payments fail")
    assert "switching_review_ids" not in point
    for month in point["trend"]["months"]:
        if month["enough"]:
            assert month["share"] == month["reviews"] / month["total_reviews"]
        else:
            assert month["share"] is None  # a thin month has no share, not a share of zero
    assert any(not month["enough"] for month in point["trend"]["months"])
    assert export["switching_review_count"] == 4

    markdown = render_markdown(export)
    assert markdown.count("- Trend: ") == 3
    assert "- Reviews about switching products: 2" in markdown
    assert "## Switching" in markdown and "4 reviews talk about switching products." in markdown
    assert "| Splitly | Tabby | 2 | payments fail |" in markdown
    assert "| Tabby | not said | 1 | too many ads |" in markdown
