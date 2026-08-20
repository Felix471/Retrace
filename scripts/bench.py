"""Seeded end-to-end performance benchmark for retrace.

The benchmark uses only the standard library and project dependencies.  ``httpx``
is a development dependency and is intentionally used for in-process ASGI timing.
Generated data defaults to a system temporary directory and is never committed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import shutil
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from retrace.adapters.registry import resolve_config
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore
from retrace.server.app import create_app

DEFAULT_SEED = 31_031
COMPARE_RUNS = ("case-compare-a", "case-compare-b")
BUDGETS = {
    "Cold ingest": 90.0,
    "Warm start": 2.0,
    "/api/runs": 0.5,
    "Single run load": 1.0,
    "5,000-event compare": 2.0,
}


@dataclass(frozen=True)
class Measurement:
    """One named elapsed-time measurement."""

    name: str
    seconds: float


def _event(run_number: int, ordinal: int, rng: random.Random) -> dict[str, object]:
    handlers = ("triage", "research", "resolution", "quality")
    stages = ("intake", "investigation", "response", "review")
    kinds = ("message", "tool_call", "tool_result")
    occurred = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(
        days=run_number % 365, seconds=ordinal * 3
    )
    tokens_in = rng.randint(20, 400)
    tokens_out = rng.randint(10, 240)
    return {
        "record_id": f"rec-{run_number:04d}-{ordinal:05d}",
        "ticket_id": f"ticket-{run_number:04d}",
        "sequence": ordinal + 1,
        "occurred_at": occurred.isoformat(),
        "handler": handlers[(ordinal + run_number) % len(handlers)],
        "workflow_stage": stages[min(ordinal * len(stages) // 5000, len(stages) - 1)],
        "step_kind": kinds[ordinal % len(kinds)],
        "body": f"Synthetic support activity {ordinal} for ticket {run_number}",
        "operation_id": f"op-{ordinal // 3:05d}",
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": round((tokens_in * 0.000002) + (tokens_out * 0.000004), 8),
    }


def generate_dataset(
    output: Path,
    *,
    seed: int = DEFAULT_SEED,
    runs: int = 1000,
    events_min: int = 1800,
    events_max: int = 2200,
    compare_events: int = 5000,
) -> None:
    """Stream a deterministic support-pipeline-shaped dataset, one run at a time."""
    if runs < 2 or events_min < 1 or events_max < events_min or compare_events < 1:
        raise ValueError("at least two runs and positive, ordered event counts are required")
    output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    specifications = [
        (f"case-{index:04d}", rng.randint(events_min, events_max), index)
        for index in range(runs - 2)
    ]
    specifications.extend(
        [(COMPARE_RUNS[0], compare_events, runs), (COMPARE_RUNS[1], compare_events, runs)]
    )
    for run_id, count, run_number in specifications:
        run_dir = output / run_id
        run_dir.mkdir()
        manifest = {
            "run_id": run_id,
            "issue_area": ("billing", "account", "delivery")[run_number % 3],
            "model_name": ("standard", "fast")[run_number % 2],
            "routing_variant": ("control", "candidate")[run_number % 2],
            "outcome": ("resolved", "escalated")[run_number % 2],
        }
        (run_dir / "meta.json").write_text(json.dumps(manifest), encoding="utf-8")
        run_rng = random.Random(seed + run_number)
        with (run_dir / "events.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for ordinal in range(count):
                event = _event(run_number, ordinal, run_rng)
                if run_id == COMPARE_RUNS[1] and ordinal in {137, 2501, 4789}:
                    event["body"] = f"Edited support activity {ordinal} for ticket {run_number}"
                stream.write(json.dumps(event, separators=(",", ":")) + "\n")


async def _api_measurements(db_path: Path, include_warm: float) -> list[Measurement]:
    app = create_app(db_path)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://bench") as client,
    ):
        started = time.perf_counter()
        response = await client.get("/api/runs")
        response.raise_for_status()
        first_runs = time.perf_counter() - started
        warm = include_warm + first_runs

        started = time.perf_counter()
        response = await client.get("/api/runs")
        response.raise_for_status()
        runs_time = time.perf_counter() - started

        started = time.perf_counter()
        response = await client.get(f"/api/runs/{COMPARE_RUNS[0]}/events", params={"limit": 500})
        response.raise_for_status()
        run_load = time.perf_counter() - started

        started = time.perf_counter()
        response = await client.get(
            "/api/compare", params={"a": COMPARE_RUNS[0], "b": COMPARE_RUNS[1]}
        )
        response.raise_for_status()
        compare = time.perf_counter() - started
    return [
        Measurement("Warm start", warm),
        Measurement("/api/runs", runs_time),
        Measurement("Single run load", run_load),
        Measurement("5,000-event compare", compare),
    ]


def measure(dataset: Path, cache_dir: Path) -> list[Measurement]:
    """Run the real cold-ingest and warm/API measurements."""
    config, adapter_ref = resolve_config(dataset)
    db_path = cache_dir / "benchmark.db"
    started = time.perf_counter()

    def progress(_run_id: str, current: int, total: int) -> None:
        if current == total or current == 1 or current % max(1, total // 20) == 0:
            print(f"Ingesting runs: {current}/{total}", flush=True)

    with SqliteStore(db_path) as store:
        ingest(config, dataset, store, adapter_ref=adapter_ref, progress=progress)
    cold = time.perf_counter() - started

    started = time.perf_counter()
    with SqliteStore(db_path) as store:
        ingest(config, dataset, store, adapter_ref=adapter_ref)
        summary = store.experiment_summary()
    if summary[0] < 3:
        raise RuntimeError("benchmark ingest produced too few runs")
    reopen_summary = time.perf_counter() - started
    return [Measurement("Cold ingest", cold), *_run_async(db_path, reopen_summary)]


def _run_async(db_path: Path, reopen_summary: float) -> list[Measurement]:
    return asyncio.run(_api_measurements(db_path, reopen_summary))


def format_table(
    measurements: Sequence[Measurement], multiplier: float, *, assert_budgets: bool = True
) -> tuple[str, bool]:
    """Format results and return whether every applicable budget passed."""
    lines = [
        "+----------------------+------------+------------+--------+",
        "| Measurement          | Measured   | Budget     | Result |",
        "+----------------------+------------+------------+--------+",
    ]
    passed = True
    for item in measurements:
        budget = BUDGETS[item.name] * multiplier
        ok = item.seconds <= budget
        passed &= ok
        result = "PASS" if ok else "FAIL"
        if not assert_budgets:
            result = "SMOKE"
        lines.append(
            f"| {item.name:<20} | {item.seconds:>8.3f} s | {budget:>8.3f} s | {result:^6} |"
        )
    lines.append("+----------------------+------------+------------+--------+")
    return "\n".join(lines), passed or not assert_budgets


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, help="dataset output directory")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--runs", type=int, default=1000)
    parser.add_argument("--events-min", type=int, default=1800)
    parser.add_argument("--events-max", type=int, default=2200)
    parser.add_argument("--compare-events", type=int, default=5000)
    parser.add_argument("--multiplier", type=float, default=1.0)
    parser.add_argument("--smoke", action="store_true", help="exercise machinery without budgets")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.multiplier <= 0:
        raise SystemExit("--multiplier must be positive")
    temporary = args.output is None
    dataset = Path(tempfile.mkdtemp(prefix="retrace-bench-data-")) if temporary else args.output
    try:
        if dataset.exists() and any(dataset.iterdir()):
            raise SystemExit(f"output directory must be empty: {dataset}")
        generate_dataset(
            dataset,
            seed=args.seed,
            runs=args.runs,
            events_min=args.events_min,
            events_max=args.events_max,
            compare_events=args.compare_events,
        )
        with tempfile.TemporaryDirectory(prefix="retrace-bench-cache-") as cache:
            measurements = measure(dataset, Path(cache))
        table, passed = format_table(measurements, args.multiplier, assert_budgets=not args.smoke)
        print(table)
        return 0 if passed else 1
    finally:
        if temporary:
            shutil.rmtree(dataset, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
