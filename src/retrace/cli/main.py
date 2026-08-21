"""User-facing command-line commands for inspecting and loading logs."""

from __future__ import annotations

import argparse
import socket
import sys
import webbrowser
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from platformdirs import user_cache_dir

from retrace import __version__
from retrace.adapters.discovery import DiscoveryError
from retrace.adapters.mapping_schema import MappingConfigError
from retrace.adapters.registry import ConfigResolutionError, resolve_config
from retrace.cli.init_scaffold import ScaffoldError, scaffold
from retrace.core.ingest import IngestReport, ingest, stable_root_hash
from retrace.core.store import SqliteStore
from retrace.server.app import create_app


def _resolve_bind_host(host: str | None) -> str:
    """Resolve the safe implicit host and warn about explicit network exposure."""
    if host is None:
        return "127.0.0.1"
    if host not in {"127.0.0.1", "localhost"}:
        print(f"Warning: explicitly serving on non-local host {_ascii(host)}")
    return host


def _pick_free_port(host: str = "127.0.0.1") -> int:
    """Ask the operating system for an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


def serve(store_path: Path, host: str, port: int | None, open_browser: bool) -> None:
    """Start the web application in this process."""
    import uvicorn

    selected_port = _pick_free_port(host) if port is None else port
    url_host = "127.0.0.1" if host == "localhost" else host
    url = f"http://{url_host}:{selected_port}"
    print(f"Serving: {url}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(store_path), host=host, port=selected_port)


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
    if report.roster_join_enabled:
        total = report.roster_agent_bearing
        rate = None if total == 0 else 100 * report.roster_matched / total
        aggregate = "n/a" if rate is None else f"{rate:.1f}%"
        print("Roster join:")
        print(
            f"  matched {report.roster_matched} of {total} "
            f"agent-bearing events ({aggregate})"
        )
        items = list(report.roster_join_by_run.items())
        for run_id, (matched, agent_bearing) in items[:20]:
            run_rate = None if agent_bearing == 0 else 100 * matched / agent_bearing
            percentage = "n/a" if run_rate is None else f"{run_rate:.1f}%"
            print(f"  {_ascii(run_id)}: {matched}/{agent_bearing} ({percentage})")
        if len(items) > 20:
            print(f"  (+{len(items) - 20} more)")
    print(f"Repaired: {sum(run.n_repaired for run in runs)} total")
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
        report = ingest(config, Path(args.path), store, adapter_ref=adapter_ref)
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
        report = ingest(
            config,
            root,
            store,
            adapter_ref=adapter_ref,
            reingest=args.reingest,
            progress=progress,
        )
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
    serve(store_path, _resolve_bind_host(args.host), args.port, not args.no_browser)
    return 0


def _init(args: argparse.Namespace) -> int:
    target = Path(args.out) if args.out else Path.cwd() / "retrace.yaml"
    if target.exists() and not args.force:
        print(f"{_ascii(target)} already exists; use --force to overwrite", file=sys.stderr)
        return 1
    draft = scaffold(Path(args.path), args.sample)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(draft, encoding="ascii")
    print(f"Wrote {_ascii(target)}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrace-logs")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="draft a mapping from sampled logs")
    init.add_argument("path")
    init.add_argument("--out")
    init.add_argument("--sample", type=int, default=200)
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=_init)
    check = commands.add_parser("check", help="inspect what would be ingested")
    check.add_argument("path")
    check.add_argument("--config")
    check.set_defaults(handler=_check)
    view = commands.add_parser("view", help="ingest logs and open the viewer")
    view.add_argument("path")
    view.add_argument("--config")
    view.add_argument("--port", type=int)
    view.add_argument("--host")
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
    except (ConfigResolutionError, DiscoveryError, MappingConfigError, ScaffoldError, OSError) as error:
        print(_ascii(error), file=sys.stderr)
        return 1


__all__ = ["main", "serve"]
