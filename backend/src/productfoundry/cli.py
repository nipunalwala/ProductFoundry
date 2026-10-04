import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from productfoundry import __version__
from productfoundry.core.base import schema_version_of
from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.registry import SCHEMAS
from productfoundry.core.run_input import RunInput
from productfoundry.orchestrator import InMemoryRunStore, Orchestrator, RunRecord
from productfoundry.stages.fakes import FAKE_STAGES

DEFAULT_STATE_FILE = Path(".productfoundry") / "runs.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="productfoundry",
        description="Turn a software product idea into an evidence-backed build plan.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="where runs are kept between commands (default: %(default)s)",
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
    approve.set_defaults(handler=_cmd_approve)

    resume = commands.add_parser("resume", help="continue a paused or failed run")
    resume.add_argument("run_id")
    resume.add_argument(
        "--from-stage", metavar="STAGE", help="run this stage again; later stages are invalidated"
    )
    resume.set_defaults(handler=_cmd_resume)

    schema = commands.add_parser("schema", help="export the JSON Schema of the contracts")
    schema.add_argument("name", nargs="?", choices=sorted(SCHEMAS), help="print one schema")
    schema.add_argument("--out", type=Path, help="write every schema to this directory")
    schema.set_defaults(handler=_cmd_schema)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except ProductFoundryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _load_store(path: Path) -> InMemoryRunStore:
    if path.exists():
        return InMemoryRunStore.load_json(path.read_text(encoding="utf-8"))
    return InMemoryRunStore()


def _save_store(store: InMemoryRunStore, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(store.dump_json(), encoding="utf-8")


def _orchestrator(store: InMemoryRunStore) -> Orchestrator:
    return Orchestrator(store, FAKE_STAGES)


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
    store = _load_store(args.state_file)
    orchestrator = _orchestrator(store)
    run = orchestrator.create_run(run_input, seed=args.seed)
    run = orchestrator.resume(run.id)
    _save_store(store, args.state_file)
    _print_run(run)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store = _load_store(args.state_file)
    if args.run_id is None:
        runs = store.list_runs()
        if not runs:
            print("no runs")
        for run in runs:
            print(f"{run.id}  {run.status:<17}  {run.created_at:%Y-%m-%d %H:%M}  {run.input.idea}")
        return 0
    run = store.get(args.run_id)
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
    store = _load_store(args.state_file)
    orchestrator = _orchestrator(store)
    orchestrator.approve(args.run_id, edited)
    run = orchestrator.resume(args.run_id)
    _save_store(store, args.state_file)
    _print_run(run)
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    store = _load_store(args.state_file)
    run = _orchestrator(store).resume(args.run_id, from_stage=args.from_stage)
    _save_store(store, args.state_file)
    _print_run(run)
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
    if run.status == "awaiting_approval":
        print(f"next    productfoundry approve {run.id}")


if __name__ == "__main__":
    raise SystemExit(main())
