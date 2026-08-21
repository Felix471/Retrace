from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from retrace.adapters.mapping_schema import MappingConfig
from retrace.adapters.registry import (
    ConfigResolutionError,
    builtin_names,
    load_builtin,
    resolve_config,
    sniff_config,
)
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures"
AG2_SAMPLE = REPO_ROOT / "reference-logs" / "mast" / "MAST" / "traces" / "AG2"


def _write_config(path: Path, *, sniff: bool = False) -> None:
    raw: dict[str, object] = {
        "retrace_mapping": 1,
        "run_discovery": {"pattern": "*.jsonl", "unit": "line"},
        "run": {"id": "id"},
        "event": {"sources": [{"name": "items", "path": "items"}]},
    }
    if sniff:
        raw["sniff"] = {"required_fields": ["id"]}
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_builtin_loads_as_packaged_resource() -> None:
    config, adapter_ref = load_builtin("avalon")

    assert isinstance(config, MappingConfig)
    assert config.run.id == "gameId"
    assert adapter_ref == "builtin:avalon"


def test_resolution_disambiguates_builtins_in_deterministic_order() -> None:
    assert builtin_names() == ("ag2", "avalon", "support_pipeline")
    assert resolve_config(FIXTURES / "avalon_mini")[1] == "builtin:avalon"
    assert resolve_config(FIXTURES / "support_pipeline")[1] == "builtin:support_pipeline"
    ag2, _ = load_builtin("ag2")
    avalon, _ = load_builtin("avalon")
    support, _ = load_builtin("support_pipeline")
    assert not sniff_config(ag2, FIXTURES / "avalon_mini")
    assert not sniff_config(ag2, FIXTURES / "support_pipeline")
    if AG2_SAMPLE.exists():
        assert resolve_config(AG2_SAMPLE)[1] == "builtin:ag2"
        assert not sniff_config(avalon, AG2_SAMPLE)
        assert not sniff_config(support, AG2_SAMPLE)


@pytest.mark.skipif(not AG2_SAMPLE.exists(), reason="local AG2 sample tree is absent")
def test_local_ag2_tree_resolves_and_checks() -> None:
    config, adapter_ref = resolve_config(AG2_SAMPLE)

    assert adapter_ref == "builtin:ag2"
    with SqliteStore(":memory:") as store:
        report = ingest(config, AG2_SAMPLE, store)
    assert report.line_failures == []


def test_resolution_rejects_unmatched_root(tmp_path: Path) -> None:
    (tmp_path / "unmatched.jsonl").write_text('{"unrelated": true}\n', encoding="utf-8")

    with pytest.raises(ConfigResolutionError, match=r"retrace init"):
        resolve_config(tmp_path)


def test_explicit_and_target_local_configs_precede_sniffing(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.yaml"
    _write_config(explicit)
    config, adapter_ref = resolve_config(FIXTURES / "avalon_mini", explicit)
    assert config.run.id == "id"
    assert adapter_ref == str(explicit)

    target = tmp_path / "target"
    target.mkdir()
    local = target / "retrace.yaml"
    _write_config(local)
    config, adapter_ref = resolve_config(target)
    assert config.run.id == "id"
    assert adapter_ref == str(local)


def test_target_file_uses_adjacent_local_config(tmp_path: Path) -> None:
    target = tmp_path / "aggregate.jsonl"
    target.write_text('{"id":"one","items":[]}\n', encoding="utf-8")
    local = tmp_path / "retrace.yaml"
    _write_config(local)

    assert resolve_config(target)[1] == str(local)


def test_aggregate_sniff_is_streaming(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "aggregate.jsonl"
    first = {"gameId": "one", "players": [], "quests": [], "discussions": []}
    target.write_text(json.dumps(first) + "\n" + "x" * 100_000, encoding="utf-8")
    config, _ = load_builtin("avalon")
    original_open = Path.open

    class FirstLineOnly:
        def __init__(self, stream: object) -> None:
            self.stream = stream
            self.lines = 0

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            self.stream.close()

        def __iter__(self):
            return self

        def __next__(self):
            self.lines += 1
            if self.lines > 1:
                raise AssertionError("sniff read beyond the first parseable line")
            return next(self.stream)

        def read(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("sniff must not slurp the aggregate")

    def guarded_open(path: Path, *args: object, **kwargs: object):
        stream = original_open(path, *args, **kwargs)
        return FirstLineOnly(stream) if path.resolve() == target.resolve() else stream

    monkeypatch.setattr(Path, "open", guarded_open)
    assert sniff_config(config, target)
    monkeypatch.undo()
    assert resolve_config(target)[1] == "builtin:avalon"
