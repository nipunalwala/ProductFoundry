"""The FastAPI app. It creates and approves runs, enqueues the work and reads results."""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, PlainTextResponse

from productfoundry import __version__, runtime
from productfoundry.api.schemas import (
    Approve,
    CreateRun,
    Health,
    Problem,
    Resume,
    RunSummary,
    RunView,
    StageOutputView,
    run_summary,
    run_view,
)
from productfoundry.core.changelog import ChangelogAlerts, ChangelogStore
from productfoundry.core.clusters import ClusterStore
from productfoundry.core.errors import (
    InvalidTransition,
    ProductFoundryError,
    RunNotFound,
    StageOutputInvalid,
)
from productfoundry.core.pricing import AlertStore, PricingAlert
from productfoundry.core.registry import SCHEMAS
from productfoundry.core.reviews import ReviewStore
from productfoundry.jobs import JobQueue
from productfoundry.orchestrator import Orchestrator, RunStatus, RunStore

MEDIA_TYPES = {"md": "text/markdown; charset=utf-8", "json": "application/json"}
ERRORS = {404: {"model": Problem}, 409: {"model": Problem}, 422: {"model": Problem}}


@dataclass
class Backend:
    """What the API works with. Tests pass in-memory parts; `create_app()` builds the real ones."""

    store: RunStore
    queue: JobQueue
    # Creates and approves runs. The API never calls `resume`, so no stage runs here.
    orchestrator: Orchestrator
    reviews: ReviewStore | None = None  # None: exports that quote reviews are unavailable
    clusters: ClusterStore | None = None
    check_edits: bool = True  # check an edited pain-point report against the stored evidence
    pricing_alerts: AlertStore | None = None  # None: there is no database to hold them
    changelog: ChangelogStore | None = None


def _real_backend() -> tuple[Backend, object]:
    from productfoundry import storage
    from productfoundry.jobs import RqQueue
    from productfoundry.settings import Settings

    settings = Settings()
    engine = storage.make_engine()
    storage.check_ready(engine)
    sessions = storage.make_sessions(engine)
    store = storage.PostgresRunStore(sessions)
    reviews, clusters = runtime.evidence_stores(sessions)
    backend = Backend(
        store=store,
        queue=RqQueue(settings.redis_url),
        # Stand-in stages are enough here: the API creates and approves, the worker runs.
        orchestrator=runtime.build_orchestrator(store, sessions, fake_stages=True),
        reviews=reviews,
        clusters=clusters,
        check_edits=not settings.fake_stages,
        pricing_alerts=storage.PricingAlertRepository(sessions),
        changelog=storage.ChangelogRepository(sessions),
    )
    return backend, engine


def _openapi(app: FastAPI) -> dict:
    """The generated schema, plus the stage outputs.

    A stage output travels as plain JSON (`StageOutputView.output`), so its shape
    would be missing from the schema. The frontend generates its types from this
    file, so each output contract is added by name.
    """
    if app.openapi_schema is None:
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes
        )
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        template = "#/components/schemas/{model}"
        for name, model in SCHEMAS.items():
            definition = model.model_json_schema(ref_template=template)
            schemas.update(definition.pop("$defs", {}))
            schemas[name] = definition
        app.openapi_schema = schema
    return app.openapi_schema


def create_app(backend: Backend | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Iterator[None]:
        engine = None
        if backend is None:
            # Built at startup, not at import: the database and Redis must be up by then.
            app.state.backend, engine = _real_backend()
        yield
        if engine is not None:
            engine.dispose()

    app = FastAPI(
        title="ProductFoundry",
        version=__version__,
        description="Start, watch and approve runs. Long stages run in the queue worker.",
        lifespan=lifespan,
    )

    app.state.backend = backend
    app.openapi = lambda: _openapi(app)

    @app.exception_handler(ProductFoundryError)
    async def report_error(request: Request, exc: ProductFoundryError) -> JSONResponse:
        if isinstance(exc, RunNotFound):
            status = 404
        elif isinstance(exc, InvalidTransition):
            status = 409
        elif isinstance(exc, StageOutputInvalid):
            status = 422
        else:
            status = 400
        return JSONResponse({"detail": str(exc)}, status_code=status)

    def get_backend(request: Request) -> Backend:
        return request.app.state.backend

    Uses = Annotated[Backend, Depends(get_backend)]

    @app.get("/health", response_model=Health)
    def health() -> Health:
        return Health()

    @app.post("/runs", response_model=RunView, status_code=201, responses=ERRORS)
    def create_run(body: CreateRun, backend: Uses) -> RunView:
        """Create a run and queue it. It stops at the first checkpoint."""
        run = backend.orchestrator.create_run(body.input, seed=body.seed)
        backend.queue.enqueue_resume(run.id)
        return run_view(run)

    @app.get("/runs", response_model=list[RunSummary])
    def list_runs(backend: Uses) -> list[RunSummary]:
        """Every run, newest first."""
        return [run_summary(run) for run in reversed(backend.store.list_runs())]

    @app.get("/runs/{run_id}", response_model=RunView, responses=ERRORS)
    def get_run(run_id: str, backend: Uses) -> RunView:
        """A run with the status of each stage. `paused_quota` is a pause, not a failure."""
        return run_view(backend.store.get(run_id))

    @app.get("/runs/{run_id}/stages/{stage_key}", response_model=StageOutputView, responses=ERRORS)
    def get_stage_output(run_id: str, stage_key: str, backend: Uses) -> StageOutputView:
        run = backend.store.get(run_id)
        record = next((r for r in run.stages if r.key == stage_key), None)
        if record is None:
            raise RunNotFound(f"run {run_id} has no stage {stage_key!r}")
        return StageOutputView(
            key=record.key,
            status=record.status,
            schema_version=record.schema_version,
            edited=record.edited_output is not None,
            output=record.effective_output,
            original_output=record.output,
        )

    @app.post("/runs/{run_id}/approve", response_model=RunView, responses=ERRORS)
    def approve(run_id: str, backend: Uses, body: Approve | None = None) -> RunView:
        """Approve the open checkpoint, unchanged or edited, and queue the rest of the run."""
        body = body or Approve()
        run = backend.store.get(run_id)
        if run.status is not RunStatus.AWAITING_APPROVAL:
            raise InvalidTransition(f"run {run.id} is {run.status}, not awaiting approval")
        competitors, pain_points = body.competitors, body.pain_points
        edited = runtime.checkpoint_edit(
            run,
            edited=body.edited_output,
            remove=competitors.remove if competitors else (),
            add=competitors.add if competitors else (),
            rename=pain_points.rename if pain_points else None,
            merge=pain_points.merge if pain_points else (),
            drop=pain_points.drop if pain_points else (),
            rank=pain_points.rank if pain_points else (),
        )
        if edited is not None and runtime.waiting_at(run, "s3_pain_points") and backend.check_edits:
            if backend.reviews is None or backend.clusters is None:
                raise ProductFoundryError(
                    "an edited pain-point report cannot be checked without the stored reviews"
                )
            runtime.check_pain_point_edit(run, edited, backend.reviews, backend.clusters)
        approved = backend.orchestrator.approve(run_id, edited)
        backend.queue.enqueue_resume(run_id)
        return run_view(approved)

    @app.post("/runs/{run_id}/resume", response_model=RunView, status_code=202, responses=ERRORS)
    def resume(run_id: str, backend: Uses, body: Resume | None = None) -> RunView:
        """Queue a paused or failed run again, or run it again from a named stage."""
        from_stage = body.from_stage if body else None
        run = backend.store.get(run_id)
        if from_stage is None:
            if run.status is RunStatus.AWAITING_APPROVAL:
                raise InvalidTransition(f"run {run.id} is waiting for approval; approve it first")
            if run.status is RunStatus.COMPLETED:
                raise InvalidTransition(f"run {run.id} is completed; name a stage to run it again")
        elif from_stage not in backend.orchestrator.stage_keys:
            raise InvalidTransition(f"unknown stage {from_stage!r}")
        backend.queue.enqueue_resume(run_id, from_stage)
        return run_view(run)

    @app.get(
        "/runs/{run_id}/exports/{name}",
        responses=ERRORS | {200: {"content": {"text/markdown": {}, "application/json": {}}}},
    )
    def download_export(
        run_id: str,
        name: Literal["report", "prd", "tasks"],
        backend: Uses,
        format: Literal["md", "json"] = "md",
    ) -> Response:
        """The pain-point report, the PRD or the task plan, as Markdown or JSON."""
        run = backend.store.get(run_id)
        text = runtime.export(run, name, format, backend.reviews)
        headers = {"Content-Disposition": f'attachment; filename="{run_id}-{name}.{format}"'}
        return PlainTextResponse(text, media_type=MEDIA_TYPES[format], headers=headers)

    @app.get("/runs/{run_id}/changelog", response_model=ChangelogAlerts, responses=ERRORS)
    def get_changelog_alerts(run_id: str, backend: Uses) -> ChangelogAlerts:
        """What competitors shipped that touches this run: fixes for its pain points, and
        new features its PRD does not cover. Matching is done by `changelog match`."""
        run = backend.store.get(run_id)
        if backend.changelog is None:
            raise ProductFoundryError("release items are kept in the database")
        return runtime.changelog_alerts(run, backend.changelog)

    @app.get("/pricing/alerts", response_model=list[PricingAlert])
    def list_pricing_alerts(backend: Uses, product_id: str | None = None) -> list[PricingAlert]:
        """Changes found between two snapshots of a tracked pricing page, newest first."""
        if backend.pricing_alerts is None:
            return []
        return backend.pricing_alerts.list(product_id)

    return app
