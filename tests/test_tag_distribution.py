from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from retrace.core.model import Run
from retrace.core.store import SqliteStore
from retrace.core.tags import TagService
from retrace.server.app import create_app


def _run(run_id: str, source: Path, metadata: dict[str, object]) -> Run:
    return Run(
        id=run_id,
        experiment_id="tags",
        source_path=source.resolve().as_posix(),
        metadata=metadata,
        outcome=None,
        started_at=None,
        ended_at=None,
        duration_s=None,
        n_events=0,
        n_turns=0,
        agent_ids=[],
        phases=[],
        tokens_in=None,
        tokens_out=None,
        total_cost=None,
        ingest_warnings=0,
        n_repaired=0,
    )


def _database(tmp_path: Path, tagged: bool = True) -> Path:
    db = tmp_path / "cache.db"
    with SqliteStore(db) as store:
        store.meta_set("root_path", tmp_path.resolve().as_posix())
        store.meta_set("discovery_unit", "file")
        values = [
            ("a", {"winReason": "good", "model_name": "large"}),
            ("b", {"winReason": "evil", "model_name": "large"}),
            ("c", {"model_name": "small"}),
        ]
        for run_id, metadata in values:
            source = tmp_path / f"{run_id}.jsonl"
            source.write_text("source\n", encoding="utf-8")
            store.insert_run(_run(run_id, source, metadata), [])
        if tagged:
            service = TagService(store)
            service.put("a", [{"mode": "2.5"}, {"mode": "2.5"}])
            service.put("b", [{"mode": "2.5"}, {"mode": "3.1"}])
    return db


def _check(db: Path, check: Callable[[httpx.AsyncClient, object], Awaitable[None]]) -> None:
    async def request() -> None:
        app = create_app(db)
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            await check(client, app)

    asyncio.run(request())


def _modes(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    return {mode["id"]: mode for mode in payload["modes"]}  # type: ignore[index]


def test_distribution_math_and_group_split(tmp_path: Path) -> None:
    db = _database(tmp_path)

    async def check(client: httpx.AsyncClient, _app: object) -> None:
        response = await client.get("/api/tags/distribution")
        assert response.status_code == 200
        payload = response.json()
        assert (payload["tagged_runs"], payload["total_tags"], payload["total_runs"]) == (2, 4, 3)
        modes = _modes(payload)
        assert (modes["2.5"]["runs_with_tag"], modes["2.5"]["total_tags"]) == (2, 3)
        assert (modes["3.1"]["runs_with_tag"], modes["3.1"]["total_tags"]) == (1, 1)
        assert len(modes) == 14
        assert all(
            (mode["runs_with_tag"], mode["total_tags"]) == (0, 0)
            for mode_id, mode in modes.items()
            if mode_id not in {"2.5", "3.1"}
        )

        grouped = (await client.get(
            "/api/tags/distribution", params={"group_by": "winReason"}
        )).json()
        groups = {group["group_value"]: group for group in grouped["groups"]}
        assert set(groups) == {"good", "evil", None}
        assert (_modes(groups["good"])["2.5"]["runs_with_tag"], groups["good"]["total_tags"]) == (1, 2)
        assert (_modes(groups["evil"])["3.1"]["runs_with_tag"], groups["evil"]["total_tags"]) == (1, 2)
        assert groups[None]["total_runs"] == 1 and groups[None]["total_tags"] == 0

        by_model = (await client.get(
            "/api/tags/distribution", params={"group_by": "model_name"}
        )).json()
        assert {group["group_value"] for group in by_model["groups"]} == {"large", "small"}

    _check(db, check)


def test_empty_state_contract_and_no_event_scan(tmp_path: Path) -> None:
    db = _database(tmp_path, tagged=False)

    async def check(client: httpx.AsyncClient, app: object) -> None:
        app.state.store.get_events = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[attr-defined]
            AssertionError("distribution must not scan events")
        )
        response = await client.get("/api/tags/distribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_tags"] == 0
        assert payload["tagged_runs"] == 0
        assert payload["total_runs"] == 3
        assert all(mode["runs_with_tag"] == mode["total_tags"] == 0 for mode in payload["modes"])

    _check(db, check)


def test_corrupt_sidecar_is_zero_with_warning(tmp_path: Path) -> None:
    db = _database(tmp_path, tagged=False)
    (tmp_path / "a.retrace.json").write_text("not json", encoding="utf-8")

    async def check(client: httpx.AsyncClient, _app: object) -> None:
        response = await client.get("/api/tags/distribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_tags"] == 0
        assert len(payload["warnings"]) == 1
        assert "a.retrace.json" in payload["warnings"][0]

    _check(db, check)


def test_ui_empty_hint_and_svg_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    app_source = (root / "src/retrace/ui/app.js").read_text(encoding="utf-8")
    assert "No tags yet - open a run and add a failure-mode tag in the replay view" in app_source
    assert "<svg" in app_source and "<title>" in app_source
    assert "/api/tags/distribution" in app_source
