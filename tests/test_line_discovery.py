from __future__ import annotations

import json
from pathlib import Path
from typing import BinaryIO

import pytest

from retrace.adapters.discovery import discover_runs, discover_runs_with_report
from retrace.adapters.mapping_schema import validate_mapping_config

PROJECT_ROOT = Path(__file__).parents[1]
EXPECTED_IDS = [
    "1776453329940-bvvf9",
    "1776470168031-kwy8o",
    "1776471689022-f5veq",
    "1776642987532-fob9d",
    "1776701240615-6ius8",
]


def _config(pattern: str = "*.jsonl", run_id: str = "gameId"):
    return validate_mapping_config(
        {
            "retrace_mapping": 1,
            "run_discovery": {"pattern": pattern, "unit": "line"},
            "run": {"id": run_id, "manifest": "ignored.json"},
            "event": {},
        }
    )


SAMPLES = [
    pytest.param(PROJECT_ROOT / "fixtures" / "avalon_mini" / "games.jsonl", id="fixture"),
    pytest.param(
        PROJECT_ROOT / "reference-logs" / "avalon_sample.jsonl",
        marks=pytest.mark.skipif(
            not (PROJECT_ROOT / "reference-logs" / "avalon_sample.jsonl").exists(),
            reason="local reference sample is absent",
        ),
        id="local-sample",
    ),
]


@pytest.mark.parametrize("path", SAMPLES)
def test_reference_sample(path: Path) -> None:
    report = discover_runs_with_report(_config(path.name), path.parent)
    assert [source.run_id for source in report.sources] == EXPECTED_IDS
    assert [source.line_no for source in report.sources] == [1, 2, 3, 4, 5]
    assert all(source.warnings == () for source in report.sources)
    assert report.line_failures == []


def test_corrupt_line_is_reported_and_skipped(tmp_path: Path) -> None:
    source = PROJECT_ROOT / "fixtures" / "avalon_mini" / "games.jsonl"
    lines = source.read_bytes().splitlines(keepends=True)
    lines[2] = b"{bad json}\n"
    path = tmp_path / "games.jsonl"
    path.write_bytes(b"".join(lines))
    report = discover_runs_with_report(_config(), tmp_path)
    assert [item.run_id for item in report.sources] == EXPECTED_IDS[:2] + EXPECTED_IDS[3:]
    assert report.line_failures[0][:2] == (path, 3)
    assert report.per_file_failure_counts[path] == 1


def test_missing_and_duplicate_ids_use_fallback(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    path.write_text('{"id":"x"}\n{"other":1}\n{"id":"x"}\n', encoding="utf-8")
    sources = discover_runs_with_report(_config(run_id="id"), tmp_path).sources
    assert [source.run_id for source in sources] == ["x", "runs#L2", "runs#L3"]
    assert sources[0].warnings == ()
    assert "null or non-scalar" in sources[1].warnings[0]
    assert "duplicate run id" in sources[2].warnings[0]


def test_lf_and_crlf_are_equivalent(tmp_path: Path) -> None:
    content = b'{"id":1}\n{"id":2}\n'
    (tmp_path / "lf.jsonl").write_bytes(content)
    (tmp_path / "crlf.jsonl").write_bytes(content.replace(b"\n", b"\r\n"))
    config = _config(run_id="id")
    lf = discover_runs(config, tmp_path / "lf.jsonl")
    crlf = discover_runs(config, tmp_path / "crlf.jsonl")
    assert [(run.run_id, run.line_no) for run in lf] == [
        (run.run_id, run.line_no) for run in crlf
    ]


def test_blank_lines_bom_and_trailing_newline(tmp_path: Path) -> None:
    (tmp_path / "runs.jsonl").write_bytes(b'\xef\xbb\xbf{"id":"a"}\n \t\n{"id":"b"}\n')
    report = discover_runs_with_report(_config(run_id="id"), tmp_path)
    assert [(run.run_id, run.line_no) for run in report.sources] == [("a", 1), ("b", 3)]
    assert report.line_failures == []
    (tmp_path / "runs.jsonl").write_text("\n \r\n\t", encoding="utf-8")
    assert discover_runs_with_report(_config(run_id="id"), tmp_path).sources == []


def test_invalid_utf8_is_a_line_failure(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    path.write_bytes(b'{"id":"a"}\n\xff\xfe\n{"id":"b"}\n')
    report = discover_runs_with_report(_config(run_id="id"), tmp_path)
    assert [run.run_id for run in report.sources] == ["a", "b"]
    assert report.line_failures[0][:2] == (path, 2)
    assert "UTF-8" in report.line_failures[0][2]


def test_streams_without_slurping_and_accepts_large_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "large.jsonl"
    path.write_text(json.dumps({"id": "large", "payload": "x" * 3_000_000}), encoding="utf-8")
    original_open = Path.open

    class GuardedFile:
        def __init__(self, wrapped: BinaryIO) -> None:
            self.wrapped = wrapped

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            self.wrapped.close()

        def __iter__(self):
            return iter(self.wrapped)

        def read(self, size: int = -1) -> bytes:
            if size < 0 or size > 1_000_000:
                raise AssertionError("whole-file read")
            return self.wrapped.read(size)

        def readlines(self, *args: object) -> list[bytes]:
            raise AssertionError("readlines called")

    def guarded_open(target: Path, *args: object, **kwargs: object):
        return GuardedFile(original_open(target, *args, **kwargs))

    monkeypatch.setattr(Path, "open", guarded_open)
    runs = discover_runs(_config(run_id="id"), path)
    assert [(run.run_id, run.line_no) for run in runs] == [("large", 1)]
