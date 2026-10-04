"""How a run is wired outside tests: real stages, adapters and stores.

The CLI, the API and the queue worker all drive the same orchestrator, so they
build it, edit checkpoints and export outputs through this module.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from productfoundry.core.clusters import ClusterStore
from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.reviews import ReviewStore
from productfoundry.orchestrator import Orchestrator, RunRecord, RunStore, Services
from productfoundry.orchestrator.checkpoints import edit_competitors, edit_pain_points
from productfoundry.stages.fakes import FAKE_STAGES

EXPORTS = ("report", "prd", "tasks")
DEFAULT_REVIEW_CAP = 2000


def evidence_stores(sessions) -> tuple[ReviewStore, ClusterStore]:
    """The review and cluster stores: the database's, or in-memory ones when there is none."""
    if sessions is not None:
        from productfoundry import storage

        return storage.ReviewRepository(sessions), storage.PostgresClusterStore(sessions)
    from productfoundry.storage.memory import InMemoryClusterStore, InMemoryReviewStore

    # Without a database, reviews and clusters last only as long as the process.
    return InMemoryReviewStore(), InMemoryClusterStore()


def services(sessions) -> Services:
    from productfoundry import llm, storage
    from productfoundry.llm.fakes import InMemoryCallStore, InMemoryUsageStore
    from productfoundry.ml.config import load_config
    from productfoundry.ml.embeddings import SentenceTransformerEmbedder
    from productfoundry.settings import Settings
    from productfoundry.sources.app_store import AppStoreLookup
    from productfoundry.sources.app_store.reviews import AppStoreReviews
    from productfoundry.sources.google_play import GooglePlayLookup
    from productfoundry.sources.google_play.reviews import GooglePlayReviews
    from productfoundry.sources.robots import Robots
    from productfoundry.sources.search.tavily import TavilySearch

    settings = Settings()
    routing = llm.load_routing()
    reviews, clusters = evidence_stores(sessions)
    if sessions is not None:
        calls = storage.PostgresCallStore(sessions)
        usage = storage.PostgresUsageStore(sessions)
    else:
        calls, usage = InMemoryCallStore(), InMemoryUsageStore()
    gateway = llm.Gateway(routing, llm.live_providers(routing, settings), calls, usage)

    search = None
    lookups = {"app_store": AppStoreLookup()}
    if settings.tavily_api_key and settings.tavily_api_key.get_secret_value():
        search = TavilySearch(settings.tavily_api_key.get_secret_value())
        lookups["google_play"] = GooglePlayLookup(search, Robots())
    return Services(
        llm=gateway,
        search=search,
        app_lookups=lookups,
        review_sources={"google_play": GooglePlayReviews(), "app_store": AppStoreReviews()},
        reviews=reviews,
        clusters=clusters,
        embedder=SentenceTransformerEmbedder(load_config().embeddings),
    )


def build_orchestrator(
    store: RunStore,
    sessions,
    *,
    fake_stages: bool = False,
    review_cap: int = DEFAULT_REVIEW_CAP,
) -> Orchestrator:
    """The real stages with their adapters, or with `fake_stages` stand-ins that make
    no search, store or LLM request."""
    if fake_stages:
        return Orchestrator(store, FAKE_STAGES)
    from productfoundry.stages.s1_competitors import competitors_stage
    from productfoundry.stages.s2_reviews import ReviewSettings, ReviewStage
    from productfoundry.stages.s3_pain_points import pain_points_stage
    from productfoundry.stages.s4_prd import prd_stage
    from productfoundry.stages.s5_tasks import tasks_stage
    from productfoundry.stages.s6_roadmap import roadmap_stage
    from productfoundry.stages.s7_acceptance import acceptance_stage

    stages = {
        "s1_competitors": competitors_stage,
        "s2_reviews": ReviewStage(ReviewSettings(cap_per_store=review_cap)),
        "s3_pain_points": pain_points_stage,
        "s4_prd": prd_stage,
        "s5_tasks": tasks_stage,
        "s6_roadmap": roadmap_stage,
        "s7_acceptance": acceptance_stage,
    }
    return Orchestrator(store, stages, services=services(sessions))


# Checkpoints


def checkpoint_edit(
    run: RunRecord,
    *,
    edited: object = None,
    remove: Sequence[str] = (),
    add: object = (),
    rename: Mapping[str, str] | None = None,
    merge: Sequence[Sequence[str]] = (),
    drop: Sequence[str] = (),
    rank: Sequence[str] = (),
) -> object:
    """The output to approve: `edited` as given, or the stage's output with the
    checkpoint's own edits applied. None when nothing is changed."""
    competitor_edit = bool(remove or add)
    pain_point_edit = bool(rename or merge or drop or rank)
    if (competitor_edit or pain_point_edit) and edited is not None:
        raise ProductFoundryError("give a whole edited output, or the checkpoint's edits, not both")
    if competitor_edit:
        if not isinstance(add, list | tuple):
            raise ProductFoundryError("the competitors to add must be a JSON list")
        stage = run.stage("s1_competitors")
        if stage.status != "awaiting_approval":
            raise ProductFoundryError("remove and add apply to the competitor checkpoint")
        edited = edit_competitors(stage.output, remove=remove, add=add)
    if pain_point_edit:
        stage = run.stage("s3_pain_points")
        if stage.status != "awaiting_approval":
            raise ProductFoundryError(
                "rename, merge, drop and rank apply to the pain-point checkpoint"
            )
        edited = edit_pain_points(stage.output, rename=rename, merge=merge, drop=drop, rank=rank)
    return edited


def waiting_at(run: RunRecord, stage_key: str) -> bool:
    return any(r.key == stage_key and r.status == "awaiting_approval" for r in run.stages)


def check_pain_point_edit(
    run: RunRecord, edited: object, reviews: ReviewStore, clusters: ClusterStore
) -> None:
    """An edited pain-point report must still match the stored clusters and reviews."""
    from productfoundry.core.competitors import CompetitorList
    from productfoundry.core.pain_points import PainPointReport
    from productfoundry.stages.s3_pain_points import run_reviews
    from productfoundry.stages.s3_pain_points.validation import check_report

    try:
        report = PainPointReport.model_validate(edited)
    except ValidationError:
        return  # approval reports schema errors
    competitors = CompetitorList.model_validate(run.stage("s1_competitors").effective_output)
    names, stored = run_reviews(competitors, reviews)
    check_report(report, clusters.for_run(run.id), stored, names, ranked_by_score=False)


# Exports


def stage_output(run: RunRecord, key: str, name: str) -> dict[str, Any]:
    stages = {record.key: record for record in run.stages}
    output = stages[key].effective_output if key in stages else None
    if output is None:
        raise ProductFoundryError(f"run {run.id} has no {name} yet")
    return output


def export(run: RunRecord, name: str, format: str, reviews: ReviewStore | None) -> str:
    """One of `EXPORTS` as Markdown (`md`) or JSON text.

    The report and the PRD quote stored reviews, so they need a review store
    that still holds them; the task plan does not.
    """
    if name not in EXPORTS:
        raise ProductFoundryError(f"unknown export {name!r}; exports are {', '.join(EXPORTS)}")
    data, render = _BUILDERS[name](run, reviews)
    if format == "json":
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return render(data)


def _evidence(run: RunRecord, reviews: ReviewStore | None):
    """The run's approved pain-point report, its product names and its stored reviews."""
    from productfoundry.core.competitors import CompetitorList
    from productfoundry.core.pain_points import PainPointReport
    from productfoundry.stages.s3_pain_points import run_reviews

    output = stage_output(run, "s3_pain_points", "pain-point report")
    if reviews is None:
        raise ProductFoundryError("the export quotes stored reviews, which --memory does not keep")
    competitors = CompetitorList.model_validate(run.stage("s1_competitors").effective_output)
    names, stored = run_reviews(competitors, reviews)
    try:
        report = PainPointReport.model_validate(output)
    except ValidationError:
        # A report saved under an earlier schema version, before trends and switching.
        raise ProductFoundryError(
            f"run {run.id} has a pain-point report in an older format; "
            f"re-run it with `resume {run.id} --from-stage s3_pain_points`"
        ) from None
    return report, names, stored


def _report(run: RunRecord, reviews: ReviewStore | None):
    from productfoundry.stages.s3_pain_points.export import export_report, render_markdown

    report, names, stored = _evidence(run, reviews)
    title = run.input.incumbent.name if run.input.incumbent else run.input.idea
    return export_report(report, stored, names, title=title), render_markdown


def _prd(run: RunRecord, reviews: ReviewStore | None):
    from productfoundry.core.prd import Prd
    from productfoundry.stages.s4_prd.export import export_prd, render_prd_markdown

    prd = Prd.model_validate(stage_output(run, "s4_prd", "PRD"))
    report, names, stored = _evidence(run, reviews)
    return export_prd(prd, report, stored, names, title=run.input.idea), render_prd_markdown


def _tasks(run: RunRecord, reviews: ReviewStore | None):
    from productfoundry.core.acceptance import AcceptanceCriteria
    from productfoundry.core.prd import Prd
    from productfoundry.core.tasks import TaskPlan
    from productfoundry.stages.s5_tasks.export import export_tasks, render_tasks_markdown

    plan = TaskPlan.model_validate(stage_output(run, "s5_tasks", "task plan"))
    prd = Prd.model_validate(stage_output(run, "s4_prd", "PRD"))
    stages = {record.key: record for record in run.stages}
    criteria = None
    if "s7_acceptance" in stages and stages["s7_acceptance"].effective_output is not None:
        criteria = AcceptanceCriteria.model_validate(stages["s7_acceptance"].effective_output)
    data = export_tasks(plan, prd, title=run.input.idea, criteria=criteria)
    return data, render_tasks_markdown


_BUILDERS: dict[str, Callable] = {"report": _report, "prd": _prd, "tasks": _tasks}
