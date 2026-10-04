import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import run_input_data
from productfoundry.cli import main
from productfoundry.core.competitors import Competitor, CompetitorList
from productfoundry.core.errors import ProductFoundryError, StageOutputInvalid
from productfoundry.core.ids import review_id
from productfoundry.core.pain_points import RANKING_FORMULA, PainPointReport, pain_point_score
from productfoundry.core.reviews import Review
from productfoundry.llm import Gateway, LlmFailed, ProviderError, ProviderResponse, load_routing
from productfoundry.llm.fakes import InMemoryCallStore, InMemoryUsageStore
from productfoundry.ml.config import load_config
from productfoundry.ml.embeddings import FakeEmbedder
from productfoundry.orchestrator import (
    PIPELINE,
    InMemoryRunStore,
    Orchestrator,
    RunStatus,
    Services,
)
from productfoundry.orchestrator.checkpoints import edit_pain_points
from productfoundry.stages.fakes import FAKE_STAGES
from productfoundry.stages.s3_pain_points import PainPointStage, run_reviews
from productfoundry.stages.s3_pain_points.export import export_report, render_markdown, shorten
from productfoundry.stages.s3_pain_points.labelling import label_schema
from productfoundry.stages.s3_pain_points.validation import check_report, report_problems
from productfoundry.storage.memory import InMemoryClusterStore, InMemoryReviewStore

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/llm/cluster_labelling.json").read_text("utf-8")
)
CONFIG = load_config()
DAY = datetime(2026, 6, 1, tzinfo=UTC)
S1, S2, S3 = "s1_competitors", "s2_reviews", "s3_pain_points"
THEMES = {"payment": 16, "ads": 12, "login": 10}  # reviews per theme


def competitor(product: str, name: str, **extra) -> Competitor:
    return Competitor(
        id=product,
        name=name,
        url=f"https://{name.lower()}.example",
        positioning="Splits bills between friends.",
        target_users="Flatmates",
        reason="Does the same job.",
        **extra,
    )


COMPETITORS = CompetitorList(
    competitors=[competitor("prod_a", "Splitly", is_incumbent=True), competitor("prod_b", "Tabby")]
)


def stored_reviews() -> list[Review]:
    """Three themes in English and Hinglish, some praise, and one review that is not analysed."""
    rows = []
    for word, count in THEMES.items():
        for i in range(count):
            hinglish = i % 3 == 0
            text = f"{word} ka issue hai, {i} baar ho gaya" if hinglish else f"{word} problem {i}"
            sentiment = "mixed" if i % 4 == 1 else "negative"
            rows.append((text, "hinglish" if hinglish else "en", sentiment, i))
    rows += [(f"I love the {i} colours", "en", "positive", i) for i in range(5)]
    rows.append(("यह ऐप बहुत धीमा है", "hi", None, 0))
    return [
        Review(
            id=review_id("google_play", f"r{n}"),
            product_id="prod_a" if i % 2 else "prod_b",
            source="google_play",
            source_review_id=f"r{n}",
            url=f"https://play.google.com/store/apps/details?id=app.example&reviewId=r{n}",
            reviewed_at=DAY + timedelta(days=7 * i),
            rating=1 if sentiment == "negative" else 3,
            language=language,
            sentiment=sentiment,
            text=text,
        )
        for n, (text, language, sentiment, i) in enumerate(rows)
    ]


class Labeller:
    """Answers from the hand-written fixture, quoting the reviews it was actually sent."""

    def __init__(self, *, junk: str | None = None, change=None) -> None:
        self.payloads: list[dict] = []
        self._junk = junk
        self._change = change  # tampers with every answer, or only the first when it returns

    def complete(self, request) -> ProviderResponse:
        payload = json.loads(request.messages[1]["content"])
        if request.task == "switching_intent":  # nothing in these reviews is about switching
            labels = [{"n": review["n"], "intent": "none"} for review in payload["reviews"]]
            return ProviderResponse(json.dumps({"labels": labels}))
        self.payloads.append(payload)
        theme = next(word for word in THEMES if word in payload["reviews"][0]["text"])
        if theme == self._junk:
            return ProviderResponse(json.dumps(FIXTURE["junk"]))
        answer = dict(FIXTURE["answers"][theme])
        answer["quotes"] = [review["n"] for review in payload["reviews"]][:4]
        answer["glosses"] = [
            {"n": review["n"], "english": FIXTURE["gloss"]}
            for review in payload["reviews"]
            if review["language"] == "hinglish"
        ]
        if self._change is not None:
            self._change(answer, len(self.payloads))
        return ProviderResponse(json.dumps(answer))


def gateway(**providers) -> Gateway:
    return Gateway(
        load_routing(), providers, InMemoryCallStore(), InMemoryUsageStore(), sleep=lambda _: None
    )


def services(llm, reviews=None, clusters=None) -> Services:
    reviews = reviews or InMemoryReviewStore()
    for item in COMPETITORS.competitors:
        reviews.ensure_product(item)
    reviews.upsert(stored_reviews())
    return Services(
        run_id="run_a",
        seed=5,
        llm=llm,
        reviews=reviews,
        clusters=clusters or InMemoryClusterStore(),
        embedder=FakeEmbedder(
            [["payment"], ["ads"], ["login"]], dimension=CONFIG.embeddings.dimension
        ),
    )


def run_stage(provider=None, **kwargs) -> tuple[PainPointReport, Services, Labeller]:
    provider = provider or Labeller(**kwargs)
    used = services(gateway(gemini=provider))
    report = PainPointStage(CONFIG)(None, {S1: COMPETITORS}, used)
    return report, used, provider


def evidence(used: Services):
    """What the validator checks a report against: the run's clusters, its stored reviews
    and the names of its products."""
    names, reviews = run_reviews(COMPETITORS, used.reviews)
    return used.clusters.for_run("run_a"), reviews, names


# The stage


def test_clusters_become_a_ranked_report_with_counts_from_the_stored_reviews():
    report, used, provider = run_stage()
    clusters, reviews, names = evidence(used)

    assert [p.label for p in report.pain_points] == [
        "Payments fail after money is debited",
        "Login code never arrives",
        "Too many full-screen ads",
    ]
    assert [p.rank for p in report.pain_points] == [1, 2, 3]
    assert [p.review_count for p in report.pain_points] == [16, 10, 12]
    assert [p.negative_share for p in report.pain_points] == [0.75, 0.7, 0.75]
    assert [p.severity for p in report.pain_points] == [5, 4, 2]
    assert [p.score for p in report.pain_points] == [14.0, 6.8, 4.2]
    assert all(p.product_ids == ["prod_a", "prod_b"] for p in report.pain_points)
    assert report.ranking_formula == RANKING_FORMULA and "severity" in RANKING_FORMULA
    assert report.junk_clusters == []
    assert report.language_counts.model_dump() == {
        "english": 29, "hinglish": 14, "not_analysed": 1,
    }  # fmt: skip
    assert (report.clustered_reviews, report.noise_reviews) == (38, 0)
    assert report_problems(report, clusters, reviews, names) == []
    assert len(provider.payloads) == 3  # one call per cluster


def test_only_the_representative_reviews_of_a_cluster_are_sent_to_the_llm():
    _, used, provider = run_stage()
    clusters, reviews, names = evidence(used)
    for payload, cluster in zip(provider.payloads, clusters, strict=True):
        assert payload["cluster_size"] == cluster.size
        assert payload["products"] == ["Splitly", "Tabby"]
        assert [r["text"] for r in payload["reviews"]] == [
            reviews[review].text for review in cluster.representative_ids
        ]
        assert len(payload["reviews"]) == CONFIG.clustering.representatives < cluster.size


def test_quotes_are_review_ids_of_the_cluster_and_hinglish_quotes_carry_a_gloss():
    report, used, _ = run_stage()
    clusters, reviews, names = evidence(used)
    members = {cluster.id: set(cluster.review_ids) for cluster in clusters}
    glossed = 0
    for point in report.pain_points:
        assert 3 <= len(point.quote_review_ids) <= 5
        assert set(point.quote_review_ids) <= members[point.cluster_id]
        hinglish = {r for r in point.quote_review_ids if reviews[r].language == "hinglish"}
        assert set(point.quote_glosses) == hinglish
        glossed += len(hinglish)
    assert glossed  # the fixture does put Hinglish reviews among the quotes
    # The gloss lives in the report only: the stored review keeps its own words.
    assert all(FIXTURE["gloss"] not in review.text for review in reviews.values())


def test_the_ranking_formula_is_a_pure_function_of_size_negative_share_and_severity():
    assert pain_point_score(16, 0.75, 5) == 14.0
    assert pain_point_score(10, 1.0, 5) == 10.0
    assert pain_point_score(10, 0.0, 5) == 5.0  # a mixed cluster counts half
    assert pain_point_score(10, 1.0, 1) == 2.0
    assert pain_point_score(40, 0.5, 2) > pain_point_score(8, 1.0, 5)  # size can outweigh severity


def test_a_junk_cluster_is_listed_separately_with_its_reason():
    report, used, _ = run_stage(junk="ads")
    assert [p.label for p in report.pain_points] == [
        "Payments fail after money is debited",
        "Login code never arrives",
    ]
    (junk,) = report.junk_clusters
    assert (junk.review_count, junk.reason) == (12, FIXTURE["junk"]["junk_reason"])
    assert report_problems(report, *evidence(used)) == []


def test_too_few_negative_reviews_give_an_empty_report_and_no_llm_call():
    provider = Labeller()
    store = InMemoryReviewStore()
    used = Services(
        run_id="run_a",
        llm=gateway(gemini=provider),
        reviews=store,
        clusters=InMemoryClusterStore(),
        embedder=FakeEmbedder(),
    )
    store.ensure_product(COMPETITORS.competitors[0])
    store.upsert([r for r in stored_reviews() if r.product_id == "prod_a"][:5])

    report = PainPointStage(CONFIG)(None, {S1: COMPETITORS}, used)
    assert report.pain_points == [] and provider.payloads == []
    assert (report.clustered_reviews, report.noise_reviews) == (5, 5)


def test_the_stage_says_which_service_is_missing():
    with pytest.raises(ProductFoundryError, match="needs a cluster store"):
        PainPointStage(CONFIG)(None, {S1: COMPETITORS}, Services(reviews=InMemoryReviewStore()))


# An answer that cites what it was not shown


def cite_outside(answer, call):
    answer["quotes"] = [1, 2, 9]


def test_a_response_citing_a_review_outside_the_cluster_is_rejected():
    bad = Labeller(change=cite_outside)
    used = services(gateway(gemini=bad, groq=Labeller(change=cite_outside)))
    with pytest.raises(LlmFailed, match="not in the sample"):
        PainPointStage(CONFIG)(None, {S1: COMPETITORS}, used)
    assert len(bad.payloads) == 2  # asked again once, then the next provider


def test_a_rejected_answer_falls_back_to_the_next_provider():
    report, _, _ = run_stage()
    good = Labeller()
    used = services(gateway(gemini=Labeller(change=cite_outside), groq=good))
    assert PainPointStage(CONFIG)(None, {S1: COMPETITORS}, used) == report
    assert len(good.payloads) == 3


def sample(*languages: str) -> list[Review]:
    reviews = stored_reviews()[: len(languages)]
    return [
        r.model_copy(update={"language": lang}) for r, lang in zip(reviews, languages, strict=True)
    ]


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ({"quotes": [1, 2, 6]}, "not in the sample"),
        ({"quotes": [1, 2, 2]}, "must not repeat"),
        ({"quotes": [1, 2]}, "expected 3 to 5 quotes"),
        ({"quotes": [1, 2, 3], "glosses": []}, "Hinglish quotes need an English gloss"),
        ({"label": " "}, "label is required"),
        ({"severity": None}, "severity is required"),
        ({"severity": 6}, "less than or equal to 5"),
        ({"junk": True}, "needs junk_reason"),
    ],
)
def test_the_answer_schema_rejects(answer, message):
    schema = label_schema(sample("en", "en", "hinglish", "en", "en"))
    complete = FIXTURE["answers"]["payment"] | {
        "quotes": [1, 2, 4],
        "glosses": [{"n": 3, "english": "It failed."}],
    }
    assert schema.model_validate(complete).severity == 5
    assert schema.model_validate(FIXTURE["junk"]).junk
    with pytest.raises(ValidationError, match=message):
        schema.model_validate(complete | answer)


# The evidence check


def test_a_report_that_the_stored_evidence_does_not_back_is_rejected():
    report, used, _ = run_stage()
    clusters, reviews, names = evidence(used)
    first, second = report.pain_points[0], report.pain_points[1]

    def problems(**changes) -> str:
        point = first.model_copy(update=changes)
        changed = report.model_copy(update={"pain_points": [point, *report.pain_points[1:]]})
        return "; ".join(report_problems(changed, clusters, reviews, names))

    foreign = [*first.quote_review_ids[:2], second.quote_review_ids[0]]
    assert "quotes reviews that are not in the cluster" in problems(
        quote_review_ids=foreign, quote_glosses={}
    )
    invented = [*first.quote_review_ids[:2], "rev_doesnotexist"]
    assert "not in the cluster" in problems(quote_review_ids=invented, quote_glosses={})
    assert "review_count is 17, the cluster holds 16" in problems(review_count=17)
    assert "negative_share is 1.0" in problems(negative_share=1.0)
    assert "product_ids" in problems(product_ids=["prod_a"])
    assert "score is 99.0, the ranking formula gives 14.0" in problems(score=99.0)
    assert "score is 14.0, the ranking formula gives 11.2" in problems(severity=4)
    assert "no such cluster in this run: cl_invented" in problems(cluster_id="cl_invented")
    assert "quote_glosses must translate the Hinglish quotes" in problems(quote_glosses={})

    def report_with(**changes) -> str:
        return "; ".join(
            report_problems(report.model_copy(update=changes), clusters, reviews, names)
        )

    assert "not ordered by score" in report_with(
        pain_points=[
            second.model_copy(update={"rank": 1}),
            first.model_copy(update={"rank": 2}),
            report.pain_points[2],
        ]
    )
    counts = report.language_counts.model_copy(update={"hinglish": 0})
    assert "language_counts do not match" in report_with(language_counts=counts)
    assert "clustered_reviews is 40" in report_with(clustered_reviews=40)
    assert "noise_reviews is 2" in report_with(noise_reviews=2)

    with pytest.raises(StageOutputInvalid, match="does not match the stored evidence"):
        check_report(report.model_copy(update={"noise_reviews": 2}), clusters, reviews, names)
    check_report(report, clusters, reviews, names)


def test_a_wrong_junk_count_or_an_unknown_junk_cluster_is_rejected():
    report, used, _ = run_stage(junk="ads")
    clusters, reviews, names = evidence(used)
    junk = report.junk_clusters[0]
    wrong = report.model_copy(
        update={"junk_clusters": [junk.model_copy(update={"review_count": 3})]}
    )
    assert (
        "review_count is 3, the cluster holds 12"
        in report_problems(wrong, clusters, reviews, names)[0]
    )
    assert "no such cluster" in report_problems(report, clusters[:2], reviews, names)[0]


# The checkpoint


def edited(report: PainPointReport, **edits) -> PainPointReport:
    return PainPointReport.model_validate(edit_pain_points(report.model_dump(mode="json"), **edits))


def test_merged_pain_points_keep_all_their_reviews():
    report, used, _ = run_stage()
    clusters, reviews, names = evidence(used)
    payment, login, ads = report.pain_points

    merged = edited(report, merge=[["3", "1"]])
    point = merged.pain_points[0]
    assert [p.label for p in merged.pain_points] == [ads.label, login.label]
    assert (point.cluster_id, point.merged_cluster_ids) == (ads.cluster_id, [payment.cluster_id])
    assert point.review_count == 28 and point.negative_share == 0.75
    assert (point.severity, point.severity_reason) == (5, payment.severity_reason)
    assert point.score == pain_point_score(28, 0.75, 5)
    assert len(point.quote_review_ids) == 5
    assert point.quote_review_ids[:2] == [ads.quote_review_ids[0], payment.quote_review_ids[0]]
    assert set(point.quote_glosses) == {
        r for r in point.quote_review_ids if reviews[r].language == "hinglish"
    }
    assert [p.rank for p in merged.pain_points] == [1, 2]
    assert report_problems(merged, clusters, reviews, names, ranked_by_score=False) == []

    everything = edited(report, merge=[["1", "2", "3"]])
    assert everything.pain_points[0].review_count == 38
    assert everything.pain_points[0].negative_share == 28 / 38
    assert report_problems(everything, clusters, reviews, names, ranked_by_score=False) == []


def test_drop_rename_and_rank_keep_counts_correct():
    report, used, _ = run_stage()
    clusters, reviews, names = evidence(used)
    payment, login, ads = report.pain_points

    changed = edited(report, drop=["2"], rename={"3": "Ads after every tap"}, rank=["3"])
    assert [(p.rank, p.label, p.review_count) for p in changed.pain_points] == [
        (1, "Ads after every tap", 12),
        (2, payment.label, 16),
    ]
    assert changed.pain_points[0].score == ads.score
    assert (changed.clustered_reviews, changed.noise_reviews) == (38, 0)
    assert report_problems(changed, clusters, reviews, names, ranked_by_score=False) == []

    by_id = edited(report, drop=[login.cluster_id], rank=[ads.cluster_id, payment.cluster_id])
    assert [p.cluster_id for p in by_id.pain_points] == [ads.cluster_id, payment.cluster_id]
    assert edited(report) == report


@pytest.mark.parametrize(
    ("edits", "message"),
    [
        ({"drop": ["7"]}, "no pain point '7'; use a rank from 1 to 3 or a cluster id"),
        ({"merge": [["1"]]}, "two or more different pain points"),
        ({"merge": [["1", "1"]]}, "two or more different pain points"),
        ({"merge": [["1", "2"], ["2", "3"]]}, "dropped or merged away"),
        ({"drop": ["1"], "rename": {"1": "New"}}, "cannot rename"),
        ({"rank": ["1", "1"]}, "appears once"),
    ],
)
def test_an_edit_that_names_a_missing_pain_point_is_refused(edits, message):
    report, _, _ = run_stage()
    with pytest.raises(ProductFoundryError, match=message):
        edit_pain_points(report.model_dump(mode="json"), **edits)


# Export


def test_the_markdown_report_shows_counts_and_linked_quotes_for_every_theme():
    report, used, _ = run_stage(junk="ads")
    names, reviews = run_reviews(COMPETITORS, used.reviews)
    export = export_report(report, reviews, names, title="Splitly")
    markdown = render_markdown(export)

    assert markdown.startswith("# Pain-point report: Splitly\n")
    assert "44 reviews: 29 English, 14 Hinglish, 1 not analysed" in markdown
    assert "38 negative or mixed reviews were grouped into themes; 0 fitted no theme." in markdown
    assert f"Ranking: {RANKING_FORMULA}." in markdown
    assert "## 1. Payments fail after money is debited" in markdown
    assert "Severity 5/5 · 16 reviews · 75% negative · score 14" in markdown
    assert "## 2. Login code never arrives" in markdown
    assert "- Products: Splitly, Tabby" in markdown
    assert "## Groups that are not a pain point" in markdown and "(12 reviews)" in markdown

    for point in report.pain_points:
        for quote in point.quote_review_ids:
            review = reviews[quote]
            assert f"> {review.text}" in markdown
            link = f"[Google Play, {review.reviewed_at:%Y-%m-%d}]({review.url})"
            assert link in markdown and f"`{quote}`" in markdown
    # A Hinglish quote keeps its words, and its English gloss is marked as a translation.
    assert "ka issue hai" in markdown
    assert f"> *Translation: {FIXTURE['gloss']}*" in markdown
    assert markdown.count("*Translation:") == sum(len(p.quote_glosses) for p in report.pain_points)


def test_the_json_export_resolves_every_quote_to_text_url_and_date():
    report, used, _ = run_stage()
    names, reviews = run_reviews(COMPETITORS, used.reviews)
    export = json.loads(json.dumps(export_report(report, reviews, names, title="Splitly")))

    assert [p["rank"] for p in export["pain_points"]] == [1, 2, 3]
    for exported, point in zip(export["pain_points"], report.pain_points, strict=True):
        assert [q["review_id"] for q in exported["quotes"]] == point.quote_review_ids
        for quote in exported["quotes"]:
            review = reviews[quote["review_id"]]
            assert (quote["text"], quote["url"]) == (review.text, review.url)
            assert quote["date"] == review.reviewed_at.date().isoformat()
            assert (quote["translation"] is not None) == (review.language == "hinglish")
    assert export["language_counts"] == {"english": 29, "hinglish": 14, "not_analysed": 1}

    del reviews[report.pain_points[0].quote_review_ids[0]]
    with pytest.raises(StageOutputInvalid, match="is not stored"):
        export_report(report, reviews, names, title="Splitly")


def test_quotes_are_short_and_cannot_inject_markdown():
    long = "The payment failed and " + "it keeps happening " * 40
    assert len(shorten(long)) <= 281 and shorten(long).endswith("…")
    assert shorten("Short one.") == "Short one."

    report, used, _ = run_stage()
    names, reviews = run_reviews(COMPETITORS, used.reviews)
    quoted = report.pain_points[0].quote_review_ids[0]
    reviews[quoted] = reviews[quoted].model_copy(
        update={"text": "see [this](https://evil.example) <b>now</b> " + long}
    )
    markdown = render_markdown(export_report(report, reviews, names, title="Splitly"))
    assert "\\[this\\](https://evil.example) \\<b\\>now" in markdown
    assert long not in markdown


# In a run


def orchestrator_with(used: Services, store=None) -> Orchestrator:
    stages = FAKE_STAGES | {S1: lambda *_: COMPETITORS, S3: PainPointStage(CONFIG)}
    return Orchestrator(store or InMemoryRunStore(), stages, pipeline=PIPELINE[:3], services=used)


def run_to_checkpoint(orchestrator: Orchestrator, run_input):
    run = orchestrator.resume(orchestrator.create_run(run_input, seed=5).id)
    orchestrator.approve(run.id)
    return orchestrator.resume(run.id)


def test_the_run_stops_at_the_checkpoint_and_the_edit_is_what_is_kept(run_input):
    provider = Labeller()
    orchestrator = orchestrator_with(services(gateway(gemini=provider)))
    run = run_to_checkpoint(orchestrator, run_input)
    assert run.status is RunStatus.AWAITING_APPROVAL
    original = copy.deepcopy(run.stage(S3).output)
    assert original["schema_version"] == 3 and len(original["pain_points"]) == 3

    orchestrator.approve(run.id, edit_pain_points(original, merge=[["1", "2"]]))
    run = orchestrator.resume(run.id)
    assert run.status is RunStatus.COMPLETED
    assert run.stage(S3).output == original  # the original is kept
    assert [p["review_count"] for p in run.stage(S3).effective_output["pain_points"]] == [26, 12]

    # Running the stage again gives the same clusters, so every label comes from the cache.
    run = orchestrator.resume(run.id, from_stage=S3)
    assert run.stage(S3).output == original and len(provider.payloads) == 3


def test_a_run_out_of_quota_pauses_and_resumes_where_it_stopped(run_input):
    refusing = [True]

    def out_of_quota(answer, call):
        if refusing[0] and call >= 2:
            raise ProviderError("rate_limit", "429")

    provider = Labeller(change=out_of_quota)
    orchestrator = orchestrator_with(services(gateway(gemini=provider)))
    run = run_to_checkpoint(orchestrator, run_input)
    assert run.status is RunStatus.PAUSED_QUOTA and "cluster_labelling" in run.pause_reason

    refusing[0] = False
    run = orchestrator.resume(run.id)
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert len(run.stage(S3).output["pain_points"]) == 3
    assert len(provider.payloads) == 5  # 1 answered, 2 refused, then only the 2 still missing


def test_a_report_the_evidence_does_not_back_fails_the_stage(run_input, monkeypatch):
    # The stage checks its own report before returning it. A wrong score stands in for a bug.
    monkeypatch.setattr("productfoundry.stages.s3_pain_points.pain_point_score", lambda *_: 1.0)
    orchestrator = orchestrator_with(services(gateway(gemini=Labeller())))
    run = run_to_checkpoint(orchestrator, run_input)
    assert run.status is RunStatus.FAILED
    assert "does not match the stored evidence" in run.error
    assert run.stage(S3).output is None


# The CLI, against the database


def test_the_cli_exports_the_report_and_edits_it_at_the_checkpoint(
    sessions, db_engine, run_input, tmp_path, monkeypatch, capsys
):
    from productfoundry.storage import PostgresClusterStore, PostgresRunStore, ReviewRepository

    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    used = services(
        gateway(gemini=Labeller()), ReviewRepository(sessions), PostgresClusterStore(sessions)
    )
    run = run_to_checkpoint(orchestrator_with(used, PostgresRunStore(sessions)), run_input)
    assert run.status is RunStatus.AWAITING_APPROVAL

    def cli(*args) -> tuple[int, str, str]:
        code = main(list(args))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    code, out, _ = cli("status", run.id)
    assert code == 0 and "pain points\n  1. Payments fail after money is debited" in out
    assert "severity 5/5, 16 reviews, 75% negative, score 14" in out
    assert f"quotes  productfoundry report {run.id}" in out

    code, out, _ = cli("report", run.id)
    assert code == 0 and out.startswith("# Pain-point report: Walnut\n")
    assert out.count("](https://play.google.com/store/apps/details?id=app.example") == 12

    target = tmp_path / "exports" / "report.json"  # the directory does not exist yet
    code, out, _ = cli("report", run.id, "--format", "json", "--out", str(target))
    exported = json.loads(target.read_text("utf-8"))
    assert code == 0 and len(exported["pain_points"]) == 3
    code, _, err = cli("report", run.id, "--out", str(tmp_path))
    assert code == 1 and err.startswith("error: cannot write")

    # An edit that the stored evidence does not back is refused, and the run still waits.
    tampered = copy.deepcopy(run.stage(S3).output)
    tampered["pain_points"][0]["review_count"] = 15
    tampered["pain_points"][0]["score"] = pain_point_score(15, 0.75, 5)
    edit_file = tmp_path / "edit.json"
    edit_file.write_text(json.dumps(tampered), encoding="utf-8")
    code, _, err = cli("approve", run.id, "--edit", str(edit_file))
    assert code == 1 and "review_count is 15, the cluster holds 16" in err

    code, _, err = cli("approve", run.id, "--remove", "Tabby")
    assert code == 1 and "competitor checkpoint" in err
    code, _, err = cli("approve", run.id, "--rename", "2")
    assert code == 1 and "--rename takes N=LABEL" in err

    code, out, _ = cli(
        "approve", run.id, "--merge", "1,2", "--rename", "1=Cannot pay or log in",
        "--drop", "3",
    )  # fmt: skip
    assert code == 0 and "s3_pain_points   completed (edited)" in out
    # The run goes on to the PRD stage, which finds no provider: tests never call one.
    assert "s4_prd           failed: LlmFailed: no provider" in out

    code, out, _ = cli("report", run.id, "--format", "json")
    (point,) = json.loads(out)["pain_points"]
    assert (point["label"], point["review_count"]) == ("Cannot pay or log in", 26)
    assert len(point["merged_cluster_ids"]) == 1 and len(point["quotes"]) == 5


def test_the_report_command_needs_a_report_and_the_database(tmp_path, capsys):
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps(run_input_data()), encoding="utf-8")
    base = ["--memory", "--fake-stages", "--state-file", str(tmp_path / "runs.json")]
    assert main([*base, "run", "--input", str(input_file)]) == 0
    run_id = capsys.readouterr().out.split()[1]

    assert main([*base, "report", run_id]) == 1
    assert "has no pain-point report yet" in capsys.readouterr().err

    assert main([*base, "approve", run_id]) == 0
    out = capsys.readouterr().out
    assert "pain points\n  1. Payments fail  severity 5/5, 8 reviews, 80% negative" in out
    assert main([*base, "report", run_id]) == 1
    assert "--memory does not keep" in capsys.readouterr().err

    assert main([*base, "approve", run_id, "--rename", "1=Money is lost"]) == 0
    assert main([*base, "status", run_id, "--output", S3]) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{") :])["pain_points"][0]["label"] == "Money is lost"
