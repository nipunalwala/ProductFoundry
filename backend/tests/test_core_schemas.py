import pytest
from pydantic import ValidationError

from conftest import run_input_data
from productfoundry.core.base import schema_version_of
from productfoundry.core.competitors import CompetitorList
from productfoundry.core.ids import new_id
from productfoundry.core.pain_points import PainPointReport
from productfoundry.core.regions import REGIONS
from productfoundry.core.registry import SCHEMAS
from productfoundry.core.reviews import ReviewSet
from productfoundry.core.run_input import RunInput


def competitor(**overrides) -> dict:
    data = {
        "id": "prod_a",
        "name": "Walnut",
        "url": "https://walnut.example",
        "positioning": "Automatic expense tracking from SMS.",
        "target_users": "Salaried people",
        "reason": "Named incumbent.",
    }
    data.update(overrides)
    return data


def product_counts(**overrides) -> dict:
    data = {
        "product_id": "prod_a",
        "total": 10,
        "by_source": {"google_play": 7, "app_store": 3},
        "by_language": {"en": 5, "hinglish": 3, "hi": 2},
        "by_sentiment": {"negative": 5, "positive": 3},
        "not_analysed": 2,
    }
    data.update(overrides)
    return data


def pain_point(**overrides) -> dict:
    data = {
        "cluster_id": "cl_a",
        "rank": 1,
        "label": "Payments fail",
        "description": "Payments fail after money is debited.",
        "severity": 4,
        "severity_reason": "Money is lost.",
        "review_count": 12,
        "negative_share": 0.9,
        "product_ids": ["prod_a"],
        "quote_review_ids": ["rev_1", "rev_2", "rev_3"],
        "score": 3.6,
        "trend": {"months": []},
    }
    data.update(overrides)
    return data


def report(**overrides) -> dict:
    data = {
        "pain_points": [pain_point()],
        "ranking_formula": "score = severity * negative_share",
        "language_counts": {"english": 5, "hinglish": 3, "not_analysed": 2},
        "clustered_reviews": 20,
        "noise_reviews": 2,
        "trend_settings": {
            "months": 12,
            "window_months": 3,
            "min_month_reviews": 5,
            "min_window_reviews": 15,
            "rising_threshold": 0.25,
        },
    }
    data.update(overrides)
    return data


# RunInput


def test_run_input_accepts_a_valid_mode_2_input():
    run_input = RunInput.model_validate(run_input_data())
    assert run_input.schema_version == 1
    assert run_input.incumbent.store_ids.google_play == "com.example.walnut"


def test_mode_2_requires_an_incumbent():
    with pytest.raises(ValidationError, match="requires an incumbent"):
        RunInput.model_validate(run_input_data(incumbent=None))


@pytest.mark.parametrize("mode", ["new_idea", "new_feature"])
def test_other_modes_do_not_require_an_incumbent(mode):
    assert RunInput.model_validate(run_input_data(mode=mode, incumbent=None)).incumbent is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "clone"},
        {"platforms": ["windows"]},
        {"platforms": []},
        {"platforms": ["android", "android"]},
        {"region": "India"},
        {"region": "ZZ"},
        {"idea": "   "},
        {"schema_version": 2},
        {"unknown_field": 1},
    ],
)
def test_run_input_rejects_values_outside_the_contract(overrides):
    with pytest.raises(ValidationError):
        RunInput.model_validate(run_input_data(**overrides))


def test_region_is_normalised_to_upper_case():
    assert RunInput.model_validate(run_input_data(region=" in ")).region == "IN"


def test_region_list_is_the_full_iso_set():
    assert len(REGIONS) == 249
    assert all(len(code) == 2 and code.isupper() for code in REGIONS)


@pytest.mark.parametrize(
    "store_ids",
    [{"google_play": "not a package"}, {"google_play": "single"}, {"app_store": "id123"}],
)
def test_store_ids_are_constrained(store_ids):
    incumbent = {"name": "Walnut", "store_ids": store_ids}
    with pytest.raises(ValidationError):
        RunInput.model_validate(run_input_data(incumbent=incumbent))


def test_incumbent_urls_must_be_http():
    incumbent = {"name": "Walnut", "urls": ["ftp://walnut.example"]}
    with pytest.raises(ValidationError, match="http"):
        RunInput.model_validate(run_input_data(incumbent=incumbent))


def test_schemas_are_immutable(run_input):
    with pytest.raises(ValidationError):
        run_input.idea = "something else"


# CompetitorList


def test_competitor_list_accepts_an_incumbent_first():
    competitors = [
        competitor(is_incumbent=True),
        competitor(id="prod_b", name="Rival"),
    ]
    parsed = CompetitorList.model_validate({"competitors": competitors})
    assert [c.name for c in parsed.competitors] == ["Walnut", "Rival"]


@pytest.mark.parametrize(
    ("competitors", "message"),
    [
        ([], "at least 1"),
        ([competitor(), competitor(name="Other")], "ids must be unique"),
        ([competitor(), competitor(id="prod_b", name="WALNUT")], "names must be unique"),
        (
            [competitor(is_incumbent=True), competitor(id="prod_b", name="B", is_incumbent=True)],
            "at most one",
        ),
        (
            [competitor(), competitor(id="prod_b", name="B", is_incumbent=True)],
            "incumbent must be first",
        ),
        ([competitor(id="walnut")], "pattern"),
        ([competitor(url="walnut.example")], "http"),
        ([competitor(reason="")], "at least 1 character"),
    ],
)
def test_competitor_list_rejects(competitors, message):
    with pytest.raises(ValidationError, match=message):
        CompetitorList.model_validate({"competitors": competitors})


def test_rejected_candidates_need_a_reason():
    with pytest.raises(ValidationError):
        CompetitorList.model_validate(
            {"competitors": [competitor()], "rejected": [{"name": "Unrelated"}]}
        )


# ReviewSet


def test_review_set_totals():
    review_set = ReviewSet.model_validate(
        {"products": [product_counts(), product_counts(product_id="prod_b")], "dropped": {"dup": 3}}
    )
    assert review_set.total_reviews == 20


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"by_source": {"google_play": 7}}, "by_source must add up"),
        ({"by_language": {"en": 5}}, "by_language must add up"),
        ({"not_analysed": 0}, "not_analysed must equal"),
        ({"by_sentiment": {"negative": 10}}, "by_sentiment must add up"),
        ({"by_source": {"g2": 10}}, "by_source"),
        ({"by_language": {"English": 10}}, "by_language"),
        ({"total": -1}, "greater than or equal to 0"),
    ],
)
def test_product_counts_must_be_consistent(overrides, message):
    with pytest.raises(ValidationError, match=message):
        ReviewSet.model_validate({"products": [product_counts(**overrides)]})


def test_review_set_lists_each_product_once():
    with pytest.raises(ValidationError, match="each product appears once"):
        ReviewSet.model_validate({"products": [product_counts(), product_counts()]})


# PainPointReport


def test_pain_point_report_accepts_a_valid_report():
    parsed = PainPointReport.model_validate(
        report(junk_clusters=[{"cluster_id": "cl_junk", "review_count": 4, "reason": "Spam."}])
    )
    assert parsed.pain_points[0].quote_review_ids == ["rev_1", "rev_2", "rev_3"]


def test_pain_point_report_may_be_empty():
    assert PainPointReport.model_validate(report(pain_points=[])).pain_points == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"quote_review_ids": ["rev_1", "rev_2"]}, "at least 3"),
        ({"quote_review_ids": [f"rev_{i}" for i in range(6)]}, "at most 5"),
        ({"quote_review_ids": ["rev_1", "rev_1", "rev_2"]}, "must not repeat"),
        ({"quote_review_ids": ["rev_1", "rev_2", "a quote as text"]}, "pattern"),
        ({"review_count": 2}, "cannot quote more reviews"),
        ({"severity": 6}, "less than or equal to 5"),
        ({"negative_share": 1.2}, "less than or equal to 1"),
        ({"product_ids": []}, "at least 1"),
        ({"rank": 2}, "ordered by rank"),
        ({"merged_cluster_ids": ["cl_a"]}, "must not repeat or include cluster_id"),
        ({"quote_glosses": {"rev_9": "It failed."}}, "may only translate reviews"),
        ({"review_count": 19}, "more reviews than were clustered"),
    ],
)
def test_pain_point_rejects(overrides, message):
    with pytest.raises(ValidationError, match=message):
        PainPointReport.model_validate(report(pain_points=[pain_point(**overrides)]))


def test_a_cluster_is_a_pain_point_or_junk_not_both():
    junk = [{"cluster_id": "cl_a", "review_count": 4, "reason": "Spam."}]
    with pytest.raises(ValidationError, match="appears once"):
        PainPointReport.model_validate(report(junk_clusters=junk))
    merged = [pain_point(merged_cluster_ids=["cl_b"])]
    junk = [{"cluster_id": "cl_b", "review_count": 4, "reason": "Spam."}]
    with pytest.raises(ValidationError, match="appears once"):
        PainPointReport.model_validate(report(pain_points=merged, junk_clusters=junk))


def test_trends_and_switching_are_part_of_the_report():
    month = {"month": "2026-09", "reviews": 4, "total_reviews": 20, "enough": True}
    switching = [{"review_id": "rev_1", "intent": "leaving", "other_product": "Tricount"}]
    row = {"from_product": "Walnut", "to_product": "Tricount", "count": 1, "review_ids": ["rev_1"]}
    point = pain_point(trend={"months": [month], "rising": True}, switching_review_ids=["rev_1"])
    parsed = PainPointReport.model_validate(
        report(pain_points=[point], switching_reviews=switching, switching_table=[row])
    )
    assert parsed.pain_points[0].trend.months[0].reviews == 4
    assert parsed.switching_table[0].to_product == "Tricount"

    def rejected(message, **overrides):
        with pytest.raises(ValidationError, match=message):
            PainPointReport.model_validate(report(**overrides))

    rejected("more reviews in a month", pain_points=[
        pain_point(trend={"months": [month | {"reviews": 21}]})
    ])  # fmt: skip
    rejected("pattern", pain_points=[pain_point(trend={"months": [month | {"month": "Sept"}]})])
    rejected("not in switching_reviews", pain_points=[pain_point(switching_review_ids=["rev_1"])])
    rejected("appears once in switching_reviews", switching_reviews=switching * 2)
    rejected("cites reviews not in switching_reviews", switching_table=[row])
    rejected("count must equal", switching_reviews=switching, switching_table=[row | {"count": 2}])
    rejected("Input should be", switching_reviews=[switching[0] | {"intent": "angry"}])
    rejected("valid dictionary or instance of TrendSettings", trend_settings=None)


def test_a_merged_pain_point_names_its_clusters_and_glosses_its_hinglish_quotes():
    point = pain_point(merged_cluster_ids=["cl_b"], quote_glosses={"rev_2": "It is very slow."})
    parsed = PainPointReport.model_validate(report(pain_points=[point])).pain_points[0]
    assert parsed.cluster_ids == ["cl_a", "cl_b"]
    assert parsed.quote_glosses == {"rev_2": "It is very slow."}


# Shared


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_every_schema_is_versioned_and_exports_json_schema(name):
    model = SCHEMAS[name]
    version = 3 if name == "PainPointReport" else 1
    assert schema_version_of(model) == version
    json_schema = model.model_json_schema()
    assert json_schema["properties"]["schema_version"]["const"] == version


def test_new_id_has_its_prefix_and_is_unique():
    first, second = new_id("run_"), new_id("run_")
    assert first.startswith("run_") and first != second
