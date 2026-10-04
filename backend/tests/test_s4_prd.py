import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from productfoundry.cli import main
from productfoundry.core.errors import ProductFoundryError, StageOutputInvalid
from productfoundry.core.prd import Prd, market_gaps
from productfoundry.llm import LlmFailed, ProviderResponse
from productfoundry.orchestrator import PIPELINE, InMemoryRunStore, Orchestrator, RunStatus
from productfoundry.orchestrator.protocols import Services
from productfoundry.stages.fakes import FAKE_STAGES
from productfoundry.stages.s3_pain_points import PainPointStage, run_reviews
from productfoundry.stages.s4_prd import draft_schema, prd_stage
from productfoundry.stages.s4_prd.export import export_prd, render_prd_markdown
from productfoundry.stages.s4_prd.validation import check_prd, grounding_problems
from test_s3_pain_points import COMPETITORS, CONFIG, Labeller, gateway, run_stage, services

FIXTURE = json.loads((Path(__file__).parent / "fixtures/llm/prd.json").read_text("utf-8"))
S1, S3, S4 = "s1_competitors", "s3_pain_points", "s4_prd"


class PrdWriter:
    """The hand-written PRD, citing the pain points and the market gap it was actually sent."""

    def __init__(self, change=None) -> None:
        self.payloads: list[dict] = []
        self._change = change

    def complete(self, request) -> ProviderResponse:
        payload = json.loads(request.messages[1]["content"])
        self.payloads.append(payload)
        answer = json.loads(json.dumps(FIXTURE["answer"]))
        points = [point["id"] for point in payload["pain_points"]]
        gap = payload["market_gaps"][-1]["id"]
        for n, requirement in enumerate(answer["requirements"]):
            requirement["evidence"] = [points[n]] if n < len(points) else [gap, points[0]]
        if self._change is not None:
            self._change(answer)
        return ProviderResponse(json.dumps(answer))


class Both:
    """One provider for a whole run: labels clusters and writes the PRD."""

    def __init__(self) -> None:
        self.labeller, self.writer = Labeller(), PrdWriter()

    def complete(self, request) -> ProviderResponse:
        return (self.writer if request.task == "prd" else self.labeller).complete(request)


@pytest.fixture(scope="module")
def stage_3():
    """The stage 3 report on the invented reviews, and the stores it came from."""
    report, used, _ = run_stage()
    return report, used


def write_prd(report, run_input, provider=None, **providers) -> tuple[Prd, PrdWriter]:
    provider = provider or PrdWriter()
    llm = gateway(gemini=provider, **providers)
    prd = prd_stage(run_input, {S1: COMPETITORS, S3: report}, Services(llm=llm))
    return prd, provider


# The stage


def test_the_prd_cites_the_pain_points_and_market_gaps_of_the_run(stage_3, run_input):
    report, _ = stage_3
    prd, provider = write_prd(report, run_input)
    clusters = [point.cluster_id for point in report.pain_points]
    gap = market_gaps(COMPETITORS)[-1]

    assert [r.id for r in prd.requirements] == ["req_001", "req_002", "req_003", "req_004"]
    assert [r.evidence for r in prd.requirements] == [
        [clusters[0]], [clusters[1]], [clusters[2]], [gap.id, clusters[0]],
    ]  # fmt: skip
    assert [r.priority for r in prd.requirements] == ["must", "must", "should", "could"]
    assert prd.market_gaps == [gap] and gap.product_name == "Tabby"
    assert gap.fact == "Splits bills between friends. Target users: Flatmates"
    assert prd.problem.startswith("People who share expenses") and len(prd.goals) == 2
    assert grounding_problems(prd, report, COMPETITORS) == []
    assert len(provider.payloads) == 1


def test_the_prompt_receives_the_pain_points_the_competitors_and_the_full_run_input(
    stage_3, run_input
):
    report, _ = stage_3
    _, provider = write_prd(report, run_input)
    (payload,) = provider.payloads
    assert payload["run_input"]["region"] == "IN"
    assert payload["run_input"]["incumbent"]["name"] == "Walnut"
    assert payload["run_input"]["idea"] == run_input.idea
    assert [p["id"] for p in payload["pain_points"]] == [p.cluster_id for p in report.pain_points]
    first = payload["pain_points"][0]
    assert (first["label"], first["review_count"], first["severity"]) == (
        "Payments fail after money is debited", 16, 5,
    )  # fmt: skip
    assert first["products"] == ["Splitly", "Tabby"]
    assert [gap["product"] for gap in payload["market_gaps"]] == ["Splitly", "Tabby"]
    assert all(gap["id"].startswith("gap_") for gap in payload["market_gaps"])


def test_an_invented_cluster_id_is_rejected_and_the_next_provider_is_asked(stage_3, run_input):
    report, _ = stage_3

    def invent(answer):
        answer["requirements"][0]["evidence"] = ["cl_invented"]

    inventing = PrdWriter(invent)
    with pytest.raises(LlmFailed, match="cites evidence that does not exist"):
        write_prd(report, run_input, inventing)
    assert len(inventing.payloads) == 2  # asked again once

    honest = PrdWriter()
    prd, _ = write_prd(report, run_input, PrdWriter(invent), openrouter=honest)
    assert len(honest.payloads) == 1 and grounding_problems(prd, report, COMPETITORS) == []


def test_a_requirement_with_empty_evidence_is_rejected(stage_3, run_input):
    report, _ = stage_3

    def empty(answer):
        answer["requirements"][1]["evidence"] = []

    with pytest.raises(LlmFailed, match="requirements.1.evidence"):
        write_prd(report, run_input, PrdWriter(empty))

    schema = draft_schema(["cl_a", "gap_b"])
    answer = json.loads(json.dumps(FIXTURE["answer"]))
    for requirement in answer["requirements"]:
        requirement["evidence"] = ["cl_a"]
    assert len(schema.model_validate(answer).requirements) == 4
    answer["requirements"][2]["evidence"] = ["cl_a", "req_001"]
    with pytest.raises(ValidationError, match="requirement 3 cites evidence that does not exist"):
        schema.model_validate(answer)


def test_the_stage_needs_the_gateway(stage_3, run_input):
    with pytest.raises(ProductFoundryError, match="needs the LLM gateway"):
        prd_stage(run_input, {S1: COMPETITORS, S3: stage_3[0]}, Services())


# The schema and the grounding check


def test_the_grounding_check_rejects_evidence_that_is_not_in_the_run(stage_3, run_input):
    report, _ = stage_3
    prd, _ = write_prd(report, run_input)

    def problems(index, **changes) -> str:
        requirements = list(prd.requirements)
        requirements[index] = requirements[index].model_copy(update=changes)
        changed = prd.model_copy(update={"requirements": requirements})
        return "; ".join(grounding_problems(changed, report, COMPETITORS))

    assert "req_001 cites evidence that is not in this run: cl_invented" in problems(
        0, evidence=["cl_invented"]
    )
    assert "req_002 cites evidence that is not in this run: gap_invented" in problems(
        1, evidence=["gap_invented"]
    )
    # A pain point the owner dropped at the checkpoint can no longer be cited.
    dropped = report.model_copy(update={"pain_points": report.pain_points[:1]})
    assert "req_002 cites evidence" in "; ".join(grounding_problems(prd, dropped, COMPETITORS))

    forged = prd.market_gaps[0].model_copy(update={"fact": "It has no free plan."})
    changed = prd.model_copy(update={"market_gaps": [forged]})
    assert (
        "is not a competitor fact of this run"
        in grounding_problems(changed, report, COMPETITORS)[0]
    )
    with pytest.raises(StageOutputInvalid, match="the PRD is not grounded"):
        check_prd(changed, report, COMPETITORS)


def prd_data(**overrides) -> dict:
    data = {
        "problem": "Payments fail.",
        "users": "Flatmates.",
        "goals": ["Payments work."],
        "requirements": [
            {"id": "req_001", "statement": "It must pay.", "priority": "must", "evidence": ["cl_a"]}
        ],
        "success_metrics": ["Payments succeed."],
    }
    return data | overrides


def requirement(**overrides) -> dict:
    return prd_data()["requirements"][0] | overrides


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"requirements": [requirement(evidence=[])]}, "at least 1"),
        ({"requirements": [requirement(evidence=["payments fail a lot"])]}, "pattern"),
        ({"requirements": [requirement(evidence=["rev_1"])]}, "pattern"),
        ({"requirements": [requirement(evidence=["cl_a", "cl_a"])]}, "must not repeat"),
        ({"requirements": [requirement(priority="urgent")]}, "must', 'should' or 'could'"),
        ({"requirements": [requirement(), requirement()]}, "requirement ids must be unique"),
        ({"requirements": []}, "at least 1"),
        ({"requirements": [requirement(evidence=["gap_x"])]}, "exactly the gaps"),
        ({"goals": []}, "at least 1"),
    ],
)
def test_the_prd_schema_rejects(overrides, message):
    assert Prd.model_validate(prd_data()).requirements[0].evidence == ["cl_a"]
    with pytest.raises(ValidationError, match=message):
        Prd.model_validate(prd_data(**overrides))


# Export


def exported(stage_3, run_input) -> tuple[dict, Prd, dict]:
    report, used = stage_3
    prd, _ = write_prd(report, run_input)
    names, reviews = run_reviews(COMPETITORS, used.reviews)
    return export_prd(prd, report, reviews, names, title=run_input.idea), prd, reviews


def test_a_valid_prd_round_trips_to_markdown_with_working_citations(stage_3, run_input):
    report, _ = stage_3
    export, prd, reviews = exported(stage_3, run_input)
    markdown = render_prd_markdown(export)

    assert markdown.startswith("# PRD: A simpler UPI expense tracker\n")
    for heading in ("Problem", "Users", "Goals", "Non-goals", "Requirements", "Success metrics"):
        assert f"\n## {heading}\n" in markdown
    # Every citation is a link, and every link lands on an anchor in the same document.
    links = re.findall(r"\]\(#([a-z0-9_]+)\)", markdown)
    anchors = re.findall(r'<a id="([a-z0-9_]+)"></a>', markdown)
    assert links == [e for r in prd.requirements for e in r.evidence]
    assert set(links) == set(anchors) and len(anchors) == len(set(anchors)) == 4

    # Each requirement shows its pain point, the counts and one quote with its source.
    first = report.pain_points[0]
    quote = reviews[first.quote_review_ids[0]]
    assert "### `req_001` (must)" in markdown
    assert (
        f"- Pain point [{first.label}](#{first.cluster_id}): 16 reviews, 75% negative, severity 5/5"
    ) in markdown
    assert f"  > {quote.text}" in markdown
    assert f"[Google Play, {quote.reviewed_at:%Y-%m-%d}]({quote.url})" in markdown
    gap = prd.market_gaps[0]
    assert f"- Market gap [Tabby](#{gap.id}): {gap.fact}" in markdown
    assert "Cited by: `req_001`, `req_004`" in markdown


def test_the_json_export_resolves_every_citation(stage_3, run_input):
    export, prd, reviews = exported(stage_3, run_input)
    export = json.loads(json.dumps(export))
    assert [r["id"] for r in export["requirements"]] == [r.id for r in prd.requirements]
    kinds = {item["id"]: item["kind"] for item in export["evidence"]}
    assert sorted(kinds.values()) == ["market_gap", "pain_point", "pain_point", "pain_point"]
    assert all(e in kinds for r in export["requirements"] for e in r["evidence"])
    point = next(item for item in export["evidence"] if item["kind"] == "pain_point")
    assert point["quote"]["text"] == reviews[point["quote"]["review_id"]].text


def test_a_citation_that_does_not_resolve_is_an_error_not_a_gap(stage_3, run_input):
    report, used = stage_3
    prd, _ = write_prd(report, run_input)
    names, reviews = run_reviews(COMPETITORS, used.reviews)
    dropped = report.model_copy(update={"pain_points": report.pain_points[:1]})
    with pytest.raises(StageOutputInvalid, match="req_002 cites"):
        export_prd(prd, dropped, reviews, names, title="x")


# In a run


def test_the_run_goes_from_the_approved_pain_points_to_a_prd(run_input):
    orchestrator = Orchestrator(
        InMemoryRunStore(), FAKE_STAGES | {S4: prd_stage}, services=Services(llm=gateway(
            gemini=PrdWriter()
        )), pipeline=PIPELINE[:4],
    )  # fmt: skip
    run = orchestrator.resume(orchestrator.create_run(run_input).id)
    orchestrator.approve(run.id)
    orchestrator.approve(orchestrator.resume(run.id).id)
    run = orchestrator.resume(run.id)
    assert run.status is RunStatus.COMPLETED
    prd = Prd.model_validate(run.stage(S4).output)
    assert prd.requirements[0].evidence == ["cl_fake0"] and prd.market_gaps[0].product_name


def test_a_run_started_before_the_stage_existed_gets_it_on_resume(run_input):
    store = InMemoryRunStore()
    old = Orchestrator(store, FAKE_STAGES, pipeline=PIPELINE[:3])
    run = old.resume(old.create_run(run_input).id)
    old.approve(run.id)
    old.approve(old.resume(run.id).id)
    assert old.resume(run.id).status is RunStatus.COMPLETED

    new = Orchestrator(store, FAKE_STAGES, pipeline=PIPELINE[:4])
    run = new.resume(run.id, from_stage=S4)
    assert run.status is RunStatus.COMPLETED and [r.key for r in run.stages][-1] == S4
    assert run.stage(S3).approved_at is not None  # earlier stages are untouched


def test_the_cli_exports_the_prd(sessions, db_engine, run_input, tmp_path, monkeypatch, capsys):
    from productfoundry.storage import PostgresClusterStore, PostgresRunStore, ReviewRepository

    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    used = services(
        gateway(gemini=Both()), ReviewRepository(sessions), PostgresClusterStore(sessions)
    )
    stages = FAKE_STAGES | {S1: lambda *_: COMPETITORS, S3: PainPointStage(CONFIG), S4: prd_stage}
    orchestrator = Orchestrator(
        PostgresRunStore(sessions), stages, services=used, pipeline=PIPELINE[:4]
    )
    run = orchestrator.resume(orchestrator.create_run(run_input, seed=5).id)
    orchestrator.approve(run.id)
    run = orchestrator.resume(run.id)

    assert main(["prd", run.id]) == 1
    assert "has no PRD yet" in capsys.readouterr().err

    orchestrator.approve(run.id)
    assert orchestrator.resume(run.id).status is RunStatus.COMPLETED

    assert main(["prd", run.id]) == 0
    markdown = capsys.readouterr().out
    assert markdown.startswith("# PRD: A simpler UPI expense tracker\n")
    assert markdown.count("](https://play.google.com/store/apps/details?id=app.example") == 4

    target = tmp_path / "prd.json"
    assert main(["prd", run.id, "--format", "json", "--out", str(target)]) == 0
    assert len(json.loads(target.read_text("utf-8"))["requirements"]) == 4
