import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from productfoundry import __version__, runtime
from productfoundry.core.base import schema_version_of
from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.registry import SCHEMAS
from productfoundry.core.run_input import RunInput
from productfoundry.orchestrator import (
    InMemoryRunStore,
    Orchestrator,
    RunRecord,
    RunStore,
)

DEFAULT_STATE_FILE = Path(".productfoundry") / "runs.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="productfoundry",
        description="Turn a software product idea into an evidence-backed build plan.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--memory",
        action="store_true",
        help="keep runs in a local JSON file instead of the database",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="where --memory keeps runs between commands (default: %(default)s)",
    )
    parser.add_argument(
        "--fake-stages",
        action="store_true",
        help="run every stage as a stand-in: no search, store or LLM request is made",
    )
    parser.add_argument(
        "--review-cap",
        type=int,
        default=2000,
        metavar="N",
        help="reviews fetched per product per store in one run (default: %(default)s)",
    )
    commands = parser.add_subparsers(dest="command")

    run = commands.add_parser("run", help="start a run from a RunInput JSON file")
    run.add_argument("--input", type=Path, required=True, help="path to a RunInput JSON file")
    run.add_argument("--seed", type=int, default=0, help="seed recorded on the run (default: 0)")
    run.set_defaults(handler=_cmd_run)

    status = commands.add_parser("status", help="show one run, or list all runs")
    status.add_argument("run_id", nargs="?")
    status.add_argument("--output", metavar="STAGE", help="print this stage's output as JSON")
    status.set_defaults(handler=_cmd_status)

    approve = commands.add_parser("approve", help="approve the checkpoint and continue the run")
    approve.add_argument("run_id")
    approve.add_argument("--edit", type=Path, help="JSON file that replaces the stage output")
    approve.add_argument(
        "--remove",
        action="append",
        default=[],
        metavar="NAME",
        help="competitor checkpoint: drop this competitor (repeatable)",
    )
    approve.add_argument(
        "--add", type=Path, help="competitor checkpoint: JSON file with a list of competitors"
    )
    approve.add_argument(
        "--rename",
        action="append",
        default=[],
        metavar="N=LABEL",
        help="pain-point checkpoint: give pain point N a new label (repeatable)",
    )
    approve.add_argument(
        "--merge",
        action="append",
        default=[],
        metavar="N,M",
        help="pain-point checkpoint: fold pain point M (and more) into N (repeatable)",
    )
    approve.add_argument(
        "--drop",
        action="append",
        default=[],
        metavar="N",
        help="pain-point checkpoint: drop pain point N (repeatable)",
    )
    approve.add_argument(
        "--rank",
        metavar="N,M",
        help="pain-point checkpoint: put these first, in this order; the rest follow by score",
    )
    approve.set_defaults(handler=_cmd_approve)

    resume = commands.add_parser("resume", help="continue a paused or failed run")
    resume.add_argument("run_id")
    resume.add_argument(
        "--from-stage", metavar="STAGE", help="run this stage again; later stages are invalidated"
    )
    resume.set_defaults(handler=_cmd_resume)

    report = commands.add_parser("report", help="export the pain-point report of a run")
    report.add_argument("run_id")
    report.add_argument("--format", choices=["md", "json"], default="md")
    report.add_argument("--out", type=Path, help="write to this file instead of printing")
    report.set_defaults(handler=_cmd_export)

    prd = commands.add_parser("prd", help="export the PRD of a run, with its citations")
    prd.add_argument("run_id")
    prd.add_argument("--format", choices=["md", "json"], default="md")
    prd.add_argument("--out", type=Path, help="write to this file instead of printing")
    prd.set_defaults(handler=_cmd_export)

    tasks = commands.add_parser(
        "tasks", help="export the task plan of a run, with its acceptance criteria"
    )
    tasks.add_argument("run_id")
    tasks.add_argument("--format", choices=["md", "json"], default="md")
    tasks.add_argument("--out", type=Path, help="write to this file instead of printing")
    tasks.set_defaults(handler=_cmd_export)

    serve = commands.add_parser("serve", help="run the HTTP API (the worker runs in Docker)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=_cmd_serve)

    openapi = commands.add_parser("openapi", help="print or write the API's OpenAPI schema")
    openapi.add_argument("--out", type=Path, help="write to this file instead of printing")
    openapi.set_defaults(handler=_cmd_openapi)

    db = commands.add_parser("db", help="manage the database")
    db.add_argument("action", choices=["upgrade"], help="upgrade: apply the migrations")
    db.set_defaults(handler=_cmd_db)

    llm = commands.add_parser("llm", help="LLM gateway tools")
    llm.add_argument("action", choices=["check"], help="check: one small live call per provider")
    llm.set_defaults(handler=_cmd_llm)

    pricing = commands.add_parser("pricing", help="read and show public pricing pages")
    actions = pricing.add_subparsers(dest="action", required=True)
    snapshot = actions.add_parser("snapshot", help="read one pricing page and store its plans")
    snapshot.add_argument("--url", required=True, help="the public pricing page")
    show = actions.add_parser("show", help="print the stored snapshots of a product")
    show.add_argument("--history", action="store_true", help="every snapshot, not only the latest")
    actions.add_parser("refresh", help="read every tracked pricing page again (the weekly job)")
    alerts = actions.add_parser("alerts", help="print the price changes found so far")
    alerts.add_argument("--product", metavar="NAME", help="only this product's changes")
    alerts.add_argument("--run", metavar="RUN_ID", help=argparse.SUPPRESS)
    for action in (snapshot, show):
        action.add_argument("--product", required=True, metavar="NAME")
        action.add_argument(
            "--run",
            metavar="RUN_ID",
            help="take the product's id from this run's competitor list, so that the "
            "snapshot joins the run's reviews",
        )
    pricing.set_defaults(handler=_cmd_pricing)

    changelog = commands.add_parser("changelog", help="track what competitors ship")
    actions = changelog.add_subparsers(dest="action", required=True)
    track = actions.add_parser("track", help="add a release source for a product")
    track.add_argument("--product", required=True, metavar="NAME")
    track.add_argument("--run", metavar="RUN_ID", help="take the product's id from this run")
    track.add_argument("--github", metavar="OWNER/REPO", help="releases of a public repository")
    track.add_argument("--feed", metavar="URL", help="an RSS or Atom release-note feed")
    track.add_argument("--page", metavar="URL", help="a public changelog page")
    track.add_argument("--app-store", metavar="ID", help="the App Store track id")
    track.add_argument("--google-play", metavar="ID", help="the Google Play package name")
    actions.add_parser("fetch", help="read every tracked source and store new release items")
    match = actions.add_parser("match", help="match a run's unseen release items (LLM calls)")
    match.add_argument("run_id")
    match.add_argument("--again", action="store_true", help="forget earlier matches first")
    alerts = actions.add_parser("alerts", help="print a run's changelog alerts")
    alerts.add_argument("run_id")
    alerts.add_argument("--format", choices=["text", "json"], default="text")
    changelog.set_defaults(handler=_cmd_changelog)

    traction = commands.add_parser("traction", help="is a product growing, flat or declining?")
    actions = traction.add_subparsers(dest="action", required=True)
    collect = actions.add_parser("collect", help="read a product's public signals and store them")
    collect.add_argument("--term", help="search term for Google Trends (default: the name)")
    collect.add_argument("--region", help="country code for the search interest (default: all)")
    collect.add_argument("--no-trends", action="store_true", help="skip Google Trends")
    collect.add_argument("--google-play", metavar="ID", help="the Google Play package name")
    collect.add_argument("--app-store", metavar="ID", help="the App Store track id")
    score = actions.add_parser("score", help="print the label and its reasons (no request)")
    score.add_argument("--format", choices=["text", "json"], default="text")
    for action in (collect, score):
        action.add_argument("--product", required=True, metavar="NAME")
        action.add_argument(
            "--run", metavar="RUN_ID", help="take the product's id and store ids from this run"
        )
    traction.set_defaults(handler=_cmd_traction)

    schema = commands.add_parser("schema", help="export the JSON Schema of the contracts")
    schema.add_argument("name", nargs="?", choices=sorted(SCHEMAS), help="print one schema")
    schema.add_argument("--out", type=Path, help="write every schema to this directory")
    schema.set_defaults(handler=_cmd_schema)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        # Product names and reviews are not limited to the console's code page.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except ProductFoundryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


@contextmanager
def _open_store(args: argparse.Namespace) -> Iterator[RunStore]:
    """The Postgres store, or with --memory the in-memory one kept in the state file."""
    args.sessions = None
    if not args.memory:
        # Imported here so --memory and `schema` work without a database driver in use.
        from productfoundry import storage

        engine = storage.make_engine()
        try:
            storage.check_ready(engine)
            args.sessions = storage.make_sessions(engine)
            yield storage.PostgresRunStore(args.sessions)
        finally:
            engine.dispose()
        return

    path: Path = args.state_file
    if path.exists():
        store = InMemoryRunStore.load_json(path.read_text(encoding="utf-8"))
    else:
        store = InMemoryRunStore()
    try:
        yield store
    finally:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(store.dump_json(), encoding="utf-8")


def _orchestrator(args: argparse.Namespace, store: RunStore) -> Orchestrator:
    """Real stages, or stand-ins with --fake-stages. Call inside `_open_store`."""
    return runtime.build_orchestrator(
        store, args.sessions, fake_stages=args.fake_stages, review_cap=args.review_cap
    )


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProductFoundryError(f"cannot read {path}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise ProductFoundryError(f"{path} is not valid JSON: {exc}") from exc


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        run_input = RunInput.model_validate(_read_json(args.input))
    except ValidationError as exc:
        raise ProductFoundryError(f"invalid run input in {args.input}:\n{exc}") from exc
    with _open_store(args) as store:
        orchestrator = _orchestrator(args, store)
        run = orchestrator.create_run(run_input, seed=args.seed)
        run = orchestrator.resume(run.id)
    _print_run(run)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    with _open_store(args) as store:
        runs = store.list_runs() if args.run_id is None else [store.get(args.run_id)]
    if args.run_id is None:
        if not runs:
            print("no runs")
        for run in runs:
            print(f"{run.id}  {run.status:<17}  {run.created_at:%Y-%m-%d %H:%M}  {run.input.idea}")
        return 0
    run = runs[0]
    if args.output is None:
        _print_run(run)
        return 0
    try:
        output = run.stage(args.output).effective_output
    except KeyError:
        raise ProductFoundryError(f"unknown stage {args.output!r}") from None
    if output is None:
        raise ProductFoundryError(f"stage {args.output} has no output yet")
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    edited = _read_json(args.edit) if args.edit else None
    added = _read_json(args.add) if args.add else []
    with _open_store(args) as store:
        orchestrator = _orchestrator(args, store)
        run = store.get(args.run_id)
        edited = runtime.checkpoint_edit(
            run,
            edited=edited,
            remove=args.remove,
            add=added,
            rename=dict(_label(item) for item in args.rename),
            merge=[item.split(",") for item in args.merge],
            drop=args.drop,
            rank=args.rank.split(",") if args.rank else (),
        )
        if edited is not None and runtime.waiting_at(run, "s3_pain_points"):
            _check_pain_point_edit(args, run, edited)
        orchestrator.approve(args.run_id, edited)
        run = orchestrator.resume(args.run_id)
    _print_run(run)
    return 0


def _label(item: str) -> tuple[str, str]:
    name, separator, label = item.partition("=")
    if not separator or not label.strip():
        raise ProductFoundryError(f"--rename takes N=LABEL, not {item!r}")
    return name, label.strip()


def _check_pain_point_edit(args: argparse.Namespace, run: RunRecord, edited: object) -> None:
    if args.fake_stages:
        return  # stand-in output cites reviews that do not exist
    if args.sessions is None:
        raise ProductFoundryError(
            "an edited pain-point report is checked against the stored reviews, "
            "which --memory does not keep; approve it unchanged or use the database"
        )
    runtime.check_pain_point_edit(run, edited, *runtime.evidence_stores(args.sessions))


def _cmd_export(args: argparse.Namespace) -> int:
    with _open_store(args) as store:
        run = store.get(args.run_id)
        reviews = runtime.evidence_stores(args.sessions)[0] if args.sessions is not None else None
        text = runtime.export(run, args.command, args.format, reviews)
    if args.out is None:
        print(text, end="")
    else:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise ProductFoundryError(f"cannot write {args.out}: {exc.strerror}") from exc
        print(args.out)
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    with _open_store(args) as store:
        run = _orchestrator(args, store).resume(args.run_id, from_stage=args.from_stage)
    _print_run(run)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from productfoundry.api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def openapi_text() -> str:
    """The OpenAPI schema as committed in backend/openapi.json."""
    from productfoundry.api import create_app

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def _cmd_openapi(args: argparse.Namespace) -> int:
    text = openapi_text()
    if args.out is None:
        print(text, end="")
    else:
        args.out.write_text(text, encoding="utf-8")
        print(args.out)
    return 0


def _cmd_db(args: argparse.Namespace) -> int:
    from productfoundry import storage

    engine = storage.make_engine()
    try:
        storage.upgrade(engine)
    finally:
        engine.dispose()
    print("database is up to date")
    return 0


def _cmd_llm(args: argparse.Namespace) -> int:
    from pydantic import BaseModel

    from productfoundry import llm, storage
    from productfoundry.llm.fakes import InMemoryCallStore, InMemoryUsageStore
    from productfoundry.settings import Settings

    class Ping(BaseModel):
        ok: bool

    routing = llm.load_routing()
    providers = llm.live_providers(routing, Settings())
    engine = None
    if args.memory:
        calls, usage = InMemoryCallStore(), InMemoryUsageStore()
    else:
        engine = storage.make_engine()
        storage.check_ready(engine)
        sessions = storage.make_sessions(engine)
        calls, usage = storage.PostgresCallStore(sessions), storage.PostgresUsageStore(sessions)
    gateway = llm.Gateway(routing, providers, calls, usage)
    messages = [{"role": "user", "content": 'Reply with the JSON object {"ok": true}.'}]

    failed = False
    try:
        for name, spec in routing.providers.items():
            if name not in providers:
                print(f"{name:<11} {spec.model:<52} no API key in .env")
                failed = True
                continue
            call = gateway.check(name, Ping, messages)
            detail = "" if call.outcome == "ok" else f"  {call.error}"
            print(f"{name:<11} {spec.model:<52} {call.outcome:<14} {call.latency_ms} ms{detail}")
            failed = failed or call.outcome != "ok"
    finally:
        if engine is not None:
            engine.dispose()
    return 1 if failed else 0


def _pricing_product(args: argparse.Namespace, store: RunStore) -> tuple[str, str]:
    """The product's id and name: the run's competitor of that name, or an id from the name."""
    from productfoundry.core.ids import product_id
    from productfoundry.core.names import normalise_name, same_product

    if args.run is None:
        return product_id(normalise_name(args.product)), args.product
    output = runtime.stage_output(store.get(args.run), "s1_competitors", "competitor list")
    for competitor in output["competitors"]:
        if same_product(competitor["name"], args.product):
            return competitor["id"], competitor["name"]
    raise ProductFoundryError(f"run {args.run} has no competitor named {args.product!r}")


def _cmd_pricing(args: argparse.Namespace) -> int:
    from productfoundry.market.pricing import render_alert, render_snapshot

    if args.memory:
        raise ProductFoundryError("pricing snapshots are kept in the database, not with --memory")
    with _open_store(args) as store:
        from productfoundry import storage

        if args.action == "refresh":
            from productfoundry.jobs.worker import pricing_counts

            outcomes = runtime.pricing_refresh(args.sessions)
            for outcome in outcomes:
                if outcome.skipped:
                    print(f"skipped: {outcome.skipped}")
                elif outcome.unchanged:
                    print(f"unchanged: {outcome.url}")
                elif outcome.alert:
                    print(render_alert(outcome.alert))
                else:
                    print(f"read, no change in plans: {outcome.url}")
            counts = pricing_counts(outcomes)
            print(", ".join(f"{count} {name}" for name, count in counts.items()))
            return 0
        if args.action == "alerts":
            product = _pricing_product(args, store)[0] if args.product else None
            found = storage.PricingAlertRepository(args.sessions).list(product)
            if not found:
                print("no price change has been found")
            for alert in found:
                print(render_alert(alert))
            return 0
        product, name = _pricing_product(args, store)
        if args.action == "snapshot":
            outcome = runtime.pricing_snapshot(args.sessions, product, name, args.url)
            if outcome.snapshot is None:
                print(f"skipped: {outcome.skipped}")
                return 1
            if outcome.unchanged:
                print("the page has not changed since the latest snapshot")
            print(render_snapshot(outcome.snapshot, name))
            if outcome.alert:
                print(render_alert(outcome.alert))
            return 0
        snapshots = storage.PricingRepository(args.sessions).history(product)
        if not snapshots:
            print(f"no pricing snapshot is stored for {name}")
            return 1
        for snapshot in snapshots if args.history else snapshots[-1:]:
            print(render_snapshot(snapshot, name))
    return 0


def _cmd_changelog(args: argparse.Namespace) -> int:
    from productfoundry.core.changelog import ChangelogSource
    from productfoundry.market.changelog import render_alerts

    if args.memory:
        raise ProductFoundryError("release items are kept in the database, not with --memory")
    with _open_store(args) as store:
        from productfoundry import storage

        changelog = storage.ChangelogRepository(args.sessions)
        if args.action == "track":
            product, name = _pricing_product(args, store)
            given = {
                "github": args.github,
                "feed": args.feed,
                "page": args.page,
                "app_store": args.app_store,
                "google_play": args.google_play,
            }
            targets = {kind: target for kind, target in given.items() if target}
            if not targets:
                raise ProductFoundryError(
                    "name a source: --github, --feed, --page, --app-store or --google-play"
                )
            for kind, target in targets.items():
                try:
                    source = ChangelogSource(product_id=product, kind=kind, target=target)
                except ValidationError as exc:
                    raise ProductFoundryError(f"invalid source: {exc}") from exc
                changelog.track(source, name)
                print(f"tracking {kind} {target} for {name}")
            return 0
        if args.action == "fetch":
            outcomes = runtime.changelog_collect(args.sessions)
            if not outcomes:
                print("no release source is tracked: add one with `changelog track`")
            for outcome in outcomes:
                where = f"{outcome.source.kind} {outcome.source.target}"
                if outcome.skipped:
                    print(f"skipped {where}: {outcome.skipped}")
                else:
                    print(f"{where}: {outcome.found} release items, {outcome.new} new")
            return 0
        run = store.get(args.run_id)
        if args.action == "match":
            matches = runtime.changelog_match(args.sessions, run, again=args.again)
            touching = sum(bool(m.cluster_ids or m.requirement_ids) for m in matches)
            print(f"{len(matches)} new release items matched, {touching} touch this run")
        alerts = runtime.changelog_alerts(run, changelog)
        if getattr(args, "format", "text") == "json":
            print(alerts.model_dump_json(indent=2))
        else:
            print(render_alerts(alerts))
    return 0


def _cmd_traction(args: argparse.Namespace) -> int:
    from productfoundry.core.traction import TractionTarget
    from productfoundry.market.traction import render_score

    if args.memory:
        raise ProductFoundryError("traction signals are kept in the database, not with --memory")
    with _open_store(args) as store:
        product, name = _pricing_product(args, store)
        if args.action == "collect":
            store_ids = {"google_play": args.google_play, "app_store": args.app_store}
            if args.run is not None:
                output = runtime.stage_output(store.get(args.run), "s1_competitors", "competitors")
                known = next(c for c in output["competitors"] if c["id"] == product)["store_ids"]
                store_ids = {key: store_ids[key] or known.get(key) for key in store_ids}
            target = TractionTarget(
                product_id=product,
                name=name,
                term=None if args.no_trends else (args.term or name),
                region=args.region,
                store_ids={key: value for key, value in store_ids.items() if value},
            )
            collected = runtime.traction_collect(args.sessions, target)
            for observation in collected.observations:
                print(f"{observation.signal}: {observation.value:,.0f}  ({observation.source})")
            for reason in collected.skipped:
                print(f"skipped: {reason}")
            print(f"{collected.requests} request(s) made")
        result = runtime.traction_score(args.sessions, product)
        if getattr(args, "format", "text") == "json":
            print(result.model_dump_json(indent=2))
        else:
            print(render_score(result, name))
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, model in SCHEMAS.items():
            path = args.out / f"{name}.v{schema_version_of(model)}.schema.json"
            path.write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
            print(path)
        return 0
    if args.name is None:
        for name, model in SCHEMAS.items():
            print(f"{name}  v{schema_version_of(model)}")
        return 0
    print(json.dumps(SCHEMAS[args.name].model_json_schema(), indent=2))
    return 0


def _print_run(run: RunRecord) -> None:
    print(f"run     {run.id}")
    print(f"status  {run.status}")
    if run.error:
        print(f"error   {run.error}")
    if run.pause_reason:
        print(f"paused  {run.pause_reason}")
    for record in run.stages:
        note = " (edited)" if record.edited_output is not None else ""
        note += f": {record.error}" if record.error else ""
        print(f"  {record.key:<16} {record.status}{note}")
    competitors = run.stage("s1_competitors")
    if competitors.status == "awaiting_approval":
        _print_competitors(competitors.output)
    pain_points = run.stage("s3_pain_points")
    if pain_points.status == "awaiting_approval":
        _print_pain_points(pain_points.output)
        print(f"quotes  productfoundry report {run.id}")
    if run.status == "awaiting_approval":
        print(f"next    productfoundry approve {run.id}")


def _print_competitors(output: dict) -> None:
    print("competitors")
    for number, competitor in enumerate(output["competitors"], start=1):
        tag = " (incumbent)" if competitor["is_incumbent"] else ""
        stores = ", ".join(f"{k}: {v}" for k, v in competitor["store_ids"].items() if v)
        print(f"  {number}. {competitor['name']}{tag}  {competitor['url']}")
        print(f"     {competitor['positioning']}")
        print(f"     why: {competitor['reason']}" + (f"  [{stores}]" if stores else ""))
    if output["rejected"]:
        print("rejected")
    for rejected in output["rejected"]:
        print(f"  - {rejected['name']}: {rejected['reason']}")


def _print_pain_points(output: dict) -> None:
    print("pain points")
    for point in output["pain_points"]:
        print(
            f"  {point['rank']}. {point['label']}  severity {point['severity']}/5, "
            f"{point['review_count']} reviews, {point['negative_share']:.0%} negative, "
            f"score {point['score']:g}  [{point['cluster_id']}]"
        )
        print(f"     {point['description']}")
    if not output["pain_points"]:
        print("  none: not enough negative reviews to form a theme")
    if output["junk_clusters"]:
        print("not pain points")
    for junk in output["junk_clusters"]:
        print(f"  - {junk['cluster_id']} ({junk['review_count']} reviews): {junk['reason']}")


if __name__ == "__main__":
    raise SystemExit(main())
