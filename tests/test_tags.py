from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from retrace.core.model import Run
from retrace.core.store import SqliteStore
from retrace.core.tags import TagPathError, TagService, guard_sidecar_path
from retrace.server.app import create_app


def _run(run_id: str, source: Path) -> Run:
    return Run(
        id=run_id, experiment_id="experiment", source_path=source.resolve().as_posix(),
        metadata={}, outcome=None, started_at=None, ended_at=None, duration_s=None,
        n_events=0, n_turns=0, agent_ids=[], phases=[], tokens_in=None,
        tokens_out=None, total_cost=None, ingest_warnings=0, n_repaired=0,
    )


def _store(tmp_path: Path, unit: str, sources: dict[str, Path]) -> SqliteStore:
    store = SqliteStore(tmp_path / "cache.db")
    store.meta_set("root_path", tmp_path.resolve().as_posix())
    store.meta_set("discovery_unit", unit)
    for run_id, source in sources.items():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("source\n", encoding="utf-8")
        store.insert_run(_run(run_id, source), [])
    return store


def _tag(mode: str = "1.1", event_ids: list[str] | None = None) -> dict[str, object]:
    return {"mode": mode, "event_ids": event_ids or [], "note": "note"}


@pytest.mark.parametrize(
    ("unit", "source_name", "sidecar_name"),
    [("file", "run.jsonl", "run.retrace.json"), ("dir", "run/events.jsonl", "run/retrace.json")],
)
def test_single_run_layout_round_trip(
    tmp_path: Path, unit: str, source_name: str, sidecar_name: str
) -> None:
    source = tmp_path / source_name
    with _store(tmp_path, unit, {"run-a": source}) as store:
        before = (source.read_bytes(), source.stat().st_mtime_ns)
        result = TagService(store).put("run-a", [_tag()], "run note")
        assert result["tags"][0]["mode"] == "1.1"
        assert result["tags"][0]["created_at"].endswith("Z")
        assert result["run_note"] == "run note"
        assert (tmp_path / sidecar_name).is_file()
        assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_shared_sidecar_merges_runs(tmp_path: Path) -> None:
    source = tmp_path / "aggregate.jsonl"
    with _store(tmp_path, "line", {"a": source, "b": source}) as store:
        service = TagService(store)
        service.put("a", [_tag("1.1")])
        service.put("b", [_tag("2.5")])
        before_b = json.loads((tmp_path / "aggregate.retrace.json").read_text())["runs"]["b"]
        service.put("a", [_tag("3.1")])
        document = json.loads((tmp_path / "aggregate.retrace.json").read_text())
        assert set(document["runs"]) == {"a", "b"}
        assert document["runs"]["b"] == before_b


def test_atomic_failure_preserves_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "run.jsonl"
    with _store(tmp_path, "file", {"a": source}) as store:
        service = TagService(store)
        service.put("a", [_tag("1.1")])
        sidecar = tmp_path / "run.retrace.json"
        original = sidecar.read_bytes()
        monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("crash")))
        with pytest.raises(OSError, match="crash"):
            service.put("a", [_tag("2.5")])
        assert sidecar.read_bytes() == original
        assert json.loads(original)["run_id"] == "a"
        temps = list(tmp_path.glob("*.tmp-*"))
        assert temps and all(not item.name.endswith(".retrace.json") for item in temps)


def test_guard_rejects_escape_and_symlink(tmp_path: Path) -> None:
    with pytest.raises(TagPathError):
        guard_sidecar_path(tmp_path.parent / "escape.retrace.json", tmp_path)
    with pytest.raises(TagPathError):
        guard_sidecar_path(tmp_path / "../../escape.retrace.json", tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    with pytest.raises(TagPathError):
        guard_sidecar_path(link / "retrace.json", tmp_path)


def test_cache_corruption_detached_anchor_and_api_validation(tmp_path: Path) -> None:
    source = tmp_path / "run.jsonl"
    db = tmp_path / "cache.db"
    store = _store(tmp_path, "file", {"a": source})
    service = TagService(store)
    service.put("a", [_tag(event_ids=["a:99"])])
    assert service.get("a")["tags"][0]["detached_event_ids"] == ["a:99"]
    sidecar = tmp_path / "run.retrace.json"
    sidecar.write_text("broken", encoding="utf-8")
    os.utime(sidecar, ns=(sidecar.stat().st_atime_ns, sidecar.stat().st_mtime_ns + 1_000_000))
    result = service.get("a")
    assert result["tags"] == [] and "run.retrace.json" in result["warning"]
    store.close()

    async def request() -> httpx.Response:
        app = create_app(db)
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.put("/api/runs/a/tags", json={"tags": [_tag("9.9")]})

    assert asyncio.run(request()).status_code == 422
