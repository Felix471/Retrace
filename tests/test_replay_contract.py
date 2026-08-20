from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest

from retrace.adapters.registry import resolve_config
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore
from retrace.server.app import create_app

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
REPAIRED_RUN = "1776470168031-kwy8o"
LONG_CONTENT_RUN = "1776471689022-f5veq"


def _database(tmp_path: Path, fixture: str) -> Path:
    fixture_root = FIXTURES / fixture
    path = tmp_path / f"{fixture}.db"
    config, _ = resolve_config(fixture_root)
    with SqliteStore(path) as store:
        ingest(config, fixture_root, store)
    return path


async def _with_client(
    path: Path, check: Callable[[httpx.AsyncClient], Awaitable[None]]
) -> None:
    app = create_app(path)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        await check(client)


@pytest.mark.parametrize(("fixture", "count"), [("avalon_mini", 5), ("support_pipeline", 10)])
def test_flat_run_list_contract_and_order(tmp_path: Path, fixture: str, count: int) -> None:
    path = _database(tmp_path, fixture)
    with SqliteStore(path) as store:
        expected_ids = [run.id for run in store.list_runs()]

    async def check(client: httpx.AsyncClient) -> None:
        response = await client.get("/api/runs")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == count
        assert [run["id"] for run in payload["rows"]] == expected_ids
        required = {
            "id", "outcome", "n_events", "n_turns", "ingest_warnings",
            "n_repaired", "duration_s",
        }
        required |= {"metadata", "total_cost"}
        assert all(set(run) == required for run in payload["rows"])

    asyncio.run(_with_client(path, check))


@pytest.mark.parametrize("fixture", ["avalon_mini", "support_pipeline"])
def test_run_and_event_replay_contract(tmp_path: Path, fixture: str) -> None:
    path = _database(tmp_path, fixture)

    async def check(client: httpx.AsyncClient) -> None:
        runs = (await client.get("/api/runs")).json()["rows"]
        for item in runs:
            run = (await client.get(f"/api/runs/{item['id']}")).json()
            assert {"agent_ids", "ingest_warnings", "n_repaired"} <= set(run)
            page = (await client.get(f"/api/runs/{item['id']}/events", params={"limit": 500})).json()
            event_fields = {"content", "structured", "metadata", "type", "badge", "repaired", "turn", "agent_id", "role"}
            assert all(event_fields <= set(event) for event in page["events"])
            assert all(event["turn"] is not None for event in page["events"])
            if fixture == "support_pipeline":
                assert run["n_repaired"] == 0

    asyncio.run(_with_client(path, check))


def test_repair_provenance_and_long_content_survive_api(tmp_path: Path) -> None:
    path = _database(tmp_path, "avalon_mini")

    async def check(client: httpx.AsyncClient) -> None:
        repaired_run = (await client.get(f"/api/runs/{REPAIRED_RUN}")).json()
        assert repaired_run["n_repaired"] == 1
        events = (await client.get(f"/api/runs/{REPAIRED_RUN}/events", params={"limit": 500})).json()["events"]
        repaired = [event for event in events if event["metadata"].get("_retrace", {}).get("repaired")]
        assert len(repaired) == 1
        assert repaired[0]["metadata"]["_retrace"]["repaired"] == {"turn": 4, "result": "fail"}
        assert repaired[0]["repaired"] == [
            {"field": "turn", "original": 4},
            {"field": "result", "original": "fail"},
        ]

        long_events = (await client.get(f"/api/runs/{LONG_CONTENT_RUN}/events", params={"limit": 500})).json()["events"]
        matching = [event["content"] for event in long_events if len(event["content"]) == 500]
        assert len(matching) == 1

    asyncio.run(_with_client(path, check))


def test_advertised_replay_filters_have_events(tmp_path: Path) -> None:
    path = _database(tmp_path, "avalon_mini")

    async def check(client: httpx.AsyncClient) -> None:
        run_id = (await client.get("/api/runs")).json()["rows"][0]["id"]
        run = (await client.get(f"/api/runs/{run_id}")).json()
        for field, values in (("agent", run["agents"]), ("phase", run["phases"]), ("type", run["types"])):
            for value in values:
                page = (await client.get(f"/api/runs/{run_id}/events", params={field: value, "limit": 1})).json()
                assert page["total"] >= 1
                assert len(page["events"]) == 1

    asyncio.run(_with_client(path, check))


def test_ui_sources_are_format_neutral() -> None:
    forbidden = (
        "speakerId", "gameId", "proposedBy", "quests", "discussions", "winner",
        "handler", "ticket_id", "step_kind", "workflow_stage", "occurred_at",
    )
    ui_root = ROOT / "src" / "retrace" / "ui"
    for path in ui_root.rglob("*"):
        if path.suffix in {".js", ".html"}:
            source = path.read_text(encoding="utf-8")
            assert not any(value in source for value in forbidden), path
