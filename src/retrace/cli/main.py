"""User-facing command-line commands for inspecting and loading logs."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from platformdirs import user_cache_dir

from retrace import __version__
from retrace.adapters.discovery import DiscoveryError
from retrace.adapters.mapping_schema import MappingConfigError
from retrace.adapters.registry import ConfigResolutionError, resolve_config
from retrace.core.ingest import IngestReport, ingest, stable_root_hash
from retrace.core.store import SqliteStore


def serve(store_path: Path, host: str, port: int, open_browser: bool) -> None:
    """Start the web application once its implementation is available."""
    del store_path, host, port, open_browser
    raise NotImplementedError("server lands in the next task")


def _ascii(value: object) -> str:
    return str(value).encode("ascii", "backslashreplace").decode("ascii")


def _hit_rate(hits: int, misses: int, failures: int) -> float | None:
    total = hits + misses + failures
    return None if total == 0 else 100 * hits / total


def _print_report(
    adapter_ref: str,
    report: IngestReport,
    summary: tuple[int, int, int],
    runs: Sequence[object],
) -> None:
    run_count, event_count, stored_warnings = summary
    warning_count = stored_warnings + len(report.line_failures)
    print(f"Adapter: {_ascii(adapter_ref)}")
    print(f"Runs: {run_count} runs")
    print(f"Events: {event_count} total events")
    print("Field hit rates:")
    for path, counter in sorted(report.field_stats.items()):
        total = counter.hits + counter.misses + counter.failures
        rate = _hit_rate(counter.hits, counter.misses, counter.failures)
        if rate is not None:
            print(f"  {_ascii(path)}: {rate:.1f}% ({counter.hits}/{total})")
    print(f"Warnings: {warning_count} total")
    print(f"Repaired: {sum(getattr(run, 'n_repaired') for run in runs)} total")
    print("Repair rules:")
    if report.repair_rule_counts:
        for (source, field, strategy), (fired, considered) in sorted(
            report.repair_rule_counts.items()
        ):
            print(
                f"  {_ascii(source)}.{_ascii(field)} ({_ascii(strategy)}): "
                f"fired {fired} of {considered} records"
            )
    else:
        print("  none")
    if report.line_failures:
        print("Line failures:")
        grouped: dict[Path, list[tuple[int, str]]] = defaultdict(list)
        for path, line_no, reason in report.line_failures:
            grouped[path].append((line_no, reason))
        for path in sorted(grouped, key=str):
            failures = grouped[path]
            for line_no, reason in failures[:10]:
                print(f"  {_ascii(path)}:{line_no}: {_ascii(reason)}")
            if len(failures) > 10:
                print(f"  {_ascii(path)}: (+{len(failures) - 10} more)")
    else:
        print("Line failures: 0")


def _resolve(args: argparse.Namespace) -> tuple[object, str]:
    explicit = None if args.config is None else Path(args.config)
    return resolve_config(Path(args.path), explicit)


def _check(args: argparse.Namespace) -> int:
    config, adapter_ref = _resolve(args)
    with SqliteStore(":memory:") as store:
        report = ingest(config, Path(args.path), store)
        runs = store.list_runs()
        _print_report(adapter_ref, report, store.experiment_summary(), runs)
    return 0


def _view(args: argparse.Namespace) -> int:
    root = Path(args.path)
    config, adapter_ref = _resolve(args)
    cache_dir = Path(args.cache_dir) if args.cache_dir else Path(user_cache_dir("retrace"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    store_path = cache_dir / f"{stable_root_hash(root)}.db"

    def progress(_run_id: str, current: int, total: int) -> None:
        print(f"\rIngesting runs: {current}/{total}", end="", flush=True)

    with SqliteStore(store_path) as store:
        report = ingest(config, root, store, reingest=args.reingest, progress=progress)
        stored_summary = store.experiment_summary()
        summary = (
            stored_summary[0],
            stored_summary[1],
            stored_summary[2] + len(report.line_failures),
        )
    if report.processed_run_ids:
        print()
    run_count, event_count, warning_count = summary
    print(
        f"Ingested: {run_count} runs, {event_count} events, "
        f"{warning_count} warnings ({_ascii(adapter_ref)})"
    )
    if report.config_hash_warning:
        print("Warning: mapping configuration changed; tag anchors may detach")
    try:
        serve(store_path, args.host, args.port, not args.no_browser)
    except NotImplementedError as error:
        print(_ascii(error))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrace-logs")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="inspect what would be ingested")
    check.add_argument("path")
    check.add_argument("--config")
    check.set_defaults(handler=_check)
    view = commands.add_parser("view", help="ingest logs and open the viewer")
    view.add_argument("path")
    view.add_argument("--config")
    view.add_argument("--port", type=int, default=8000)
    view.add_argument("--host", default="127.0.0.1")
    view.add_argument("--no-browser", action="store_true")
    view.add_argument("--reingest", action="store_true")
    view.add_argument("--cache-dir")
    view.set_defaults(handler=_view)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, execute a command, and return its exit status."""
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except (ConfigResolutionError, DiscoveryError, MappingConfigError, OSError) as error:
        print(_ascii(error), file=sys.stderr)
        return 1


__all__ = ["main", "serve"]
