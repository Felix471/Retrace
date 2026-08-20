from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "support_pipeline"
GENERATOR = FIXTURE_ROOT / "generate.py"
EXPECTED_RUN_IDS = tuple(f"case-{index:02d}" for index in range(1, 11))
KNOWN_MALFORMED_RUN = "case-07"
EXPECTED_MALFORMED_COUNT = 2
EXPECTED_FILES = {"events.jsonl", "meta.json"}
EXPECTED_META_KEYS = {
    "issue_area",
    "model_name",
    "outcome",
    "routing_variant",
    "run_id",
}
EXPECTED_HANDLERS = ("triage", "specialist", "reviewer")
EXPECTED_STEP_KINDS = {"message", "tool_call", "tool_result"}
MAX_FIXTURE_BYTES = 1_000_000


def _run_directories(root: Path = FIXTURE_ROOT) -> list[Path]:
    return sorted(path for path in root.iterdir() if path.is_dir())


def _tree_snapshot(
    root: Path,
    omitted: frozenset[str] = frozenset(),
) -> dict[str, bytes | None]:
    snapshot: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in omitted:
            continue
        key = f"{relative}/" if path.is_dir() else relative
        snapshot[key] = None if path.is_dir() else path.read_bytes()
    return snapshot


def _event_lines(run_dir: Path) -> list[str]:
    raw = (run_dir / "events.jsonl").read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        pytest.fail(f"invalid UTF-8 in {run_dir.name}: {error}", pytrace=False)
    return text.splitlines()


def _load_events(run_dir: Path) -> tuple[list[dict[str, object]], list[int]]:
    events: list[dict[str, object]] = []
    malformed_lines: list[int] = []
    for line_number, line in enumerate(_event_lines(run_dir), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed_lines.append(line_number)
            continue
        if not isinstance(value, dict):
            pytest.fail(
                f"non-object JSON at {run_dir.name}/events.jsonl:{line_number}",
                pytrace=False,
            )
        events.append(value)
    return events, malformed_lines


def _load_manifest(run_dir: Path) -> dict[str, object]:
    value = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        pytest.fail(f"non-object manifest in {run_dir.name}", pytrace=False)
    return value


def test_regeneration_is_byte_identical_to_committed_fixture(tmp_path: Path) -> None:
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"

    for output_dir in (first_output, second_output):
        subprocess.run(
            [sys.executable, str(GENERATOR), str(output_dir)],
            check=True,
            cwd=REPO_ROOT,
        )

    committed = _tree_snapshot(FIXTURE_ROOT, frozenset({"generate.py"}))
    assert _tree_snapshot(first_output) == _tree_snapshot(second_output) == committed


def test_fixture_layout_and_total_size() -> None:
    run_dirs = _run_directories()

    assert tuple(path.name for path in run_dirs) == EXPECTED_RUN_IDS
    assert {path.name for path in FIXTURE_ROOT.iterdir() if path.is_file()} == {"generate.py"}
    for run_dir in run_dirs:
        assert {path.name for path in run_dir.iterdir()} == EXPECTED_FILES
        assert all(path.is_file() for path in run_dir.iterdir())

    fixture_files = [path for path in FIXTURE_ROOT.rglob("*") if path.is_file()]
    assert sum(path.stat().st_size for path in fixture_files) < MAX_FIXTURE_BYTES
    for path in fixture_files:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r" not in raw
        assert raw.endswith(b"\n")
        assert not raw.endswith(b"\n\n")
        raw.decode("utf-8")


def test_jsonl_is_valid_except_known_malformed_lines() -> None:
    malformed: dict[str, list[int]] = {}

    for run_dir in _run_directories():
        _, line_numbers = _load_events(run_dir)
        if line_numbers:
            malformed[run_dir.name] = line_numbers
            line_count = len(_event_lines(run_dir))
            assert all(1 < line_number < line_count for line_number in line_numbers)

    assert {run_id: len(lines) for run_id, lines in malformed.items()} == {
        KNOWN_MALFORMED_RUN: EXPECTED_MALFORMED_COUNT
    }


def test_every_event_has_numeric_usage_and_positive_run_sums() -> None:
    for run_dir in _run_directories():
        events, _ = _load_events(run_dir)
        totals = {"tokens_in": 0.0, "tokens_out": 0.0, "cost": 0.0}

        assert events
        for event in events:
            for field_name in totals:
                value = event.get(field_name)
                assert type(value) in {int, float}
                assert math.isfinite(float(value))
                totals[field_name] += float(value)

        assert all(total > 0 for total in totals.values())


def test_manifest_outcome_coverage() -> None:
    outcomes: set[object] = set()

    for run_dir in _run_directories():
        manifest = _load_manifest(run_dir)
        assert set(manifest) == EXPECTED_META_KEYS
        assert manifest["run_id"] == run_dir.name
        outcomes.add(manifest["outcome"])

    assert {"escalated", "resolved"}.issubset(outcomes)


def test_event_type_handler_timestamp_and_tool_pair_coverage() -> None:
    all_step_kinds: set[object] = set()

    for run_dir in _run_directories():
        events, _ = _load_events(run_dir)
        handlers = tuple(dict.fromkeys(event["handler"] for event in events))
        assert handlers == EXPECTED_HANDLERS

        ticket_ids = {event["ticket_id"] for event in events}
        assert len(ticket_ids) == 1
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))

        calls: set[tuple[object, object]] = set()
        results: set[tuple[object, object]] = set()
        for event in events:
            handler = event["handler"]
            step_kind = event["step_kind"]
            all_step_kinds.add(step_kind)

            occurred_at = event["occurred_at"]
            assert isinstance(occurred_at, str)
            parsed = datetime.fromisoformat(occurred_at)
            assert parsed.utcoffset() is not None

            if step_kind == "tool_call":
                calls.add((handler, event["operation_id"]))
            elif step_kind == "tool_result":
                results.add((handler, event["operation_id"]))

        for handler in EXPECTED_HANDLERS:
            handler_kinds = {
                event["step_kind"] for event in events if event["handler"] == handler
            }
            assert EXPECTED_STEP_KINDS.issubset(handler_kinds)
        assert calls == results
        assert len(calls) == len(EXPECTED_HANDLERS)

    assert EXPECTED_STEP_KINDS.issubset(all_step_kinds)
