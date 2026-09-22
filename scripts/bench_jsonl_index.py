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
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from retrace.adapters.discovery import iter_jsonl_records
from retrace.core.jsonl_index import JsonlIndex, find_indexer, index_path, load_index

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


def _python_object_at(path: Path, ordinal: int) -> dict[str, object]:
    object_ordinal = 0
    for _, item in iter_jsonl_records(path):
        if not isinstance(item, dict):
            continue
        if object_ordinal == ordinal:
            return item
        object_ordinal += 1
    raise RuntimeError(f"Python reader did not yield object record {ordinal}")


def _format_timings(timings: tuple[float, ...]) -> str:
    return ", ".join(f"{seconds:.6f}" for seconds in timings)


def _print_report(
    path: Path,
    source_size: int,
    index: JsonlIndex,
    object_count: int,
    bad_line_count: int,
    measurements: Sequence[Measurement],
) -> None:
    python_version = platform.python_version()
    print(
        f"Machine: {platform.platform()} | Python {python_version} | "
        f"CPU count: {os.cpu_count()}"
    )
    print(f"Date (UTC): {datetime.now(UTC):%Y-%m-%d}")
    print(f"Input: {path}")
    print(f"File size: {source_size} bytes ({source_size / (1024 * 1024):.3f} MiB)")
    print(f"Physical line count: {len(index.entries)}")
    print(f"Object record count: {object_count}")
    print(f"Bad-line count: {bad_line_count}")
    print()
    print("| Measurement | Median wall time (s) |")
    print("| --- | ---: |")
    for measurement in measurements:
        print(f"| {measurement.name} | {measurement.median:.3f} |")
    ratio = measurements[0].median / measurements[1].median
    print(f"Ratio (a)/(b): {ratio:.2f}")
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
        if sampled:
            indexed_object = index.record_object(sampled[0])
            python_object = _python_object_at(path, sampled[0])
            if indexed_object != python_object:
                raise RuntimeError(
                    f"indexed object {sampled[0]} does not match the Python reader"
                )

        def access_records() -> None:
            for ordinal in sampled:
                index.record_object(ordinal)

        for _ in range(args.runs):
            elapsed, _ = _timed(access_records)
            access_timings.append(elapsed)

        measurements = (
            Measurement("Python full pass", tuple(python_timings)),
            Measurement("Indexer build", tuple(build_timings)),
            Measurement(f"Indexed random access, {len(sampled)} records", tuple(access_timings)),
        )
        _print_report(
            path,
            initial_stat.st_size,
            index,
            object_count,
            bad_line_count,
            measurements,
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
