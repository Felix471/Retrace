from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_core_purity import check, main

REPO_ROOT = Path(__file__).resolve().parents[1]
# This marker makes the checker recognize this module as a fixture-loading test.
FIXTURE_MARKER = "avalon_mini"


def _write_tree(root: Path, field_names: list[str] | None = None) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "fixtures").mkdir()
    config = json.loads((REPO_ROOT / "scripts" / "purity_config.json").read_text(encoding="utf-8"))
    (root / "scripts" / "purity_config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    (root / "fixtures" / "field_names.json").write_text(
        json.dumps({"sample.records": field_names or ["fixtureField"]}), encoding="utf-8"
    )


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_violation_reports_path_line_and_boundary_cases(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    _write(tmp_path, "src/pkg/core.py", "safe\nquestRound quests Avalon\nrequest Request\n")

    violations = check(tmp_path)

    assert [(item.path, item.line, item.token) for item in violations] == [
        ("src/pkg/core.py", 2, "Avalon"),
        ("src/pkg/core.py", 2, "quest"),
        ("src/pkg/core.py", 2, "quest"),
    ]


def test_exact_and_fixture_tree_allowlists_pass(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    _write(tmp_path, "src/retrace/adapters/builtin/avalon.yaml", "merlin: quest\n")
    _write(tmp_path, "fixtures/avalon_mini/sample.txt", "morgana assassin\n")

    assert check(tmp_path) == []


def test_test_marker_is_required_for_domain_literals(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    _write(tmp_path, "tests/test_loader.py", "sample = 'builtin:avalon'\nrole = 'mordred'\n")
    assert check(tmp_path) == []

    _write(tmp_path, "tests/test_loader.py", "role = 'mordred'\n")
    violations = check(tmp_path)
    assert [(item.path, item.line, item.token) for item in violations] == [
        ("tests/test_loader.py", 1, "mordred")
    ]


def test_pull_request_is_not_a_domain_match(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    _write(tmp_path, ".github/workflows/ci.yml", "pull_request:\n")

    assert check(tmp_path) == []


def test_ui_field_name_hit_is_reported(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    _write(tmp_path, "src/retrace/ui/app.js", "const value = row.fixtureField;\n")

    violations = check(tmp_path)
    assert [(item.path, item.line, item.token, item.rule) for item in violations] == [
        ("src/retrace/ui/app.js", 1, "fixtureField", "fixture field name in UI")
    ]


def test_core_model_field_names_are_allowed_in_ui(tmp_path: Path) -> None:
    _write_tree(tmp_path, ["outcome", "tokens_in"])
    _write(tmp_path, "src/retrace/ui/app.js", "row.outcome + row.tokens_in;\n")

    assert check(tmp_path) == []


def test_platform_field_name_is_allowed_in_ui(tmp_path: Path) -> None:
    _write_tree(tmp_path, ["body"])
    _write(tmp_path, "src/retrace/ui/app.js", "fetch(url, { body: payload });\n")

    assert check(tmp_path) == []


@pytest.mark.parametrize("field_name", ["speakerId", "handler", "step_kind"])
def test_fixture_specific_field_names_are_rejected_in_ui(
    tmp_path: Path, field_name: str
) -> None:
    _write_tree(tmp_path, [field_name])
    _write(tmp_path, "src/retrace/ui/app.js", f"row.{field_name};\n")

    violations = check(tmp_path)
    assert [(item.token, item.rule) for item in violations] == [
        (field_name, "fixture field name in UI")
    ]


@pytest.mark.parametrize("contents", [None, "not json", "[]"])
def test_missing_or_invalid_config_has_clear_error(
    tmp_path: Path, contents: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.json"
    if contents is not None:
        config.write_text(contents, encoding="utf-8")

    assert main([str(tmp_path), str(config)]) == 2
    assert "core purity: ERROR: config error:" in capsys.readouterr().out


def test_seeded_violation_makes_gate_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_tree(tmp_path)
    seed = _write(tmp_path, "src/retrace/_purity_seed.py", "role = 'oberon'\n")
    try:
        assert main([str(tmp_path)]) == 1
        assert "src/retrace/_purity_seed.py:1: oberon (banned identifier)" in capsys.readouterr().out
    finally:
        seed.unlink()


def test_real_tree_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(REPO_ROOT)]) == 0
    assert capsys.readouterr().out == "core purity: OK\n"


def test_field_manifest_matches_committed_fixture_keys() -> None:
    manifest = json.loads(
        (REPO_ROOT / "fixtures" / "field_names.json").read_text(encoding="utf-8")
    )
    aggregate = json.loads(
        (REPO_ROOT / "fixtures" / "avalon_mini" / "games.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    event_keys: set[str] = set()
    manifest_keys: set[str] = set()
    for case in sorted((REPO_ROOT / "fixtures" / "support_pipeline").glob("case-*")):
        event_keys.update(json.loads((case / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]))
        manifest_keys.update(json.loads((case / "meta.json").read_text(encoding="utf-8")))

    assert manifest == {
        "avalon_mini.records": sorted(aggregate),
        "support_pipeline.manifest": sorted(manifest_keys),
        "support_pipeline.records": sorted(event_keys),
    }
