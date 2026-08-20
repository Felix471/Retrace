from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx
import pytest

from retrace.adapters.registry import resolve_config
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore
from retrace.server.app import create_app

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def _database(tmp_path: Path, fixture: str) -> Path:
    fixture_root = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, fixture_root)
    database = tmp_path / f"{fixture}.db"
    config, _ = resolve_config(fixture_root)
    with SqliteStore(database) as store:
        ingest(config, fixture_root, store)
    return database


async def _client(database: Path) -> tuple[object, httpx.AsyncClient]:
    app = create_app(database)
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    client._retrace_context = context  # type: ignore[attr-defined]
    return app, client


async def _close(client: httpx.AsyncClient) -> None:
    await client.aclose()
    await client._retrace_context.__aexit__(None, None, None)  # type: ignore[attr-defined]


@pytest.mark.parametrize("fixture", ["avalon_mini", "support_pipeline"])
def test_full_tagging_ui_api_flow_and_restart_persistence(tmp_path: Path, fixture: str) -> None:
    database = _database(tmp_path, fixture)

    async def exercise() -> None:
        _app, client = await _client(database)
        try:
            vocabulary_response = await client.get("/api/tags/vocabulary")
            assert vocabulary_response.status_code == 200
            categories = vocabulary_response.json()["categories"]
            assert len(categories) == 3
            assert all(category["modes"] for category in categories)
            assert all(mode["description"] for category in categories for mode in category["modes"])
            mode_ids = [mode["id"] for category in categories for mode in category["modes"]]

            run_id = (await client.get("/api/runs" )).json()["rows"][0]["id"]
            page = (await client.get(f"/api/runs/{run_id}/events", params={"limit": 1})).json()
            event_id = page["events"][0]["id"]
            first_payload = {"tags": [{"mode": mode_ids[0], "note": "anchored", "event_ids": [event_id]}]}
            put_first = await client.put(f"/api/runs/{run_id}/tags", json=first_payload)
            assert put_first.status_code == 200
            first = put_first.json()
            assert first["tags"][0]["detached_event_ids"] == []
            assert (await client.get(f"/api/runs/{run_id}/tags")).json() == first

            bogus_id = f"{run_id}:{page['total'] + 100}"
            replacement = [
                first["tags"][0],
                {"mode": mode_ids[1], "note": "bogus", "event_ids": [bogus_id]},
            ]
            second = (await client.put(f"/api/runs/{run_id}/tags", json={"tags": replacement})).json()
            fetched = (await client.get(f"/api/runs/{run_id}/tags")).json()
            assert fetched == second
            assert fetched["tags"][1]["detached_event_ids"] == [bogus_id]

            deleted = (await client.put(
                f"/api/runs/{run_id}/tags", json={"tags": [second["tags"][1]]}
            )).json()
            assert len(deleted["tags"]) == 1
            assert (await client.get(f"/api/runs/{run_id}/tags")).json() == deleted
        finally:
            await _close(client)

        _fresh_app, fresh_client = await _client(database)
        try:
            reloaded = (await fresh_client.get(f"/api/runs/{run_id}/tags")).json()
            assert reloaded == deleted
        finally:
            await _close(fresh_client)

    asyncio.run(exercise())
