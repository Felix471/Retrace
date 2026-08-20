from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from retrace.adapters.mapping_schema import load_mapping_config
from retrace.adapters.registry import sniff_config
from retrace.cli.init_scaffold import (
    detect_candidates,
    detect_layout,
    is_small_cardinality,
    is_timestamp,
    render_draft,
    unique_id_candidate,
)
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore
from scripts.check_core_purity import check

REPO_ROOT = Path(__file__).resolve().parents[1]
# Marker permits this fixture-loading test to exercise the real sample tree.
FIXTURE_MARKER = "avalon_mini"


def _cli(*args: object, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "retrace.cli", *(str(arg) for arg in args)],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _apply_stored_patch(text: str, patch: Path) -> tuple[str, int]:
    lines = text.splitlines()
    patch_lines = patch.read_text(encoding="utf-8").splitlines()
    removed = [line[1:] for line in patch_lines if line.startswith("-") and not line.startswith("---")]
    added = [line[1:] for line in patch_lines if line.startswith("+") and not line.startswith("+++")]
    for old, new in zip(removed, added, strict=True):
        lines[lines.index(old)] = new
    edits = sum(not line.lstrip().startswith("#") for line in added)
    return "\n".join(lines) + "\n", edits


@pytest.mark.parametrize(
    ("fixture", "runs", "events"),
    [("avalon_mini", 5, 652), ("support_pipeline", 10, 120)],
)
def test_init_then_ingest_stored_patch(
    tmp_path: Path, fixture: str, runs: int, events: int
) -> None:
    source = REPO_ROOT / "fixtures" / fixture
    logs = tmp_path / "logs"
    shutil.copytree(source, logs)
    before = _tree_hash(logs)

    result = _cli("init", logs, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert _tree_hash(logs) == before
    draft = tmp_path / "retrace.yaml"
    load_mapping_config(draft)
    patched, edits = _apply_stored_patch(
        draft.read_text(encoding="ascii"),
        REPO_ROOT / "tests" / "data" / "init_patches" / f"{fixture}.patch",
    )
    draft.write_text(patched, encoding="ascii")
    assert edits <= 3
    config = load_mapping_config(draft)
    assert sniff_config(config, logs) is True
    with SqliteStore(":memory:") as store:
        ingest(config, logs, store)
        assert store.experiment_summary()[:2] == (runs, events)


def test_slot_detectors_and_hit_rate() -> None:
    records = [
        {"occurred_at": "2026-01-01T00:00:00Z", "sequence": 1, "actor": "a", "body": "a long message"},
        {"occurred_at": "2026-01-01T00:00:01Z", "sequence": 2, "actor": "b", "body": "another long message"},
        {"sequence": "bad", "actor": "a"},
    ]
    timestamp = detect_candidates(records, "timestamp")[0]
    turn = detect_candidates(records, "turn")[0]
    assert (timestamp.field, timestamp.hits, timestamp.total) == ("occurred_at", 2, 3)
    assert (turn.field, turn.hits, turn.total) == ("sequence", 2, 3)
    assert detect_candidates(records, "agent_id")[0].field == "actor"
    assert detect_candidates(records, "content")[0].field == "body"


@pytest.mark.parametrize(
    "slot,value",
    [
        ("role", "assistant"), ("type", "message"), ("phase", "review"),
        ("tokens_in", 3), ("tokens_out", 4), ("cost", 0.02),
    ],
)
def test_each_remaining_slot(slot: str, value: object) -> None:
    key = {"role": "persona", "type": "event_kind", "phase": "stage"}.get(slot, slot)
    assert detect_candidates([{key: value}], slot)[0].field == key


def test_timestamp_shapes_cardinality_and_unique_id() -> None:
    assert is_timestamp("2026-01-01T12:30:00Z") == (True, "ISO-8601")
    assert is_timestamp(1_700_000_000) == (True, "epoch-like")
    assert is_timestamp("yesterday")[0] is False
    assert is_small_cardinality(["a", "b", "a"], 20)
    candidate = unique_id_candidate([{"run_id": "one"}, {"run_id": "two"}])
    assert candidate is not None and candidate.field == "run_id"
    assert unique_id_candidate([{"run_id": "same"}, {"run_id": "same"}]) is None


def test_todo_and_type_map_rendering(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"event_kind":"message","opaque":{}}\n{"event_kind":"custom","opaque":{}}\n')
    draft = render_draft(detect_layout(path))
    assert "# TODO: timestamp" in draft
    assert "type:" in draft and "custom: other" in draft and "message: message" in draft
    assert "runner-up" in draft or "100% of 2 sampled records" in draft


def test_sniff_excludes_partial_coverage_keys(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"record_id":"one","event_kind":"message","optional":"x"}\n'
        '{"record_id":"two","event_kind":"message"}\n'
    )
    draft_path = tmp_path / "draft.yaml"
    draft_path.write_text(render_draft(detect_layout(path)))
    config = load_mapping_config(draft_path)
    assert config.sniff is not None
    assert config.sniff.required_fields == ["record_id", "event_kind"]
    assert "optional" not in config.sniff.required_fields
    assert "present in 2/2 sampled records" in draft_path.read_text()
    assert sniff_config(config, path) is True


def test_sniff_todo_when_no_key_has_full_coverage(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"left":1}\n{"right":2}\n')
    draft_path = tmp_path / "draft.yaml"
    draft_path.write_text(render_draft(detect_layout(path)))
    config = load_mapping_config(draft_path)
    assert config.sniff is None
    assert "# TODO: sniff" in draft_path.read_text()
    assert "left 50%" in draft_path.read_text()


def test_layout_detection_line_dir_file(tmp_path: Path) -> None:
    line = tmp_path / "aggregate.jsonl"
    line.write_text('{"run_id":"a","events":[{"text":"long enough"}]}\n')
    assert detect_layout(line).unit == "line"

    line.unlink()
    for name in ("one", "two"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "events.jsonl").write_text('{"text":"long enough"}\n')
        (directory / "meta.json").write_text('{"status":"ok"}\n')
    layout = detect_layout(tmp_path)
    assert (layout.unit, layout.events_file, layout.manifest) == ("dir", "events.jsonl", "meta.json")

    shutil.rmtree(tmp_path / "one")
    shutil.rmtree(tmp_path / "two")
    (tmp_path / "one.jsonl").write_text('{"text":"long enough"}\n')
    (tmp_path / "two.jsonl").write_text('{"text":"long enough"}\n')
    assert detect_layout(tmp_path).unit == "file"


@pytest.mark.parametrize("kind", ["empty", "binary", "missing"])
def test_helpful_failures(tmp_path: Path, kind: str) -> None:
    logs = tmp_path / "logs"
    if kind != "missing":
        logs.mkdir()
    if kind == "binary":
        (logs / "bad.jsonl").write_bytes(b"\xff\x00\xfe")
    result = _cli("init", logs, cwd=tmp_path)
    assert result.returncode != 0
    expected = "does not exist" if kind == "missing" else "no parseable JSONL lines"
    assert expected in result.stderr


def test_out_force_and_refusal(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "events.jsonl").write_text('{"message":"long enough"}\n')
    output = tmp_path / "nested" / "draft.yaml"
    assert _cli("init", logs, "--out", output, cwd=tmp_path).returncode == 0
    original = output.read_text()
    refusal = _cli("init", logs, "--out", output, cwd=tmp_path)
    assert refusal.returncode != 0 and "already exists" in refusal.stderr
    assert output.read_text() == original
    assert _cli("init", logs, "--out", output, "--force", cwd=tmp_path).returncode == 0


def test_new_module_passes_core_purity() -> None:
    assert check(REPO_ROOT) == []
