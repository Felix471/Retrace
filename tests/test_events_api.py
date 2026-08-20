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

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures"
AVALON_RUN = "1776471689022-f5veq"
SUPPORT_RUN = "case-01"


def _database(tmp_path: Path, fixture: str) -> Path:
    root = FIXTURES / fixture
    path = tmp_path / f"{fixture}.db"
    config, _ = resolve_config(root)
    with SqliteStore(path) as store:
        ingest(config, root, store)
    return path


async def _with_client(
    path: Path, request: Callable[[httpx.AsyncClient], Awaitable[None]]
) -> None:
    app = create_app(path)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        await request(client)


@pytest.mark.parametrize(
    ("fixture", "run_id"),
    [("avalon_mini", AVALON_RUN), ("support_pipeline", SUPPORT_RUN)],
)
def test_run_payload_has_all_fields_and_distinct_lists(
    tmp_path: Path, fixture: str, run_id: str
) -> None:
    path = _database(tmp_path, fixture)
    with SqliteStore(path) as store:
        run = store.get_run(run_id)
        assert run is not None
        expected = {
            **run.to_dict(),
            "agents": store.distinct_agents(run_id),
            "phases": store.distinct_phases(run_id),
            "types": store.distinct_types(run_id),
        }

    async def check(client: httpx.AsyncClient) -> None:
        response = await client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        assert response.json() == expected
        if fixture == "support_pipeline":
            assert response.json()["outcome"] == "resolved"
            assert response.json()["metadata"] == {
                "issue_area": "duplicate_charge",
                "model_name": "support-lite-v1",
                "routing_variant": "standard",
            }

    asyncio.run(_with_client(path, check))


@pytest.mark.parametrize(
    ("fixture", "run_id"),
    [("avalon_mini", AVALON_RUN), ("support_pipeline", SUPPORT_RUN)],
)
def test_filter_and_pagination_matrix(
    tmp_path: Path, fixture: str, run_id: str
) -> None:
    path = _database(tmp_path, fixture)
    with SqliteStore(path) as store:
        agents = store.distinct_agents(run_id)
        phases = store.distinct_phases(run_id)
        types = store.distinct_types(run_id)
        filters = [
            {},
            {"agent": agents[0]},
            {"phase": phases[0]},
            {"type": types[0]},
            {"agent": agents[0], "phase": phases[0], "type": types[0]},
        ]
        expected = {}
        for index, values in enumerate(filters):
            events, total = store.get_events(
                run_id,
                agent=values.get("agent"),
                phase=values.get("phase"),
                type=values.get("type"),
                limit=2000,
            )
            expected[index] = ([event.to_dict() for event in events], total)

    async def check(client: httpx.AsyncClient) -> None:
        for index, values in enumerate(filters):
            all_events, total = expected[index]
            page_size = 17 if fixture == "avalon_mini" else 5
            stitched: list[dict[str, object]] = []
            for offset in range(0, total, page_size):
                response = await client.get(
                    f"/api/runs/{run_id}/events",
                    params={**values, "offset": offset, "limit": page_size},
                )
                assert response.status_code == 200
                payload = response.json()
                assert payload["total"] == total
                assert payload["offset"] == offset
                assert payload["limit"] == page_size
                stitched.extend(payload["events"])
            assert stitched == all_events
            assert len({event["id"] for event in stitched}) == len(stitched)

            last_offset = max(total - 1, 0)
            last = await client.get(
                f"/api/runs/{run_id}/events",
                params={**values, "offset": last_offset, "limit": page_size},
            )
            assert len(last.json()["events"]) <= 1
            beyond = await client.get(
                f"/api/runs/{run_id}/events",
                params={**values, "offset": total + 10, "limit": page_size},
            )
            assert beyond.json()["events"] == []
            assert beyond.json()["total"] == total

    asyncio.run(_with_client(path, check))


def test_pagination_boundaries(tmp_path: Path) -> None:
    path = _database(tmp_path, "avalon_mini")

    async def check(client: httpx.AsyncClient) -> None:
        clamped = await client.get(
            f"/api/runs/{AVALON_RUN}/events", params={"limit": 5000}
        )
        assert clamped.status_code == 200
        assert clamped.json()["limit"] == 2000
        assert len(clamped.json()["events"]) == 193

        empty = await client.get(
            f"/api/runs/{AVALON_RUN}/events", params={"limit": 0}
        )
        assert empty.status_code == 200
        assert empty.json()["events"] == []
        assert empty.json()["total"] == 193
        assert empty.json()["limit"] == 0

        invalid_offset = await client.get(
            f"/api/runs/{AVALON_RUN}/events", params={"offset": -1}
        )
        invalid_limit = await client.get(
            f"/api/runs/{AVALON_RUN}/events", params={"limit": -1}
        )
        assert invalid_offset.status_code == 422
        assert invalid_limit.status_code == 422

    asyncio.run(_with_client(path, check))


def test_unknown_run_returns_json_404_for_both_endpoints(tmp_path: Path) -> None:
    path = _database(tmp_path, "support_pipeline")

    async def check(client: httpx.AsyncClient) -> None:
        summary = await client.get("/api/runs/missing")
        events = await client.get(
            "/api/runs/missing/events", params={"agent": "triage", "phase": "intake"}
        )
        for response in (summary, events):
            assert response.status_code == 404
            assert response.headers["content-type"].startswith("application/json")
            assert response.json() == {"detail": "Run not found"}

    asyncio.run(_with_client(path, check))


def test_openapi_documents_run_and_event_response_models(tmp_path: Path) -> None:
    path = _database(tmp_path, "support_pipeline")

    async def check(client: httpx.AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        summary_schema = schema["paths"]["/api/runs/{run_id}"]["get"]["responses"]["200"]
        events_schema = schema["paths"]["/api/runs/{run_id}/events"]["get"]["responses"][
            "200"
        ]
        assert summary_schema["content"]["application/json"]["schema"]["$ref"].endswith(
            "/RunResponse"
        )
        assert events_schema["content"]["application/json"]["schema"]["$ref"].endswith(
            "/EventsResponse"
        )
        models = schema["components"]["schemas"]
        assert {"metadata", "started_at", "agents", "types"} <= set(
            models["RunResponse"]["properties"]
        )
        assert {"structured", "timestamp", "refs"} <= set(
            models["EventResponse"]["properties"]
        )
        assert "capped at 2000" in models["EventsResponse"]["properties"]["limit"][
            "description"
        ]

    asyncio.run(_with_client(path, check))
