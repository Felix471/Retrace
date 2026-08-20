from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from retrace.adapters.discovery import DiscoveryError, discover_runs
from retrace.adapters.mapping_schema import MappingConfigError, validate_mapping_config

PROJECT_ROOT = Path(__file__).parents[1]


def _config(
    pattern: str,
    *,
    unit: str = "file",
    run_id: str = "{file_stem}",
    events_file: str | None = None,
    manifest: str | None = None,
):
    discovery: dict[str, object] = {"pattern": pattern, "unit": unit}
    if events_file is not None:
        discovery["events_file"] = events_file
    run: dict[str, object] = {"id": run_id}
    if manifest is not None:
        run["manifest"] = manifest
    return validate_mapping_config(
        {
            "retrace_mapping": 1,
            "run_discovery": discovery,
            "run": run,
            "event": {},
        }
    )


def test_discovers_directory_fixture() -> None:
    root = PROJECT_ROOT / "fixtures" / "support_pipeline"
    config = _config(
        "*/", unit="dir", run_id="{dir_name}", events_file="events.jsonl", manifest="meta.json"
    )

    runs = discover_runs(config, root)

    assert [run.run_id for run in runs] == [f"case-{number:02}" for number in range(1, 11)]
    assert all(run.events_path.exists() and run.warnings == () for run in runs)
    assert all(run.manifest is not None for run in runs)
    assert [run.manifest["run_id"] for run in runs if run.manifest] == [
        run.run_id for run in runs
    ]


def test_discovers_nested_files_deterministically(tmp_path: Path) -> None:
    for relative in ("z/third.jsonl", "a/second.jsonl", "a/first.jsonl"):
        path = tmp_path / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text("", encoding="utf-8")

    runs = discover_runs(_config("**/*.jsonl"), tmp_path)
    by_directory = discover_runs(_config("**/*.jsonl", run_id="{dir_name}"), tmp_path)

    assert [run.root.relative_to(tmp_path).as_posix() for run in runs] == [
        "a/first.jsonl",
        "a/second.jsonl",
        "z/third.jsonl",
    ]
    assert [run.run_id for run in runs] == ["first", "second", "third"]
    assert [run.run_id for run in by_directory] == ["a", "a~2", "z"]


@pytest.mark.parametrize("count", [2, 3])
def test_duplicate_ids_receive_stable_suffixes(tmp_path: Path, count: int) -> None:
    for index in range(count):
        directory = tmp_path / str(index)
        directory.mkdir()
        (directory / "same.jsonl").write_text("", encoding="utf-8")

    runs = discover_runs(_config("*/*.jsonl"), tmp_path)

    assert [run.run_id for run in runs] == ["same", *[f"same~{n}" for n in range(2, count + 1)]]
    assert runs[0].warnings == ()
    assert all(len(run.warnings) == 1 for run in runs[1:])
    assert all(str(runs[0].root) in run.warnings[0] for run in runs[1:])


def test_duplicate_suffix_does_not_collide_with_natural_id(tmp_path: Path) -> None:
    for relative in ("a/x.jsonl", "b/x.jsonl", "c/x~2.jsonl"):
        path = tmp_path / relative
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
    runs = discover_runs(_config("*/*.jsonl"), tmp_path)
    assert [run.run_id for run in runs] == ["x", "x~3", "x~2"]


@pytest.mark.parametrize("kind", ["empty", "missing", "no-match"])
def test_no_matches_is_clear(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "missing" if kind == "missing" else tmp_path
    pattern = "*.txt" if kind == "no-match" else "*.jsonl"
    if kind == "no-match":
        (tmp_path / "other.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(DiscoveryError) as captured:
        discover_runs(_config(pattern), root)
    assert str(root) in str(captured.value)
    assert pattern in str(captured.value)
    assert "\n" not in str(captured.value)


def test_directory_diagnostics_and_manifest_failures(tmp_path: Path) -> None:
    missing_events = tmp_path / "a"
    missing_manifest = tmp_path / "b"
    invalid_manifest = tmp_path / "c"
    for directory in (missing_events, missing_manifest, invalid_manifest):
        directory.mkdir()
    for directory in (missing_manifest, invalid_manifest):
        (directory / "events.jsonl").write_text("", encoding="utf-8")
    (invalid_manifest / "meta.json").write_text("not json", encoding="utf-8")

    with pytest.warns(UserWarning, match="expected file"):
        runs = discover_runs(
            _config(
                "*", unit="dir", events_file="events.jsonl", manifest="meta.json", run_id="{dir_name}"
            ),
            tmp_path,
        )

    assert [run.run_id for run in runs] == ["b", "c"]
    assert all(run.manifest is None and len(run.warnings) == 1 for run in runs)
    assert "missing" in runs[0].warnings[0]
    assert "invalid" in runs[1].warnings[0]


@pytest.mark.parametrize("template, unknown", [("{other}", "other"), ("{file_stem.x}", "file_stem.x")])
def test_unknown_template_variable_is_rejected(tmp_path: Path, template: str, unknown: str) -> None:
    (tmp_path / "run.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(MappingConfigError) as captured:
        discover_runs(_config("*.jsonl", run_id=template), tmp_path)
    assert str(captured.value) == (
        f"run.id: unknown template variable {unknown!r}; valid variables: file_stem, dir_name"
    )


@pytest.mark.parametrize("pattern", ["/tmp/*.jsonl", "C:\\temp\\*.jsonl", "../*.jsonl", "a/../*.jsonl"])
def test_unsafe_pattern_is_rejected(tmp_path: Path, pattern: str) -> None:
    with pytest.raises(MappingConfigError) as captured:
        discover_runs(_config(pattern), tmp_path)
    assert "run_discovery.pattern:" in str(captured.value)
    assert "\n" not in str(captured.value)


def test_line_unit_discovers_json_objects(tmp_path: Path) -> None:
    (tmp_path / "runs.jsonl").write_text('{"id":"one"}\n', encoding="utf-8")
    runs = discover_runs(_config("*.jsonl", unit="line", run_id="id"), tmp_path)
    assert [(run.run_id, run.line_no) for run in runs] == [("one", 1)]


def test_discovery_is_read_only(tmp_path: Path) -> None:
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "events.jsonl").write_text("event\n", encoding="utf-8")
    (directory / "meta.json").write_text(json.dumps({"value": 1}), encoding="utf-8")

    def hashes() -> dict[str, str]:
        return {
            path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in tmp_path.rglob("*")
            if path.is_file()
        }

    before = hashes()
    discover_runs(
        _config("*", unit="dir", events_file="events.jsonl", manifest="meta.json"), tmp_path
    )
    assert hashes() == before
