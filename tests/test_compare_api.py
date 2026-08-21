from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from retrace.adapters.registry import resolve_config
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore
from retrace.server.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures"
AVALON_A = "1776453329940-bvvf9"
AVALON_B = "1776470168031-kwy8o"


def _database(tmp_path: Path, *fixtures: str) -> Path:
    path = tmp_path / "compare.db"
    with SqliteStore(path) as store:
        for fixture in fixtures:
            root = FIXTURES / fixture
            config, _ = resolve_config(root)
            ingest(config, root, store)
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


def test_avalon_contract_pagination_and_same_run(tmp_path: Path) -> None:
    path = _database(tmp_path, "avalon_mini")

    async def check(client: httpx.AsyncClient) -> None:
        full = (await client.get(
            "/api/compare", params={"a": AVALON_A, "b": AVALON_B, "limit": 2000}
        )).json()
        assert sum(full["counts"].values()) == full["total"]
        stitched = []
        for offset in range(0, full["total"] + 3, 7):
            page = (await client.get("/api/compare", params={
                "a": AVALON_A, "b": AVALON_B, "offset": offset, "limit": 7,
            })).json()
            assert page["total"] == full["total"]
            stitched.extend(page["pairs"])
        assert stitched == full["pairs"]
        for side in ("a", "b"):
            indices = [pair[f"index_{side}"] for pair in stitched if pair[f"index_{side}"] is not None]
            assert indices == list(range(len(indices)))
        for field, status in (
            ("first_structural_divergence", {"only-a", "only-b"}),
            ("first_content_divergence", {"content-diff"}),
        ):
            index = full[field]
            if index is not None:
                assert full["pairs"][index]["status"] in status

        same = (await client.get(
            "/api/compare", params={"a": AVALON_A, "b": AVALON_A}
        )).json()
        assert same["counts"] == {
            "matches": same["total"], "content_diffs": 0, "only_a": 0, "only_b": 0,
        }
        assert same["first_divergence"] is None
        assert same["first_structural_divergence"] is None
        assert same["first_content_divergence"] is None

    asyncio.run(_with_client(path, check))


def test_support_content_alignment_and_validation(tmp_path: Path) -> None:
    path = _database(tmp_path, "support_pipeline")

    async def check(client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/api/compare", params={"a": "case-01", "b": "case-02"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"]["content_diffs"] > 0
        assert payload["first_content_divergence"] is not None
        structural = payload["first_structural_divergence"]
        content = payload["first_content_divergence"]
        expected = (
            {"index": structural, "kind": "structural"}
            if structural is not None
            else {"index": content, "kind": "content"}
        )
        assert payload["first_divergence"] == expected
        assert (await client.get(
            "/api/compare", params={"a": "missing", "b": "case-02"}
        )).status_code == 404
        invalid = await client.get(
            "/api/compare", params={"a": "case-01", "b": "case-02", "comparator": "fuzzy"}
        )
        assert invalid.status_code == 422
        assert "exact" in invalid.json()["detail"] and "normalized" in invalid.json()["detail"]
        assert (await client.get(
            "/api/compare", params={"a": "case-01", "b": "case-02", "limit": -1}
        )).status_code == 422

    asyncio.run(_with_client(path, check))


def test_pagination_boundaries_and_openapi(tmp_path: Path) -> None:
    path = _database(tmp_path, "support_pipeline")

    async def check(client: httpx.AsyncClient) -> None:
        clamped = (await client.get(
            "/api/compare", params={"a": "case-01", "b": "case-02", "limit": 9999}
        )).json()
        assert clamped["limit"] == 2000
        beyond = (await client.get("/api/compare", params={
            "a": "case-01", "b": "case-02", "offset": clamped["total"] + 1,
        })).json()
        assert beyond["pairs"] == [] and beyond["total"] == clamped["total"]
        schema = (await client.get("/openapi.json")).json()
        response = schema["paths"]["/api/compare"]["get"]["responses"]["200"]
        assert response["content"]["application/json"]["schema"]["$ref"].endswith(
            "/CompareResponse"
        )
        properties = schema["components"]["schemas"]["CompareResponse"]["properties"]
        assert {"run_a", "run_b", "counts", "first_divergence", "pairs", "total"} <= set(properties)
        assert "capped at 2000" in properties["limit"]["description"]

    asyncio.run(_with_client(path, check))


def test_cross_format_compare_smoke(tmp_path: Path) -> None:
    path = _database(tmp_path, "avalon_mini", "support_pipeline")

    async def check(client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/api/compare", params={"a": AVALON_A, "b": "case-01", "limit": 2000}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"]["matches"] <= 1
        assert payload["counts"]["content_diffs"] == 0
        assert payload["counts"]["only_a"] + payload["counts"]["only_b"] > 0

    asyncio.run(_with_client(path, check))
