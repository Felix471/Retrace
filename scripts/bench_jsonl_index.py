"""Benchmark native JSONL indexing against the pure-Python reader.

The benchmark reads a user-supplied JSONL file and writes only the native
indexer's default sidecar. Generated or corpus data is never committed.
"""

from __future__ import annotations

import argparse
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from retrace.adapters.discovery import iter_jsonl_records
from retrace.adapters.mapping_schema import MappingConfig
from retrace.adapters.registry import resolve_config
from retrace.core.ingest import ingest
from retrace.core.jsonl_index import (
    STATUS_OK,
    JsonlIndex,
    find_indexer,
    index_path,
    load_index,
)
from retrace.core.store import SqliteStore

DEFAULT_RUNS = 3
DEFAULT_SAMPLES = 1000
DEFAULT_SEED = 31_031
T = TypeVar("T")


@dataclass(frozen=True)
class Measurement:
    """One named wall-time measurement with its individual runs."""

    name: str
    timings: tuple[float, ...]

    @property
    def median(self) -> float:
        """Return the median elapsed time in seconds."""
        return statistics.median(self.timings)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSONL source file")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--keep-index", action="store_true")
    parser.add_argument("--ingest-config", type=Path, help="line-unit mapping YAML")
    return parser


def _timed(function: Callable[[], T]) -> tuple[float, T]:
    started = time.perf_counter()
    result = function()
    return time.perf_counter() - started, result


def _python_pass(path: Path) -> tuple[int, int]:
    object_count = 0
    bad_line_count = 0
    for _, item in iter_jsonl_records(path):
        if isinstance(item, dict):
            object_count += 1
        else:
            bad_line_count += 1
    return object_count, bad_line_count


def _build_index(indexer: Path, path: Path, sidecar: Path) -> None:
    sidecar.unlink(missing_ok=True)
    subprocess.run([indexer, str(path)], check=True, capture_output=True)


def _sample_indices(index: JsonlIndex, samples: int, seed: int) -> list[int]:
    if len(index) <= samples:
        return list(range(len(index)))
    rng = random.Random(seed)
    return [rng.randrange(len(index)) for _ in range(samples)]


def _fetch_lines(path: Path, wanted_lines: set[int]) -> dict[int, dict[str, object] | str]:
    return {line_no: item for line_no, item in iter_jsonl_records(path) if line_no in wanted_lines}


def _ingest_once(config: MappingConfig, path: Path) -> tuple[float, int, int]:
    with (
        tempfile.TemporaryDirectory(prefix="retrace-jsonl-index-bench-") as temporary,
        SqliteStore(Path(temporary) / "benchmark.db") as store,
    ):
        elapsed, report = _timed(lambda: ingest(config, path, store))
    return elapsed, report.runs_ingested, len(report.line_failures)


def _format_timings(timings: tuple[float, ...]) -> str:
    return ", ".join(f"{seconds:.6f}" for seconds in timings)


def _print_report(
    path: Path,
    source_size: int,
    index: JsonlIndex,
    object_count: int,
    bad_line_count: int,
    measurements: Sequence[Measurement],
    ingest_counts: tuple[tuple[int, int], tuple[int, int]] | None,
) -> None:
    python_version = platform.python_version()
    print(f"Machine: {platform.platform()} | Python {python_version} | CPU count: {os.cpu_count()}")
    print(f"Date (UTC): {datetime.now(UTC):%Y-%m-%d}")
    print(f"Input: {path}")
    print(f"File size: {source_size} bytes ({source_size / (1024 * 1024):.3f} MiB)")
    print(f"Physical line count: {len(index.entries)}")
    print(f"Object record count: {object_count}")
    print(f"Bad-line count: {bad_line_count}")
    if ingest_counts is None:
        print("ingest measurement skipped (no --ingest-config)")
    else:
        without_counts, with_counts = ingest_counts
        print(
            "Line-unit ingest without index: "
            f"{without_counts[0]} runs ingested, {without_counts[1]} line failures"
        )
        print(
            "Line-unit ingest with index: "
            f"{with_counts[0]} runs ingested, {with_counts[1]} line failures"
        )
    print()
    print("| Measurement | Median wall time (s) |")
    print("| --- | ---: |")
    for measurement in measurements:
        print(f"| {measurement.name} | {measurement.median:.3f} |")
    print(f"Ratio (a)/(b): {measurements[0].median / measurements[1].median:.2f}")
    print(f"Ratio (d)/(c): {measurements[3].median / measurements[2].median:.2f}")
    if ingest_counts is not None:
        print(f"Ratio (e without)/(e with): {measurements[4].median / measurements[5].median:.2f}")
    print()
    for measurement in measurements:
        print(f"Raw timings - {measurement.name} (s): {_format_timings(measurement.timings)}")


def _run(args: argparse.Namespace, indexer: Path) -> int:
    path = args.input.resolve()
    sidecar = index_path(path)
    initial_stat = path.stat()
    python_timings: list[float] = []
    build_timings: list[float] = []
    access_timings: list[float] = []
    fetch_timings: list[float] = []
    object_count = 0
    bad_line_count = 0
    cleanup_message = ""
    try:
        for run_number in range(args.runs):
            elapsed, counts = _timed(lambda: _python_pass(path))
            python_timings.append(elapsed)
            current_object_count, current_bad_line_count = counts
            if run_number == 0:
                object_count = current_object_count
                bad_line_count = current_bad_line_count
            elif counts != (object_count, bad_line_count):
                raise RuntimeError("Python reader counts changed between runs")

        for _ in range(args.runs):
            elapsed, _ = _timed(lambda: _build_index(indexer, path, sidecar))
            build_timings.append(elapsed)
            if load_index(path) is None:
                raise RuntimeError("native indexer did not produce a valid current sidecar index")

        index = load_index(path)
        if index is None:
            raise RuntimeError("could not load the sidecar index after building it")
        sampled = _sample_indices(index, args.samples, args.seed)
        ok_entries = tuple(entry for entry in index.entries if entry.status == STATUS_OK)
        wanted_lines = {ok_entries[ordinal].line_no for ordinal in sampled}

        def access_records() -> None:
            for ordinal in sampled:
                index.record_object(ordinal)

        for _ in range(args.runs):
            elapsed, _ = _timed(access_records)
            access_timings.append(elapsed)

        found: dict[int, dict[str, object] | str] = {}
        for _ in range(args.runs):
            elapsed, found = _timed(lambda: _fetch_lines(path, wanted_lines))
            fetch_timings.append(elapsed)

        for ordinal in sampled:
            line_no = ok_entries[ordinal].line_no
            if line_no not in found:
                raise RuntimeError(f"Python reader did not yield line {line_no}")
            python_object = found[line_no]
            if index.record_object(ordinal) != python_object:
                raise RuntimeError(
                    f"indexed object {ordinal} does not match Python reader line {line_no}"
                )

        measurements: list[Measurement] = [
            Measurement("Python full pass", tuple(python_timings)),
            Measurement("Indexer build", tuple(build_timings)),
            Measurement(f"Indexed random access, {len(sampled)} records", tuple(access_timings)),
            Measurement(
                f"Python fetch, {len(sampled)} records by line number",
                tuple(fetch_timings),
            ),
        ]
        ingest_counts: tuple[tuple[int, int], tuple[int, int]] | None = None
        if args.ingest_config is not None:
            config, _ = resolve_config(path, explicit=args.ingest_config)
            if config.run_discovery.unit != "line":
                raise RuntimeError("--ingest-config must use run_discovery unit 'line'")

            without_timings: list[float] = []
            without_results: list[tuple[int, int]] = []
            for _ in range(args.runs):
                sidecar.unlink(missing_ok=True)
                elapsed, runs_ingested, line_failures = _ingest_once(config, path)
                without_timings.append(elapsed)
                without_results.append((runs_ingested, line_failures))

            _build_index(indexer, path, sidecar)
            if load_index(path) is None:
                raise RuntimeError("native indexer did not produce a valid index for ingest")
            with_timings: list[float] = []
            with_results: list[tuple[int, int]] = []
            for _ in range(args.runs):
                elapsed, runs_ingested, line_failures = _ingest_once(config, path)
                with_timings.append(elapsed)
                with_results.append((runs_ingested, line_failures))

            if len(set(without_results)) != 1 or len(set(with_results)) != 1:
                raise RuntimeError("line-unit ingest counts changed between runs")
            without_counts = without_results[0]
            with_counts = with_results[0]
            if without_counts[0] != with_counts[0]:
                raise RuntimeError(
                    "line-unit ingest run counts differ: "
                    f"without index {without_counts[0]}, with index {with_counts[0]}"
                )
            ingest_counts = (without_counts, with_counts)
            measurements.extend(
                [
                    Measurement("Line-unit ingest without index", tuple(without_timings)),
                    Measurement("Line-unit ingest with index", tuple(with_timings)),
                ]
            )

        _print_report(
            path,
            initial_stat.st_size,
            index,
            object_count,
            bad_line_count,
            measurements,
            ingest_counts,
        )
        return 0
    finally:
        if args.keep_index:
            cleanup_message = f"Kept sidecar index: {sidecar}"
        else:
            sidecar.unlink(missing_ok=True)
            cleanup_message = f"Removed sidecar index: {sidecar}"
        print(cleanup_message)
        final_stat = path.stat()
        if (
            final_stat.st_size != initial_stat.st_size
            or final_stat.st_mtime_ns != initial_stat.st_mtime_ns
        ):
            raise RuntimeError("source file size or modification time changed during benchmark")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.runs <= 0:
        print("error: --runs must be positive", file=sys.stderr)
        return 1
    if args.samples < 0:
        print("error: --samples must not be negative", file=sys.stderr)
        return 1
    if not args.input.is_file():
        print(f"input file not found: {args.input}", file=sys.stderr)
        return 1
    indexer = find_indexer()
    if indexer is None:
        print(
            "native indexer not found: set RETRACE_JSONL_INDEXER or put "
            "retrace-jsonl-index on PATH",
            file=sys.stderr,
        )
        return 2
    try:
        return _run(args, indexer)
    except subprocess.CalledProcessError as error:
        print(f"native indexer failed with exit code {error.returncode}", file=sys.stderr)
        stderr = error.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        if stderr:
            print(stderr.rstrip(), file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as error:
        print(f"benchmark failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
