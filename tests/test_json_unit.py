from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from retrace.adapters.discovery import discover_runs_with_report
from retrace.adapters.mapping_schema import MappingConfigError, validate_mapping_config
from retrace.adapters.registry import sniff_config
from retrace.cli.init_scaffold import detect_layout, render_draft
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore
from retrace.core.tags import TagService

REPO_ROOT = Path(__file__).resolve().parents[1]


def _raw(pattern: str = "*.json") -> dict[str, object]:
    return {
        "retrace_mapping": 1,
        "run_discovery": {"pattern": pattern, "unit": "json"},
        "run": {"id": "run_id", "metadata": {"correct": "other_data.correct"}},
        "event": {"sources": [{"name": "entries", "path": "entries", "fields": {
            "turn": "seq", "role": "role", "agent_id": "name", "content": "text"
        }}]},
        "sniff": {"required_fields": ["run_id", "entries"]},
    }


def _config(pattern: str = "*.json"):
    return validate_mapping_config(_raw(pattern))


def _write(path: Path, value: object, *, bom: bool = False) -> None:
    text = json.dumps(value, indent=2)
    path.write_text(("\ufeff" if bom else "") + text, encoding="utf-8")


def test_schema_json_and_contradictions() -> None:
    assert _config().run_discovery.unit == "json"
    raw = _raw()
    raw["run_discovery"] = {"pattern": "*.json", "unit": "json", "events_file": "x"}
    with pytest.raises(MappingConfigError, match="events_file cannot be combined with unit 'json'"):
        validate_mapping_config(raw)

    flat = _raw()
    flat["event"] = {"content": "text"}
    with SqliteStore(":memory:") as store, pytest.raises(
        MappingConfigError, match="event.sources: sources are required for json units"
    ):
        ingest(validate_mapping_config(flat), Path("."), store)


def test_discovery_documents_failures_ids_bom_and_single_file(tmp_path: Path) -> None:
    _write(tmp_path / "a.json", {"run_id": "a", "entries": []}, bom=True)
    _write(tmp_path / "null.json", {"run_id": None, "entries": []})
    _write(tmp_path / "dup.json", {"run_id": "a", "entries": []})
    _write(tmp_path / "array.json", [])
    (tmp_path / "bad.json").write_text("{bad", encoding="utf-8")

    report = discover_runs_with_report(_config(), tmp_path)
    assert [source.run_id for source in report.sources] == ["a", "dup", "null"]
    assert report.sources[1].warnings and report.sources[2].warnings
    assert {(path.name, line) for path, line, _ in report.line_failures} == {
        ("array.json", 1), ("bad.json", 1)
    }
    single = discover_runs_with_report(_config(), tmp_path / "a.json")
    assert len(single.sources) == 1 and single.sources[0].manifest is None


def test_ingest_incremental_sniff_init_and_tags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "a.json", {"run_id": "a", "status": "ok", "entries": [
        {"seq": 1, "role": "user", "name": "u", "text": "hello there"},
        {"seq": 2, "role": "assistant", "name": "a", "text": "answer here"},
    ]})
    _write(tmp_path / "b.json", {"run_id": "b", "status": "ok", "entries": [
        {"seq": 1, "role": "user", "name": "u", "text": "second run"}
    ]})
    config = _config()
    assert sniff_config(config, tmp_path)

    layout = detect_layout(tmp_path)
    assert layout.unit == "json"
    draft_path = tmp_path / "draft.yaml"
    draft_path.write_text(render_draft(layout), encoding="ascii")
    draft = validate_mapping_config(yaml.safe_load(draft_path.read_text()))
    assert draft.run_discovery.unit == "json"
    assert "present in 2/2 sampled records" in draft_path.read_text()

    with SqliteStore(":memory:") as store:
        first = ingest(config, tmp_path, store)
        assert (first.runs_ingested, store.experiment_summary()[:2]) == (2, (2, 3))
        sidecar, shared = TagService(store).sidecar_path("a")
        assert (sidecar.name, shared) == ("a.retrace.json", False)

        from retrace.adapters import discovery
        monkeypatch.setattr(discovery, "load_json_document", lambda _path: (_ for _ in ()).throw(AssertionError()))
        second = ingest(config, tmp_path, store)
        assert (second.runs_skipped_unchanged, second.runs_ingested) == (2, 0)


def test_duplicate_ids_across_json_files_use_relative_paths_and_check_warns(
    tmp_path: Path,
) -> None:
    for directory in ("alpha", "beta", "gamma"):
        target = tmp_path / directory / "same.json"
        target.parent.mkdir()
        _write(target, {"run_id": "shared", "entries": []})
    config = _config("**/*.json")

    discovery = discover_runs_with_report(config, tmp_path)
    assert [source.run_id for source in discovery.sources] == [
        "shared", "beta/same", "gamma/same"
    ]
    assert sum(len(source.warnings) for source in discovery.sources) == 2

    with SqliteStore(":memory:") as store:
        first = ingest(config, tmp_path, store)
        assert first.runs_ingested == 3
        assert len(store.list_runs()) == 3
        assert store.experiment_summary()[2] == 2
        second = ingest(config, tmp_path, store)
        assert (second.runs_skipped_unchanged, second.runs_ingested) == (3, 0)

    mapping = tmp_path / "mapping.yaml"
    mapping.write_text(yaml.safe_dump(_raw("**/*.json"), sort_keys=False), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "retrace.cli", "check", str(tmp_path), "--config", str(mapping)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "Warnings: 2 total" in result.stdout


def test_null_json_id_uses_relative_path_fallback(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "record.json"
    target.parent.mkdir()
    _write(target, {"run_id": None, "entries": []})
    source = discover_runs_with_report(_config("**/*.json"), tmp_path).sources[0]
    assert source.run_id == "nested/record"
    assert len(source.warnings) == 1
    assert "original id None" in source.warnings[0]


@pytest.mark.parametrize(
    ("name", "count", "source", "fields"),
    [
        ("ag2_sample.json", 10, "trajectory", {"role": "role", "agent_id": "name", "content": "content"}),
        ("hyper_sample.json", 773, "trajectory", {}),
    ],
)
def test_local_samples(name: str, count: int, source: str, fields: dict[str, str]) -> None:
    path = REPO_ROOT / "reference-logs" / "mast" / name
    if not path.exists():
        pytest.skip(f"local sample missing: {path}")
    raw = {
        "retrace_mapping": 1,
        "run_discovery": {"pattern": path.name, "unit": "json"},
        "run": {"id": "instance_id", "metadata": {"correct": "other_data.correct"}},
        "event": {"sources": [{"name": "trace", "path": source, "fields": fields}]},
    }
    with SqliteStore(":memory:") as store:
        ingest(validate_mapping_config(raw), path, store)
        runs = store.list_runs()
        events, _ = store.get_events(runs[0].id, limit=count)
        assert len(runs) == 1 and len(events) == count
        if name.startswith("ag2"):
            assert all(event.role for event in events)
            assert "correct" in runs[0].metadata
        else:
            assert [event.ordinal for event in events] == list(range(count))
            assert events[0].content


def test_local_ag2_full_tree_duplicate_ids() -> None:
    root = REPO_ROOT / "reference-logs" / "mast" / "MAST" / "traces" / "AG2"
    if not root.exists():
        pytest.skip(f"local AG2 tree missing: {root}")
    raw = {
        "retrace_mapping": 1,
        "run_discovery": {"pattern": "**/*.json", "unit": "json"},
        "run": {"id": "instance_id"},
        "event": {"sources": [{"name": "trace", "path": "trajectory", "fields": {}}]},
    }
    original_ids = {
        str(json.loads(path.read_text(encoding="utf-8-sig"))["instance_id"])
        for path in root.glob("**/*.json")
    }
    with SqliteStore(":memory:") as store:
        report = ingest(validate_mapping_config(raw), root, store)
        runs = store.list_runs()
        assert len(runs) == 7_184
        assert len(original_ids) == 200
        assert sum(run.ingest_warnings for run in runs) == 7_184 - 200
        assert report.line_failures == []
        assert store.get_run(next(iter(original_ids))) is not None


def test_cli_prints_document_failure(tmp_path: Path) -> None:
    _write(tmp_path / "good.json", {"run_id": "good", "entries": []})
    (tmp_path / "bad.json").write_text("{bad", encoding="utf-8")
    config = tmp_path / "mapping.yaml"
    config.write_text(yaml.safe_dump(_raw(), sort_keys=False), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "retrace.cli", "check", str(tmp_path), "--config", str(config)],
        cwd=REPO_ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"{tmp_path / 'bad.json'}:1:" in result.stdout
    assert "1 runs" in result.stdout
