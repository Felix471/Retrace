from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import perf_counter

import httpx
import pytest

from retrace.adapters.registry import resolve_config
from retrace.core.ingest import ingest
from retrace.core.model import Run
from retrace.core.store import SqliteStore
from retrace.server.app import create_app

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
FIXED_FIELDS = {
    "id", "outcome", "n_events", "n_turns", "duration_s", "total_cost",
    "ingest_warnings", "n_repaired", "metadata",
}
GROUP_FIELDS = {
    "group_value", "run_count", "mean_turns", "median_turns", "mean_cost",
    "cost_excluded", "mean_duration", "duration_excluded", "outcome_distribution",
}


def _check(path: Path, check: Callable[[httpx.AsyncClient], Awaitable[None]]) -> None:
    async def run() -> None:
        app = create_app(path)
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            await check(client)

    asyncio.run(run())


def _fixture_database(tmp_path: Path, fixture: str) -> Path:
    root = FIXTURES / fixture
    path = tmp_path / f"{fixture}.db"
    config, _ = resolve_config(root)
    with SqliteStore(path) as store:
        ingest(config, root, store)
    return path


@pytest.mark.parametrize("fixture", ["avalon_mini", "support_pipeline"])
def test_batch_table_contract_on_both_fixtures(tmp_path: Path, fixture: str) -> None:
    path = _fixture_database(tmp_path, fixture)

    async def check(client: httpx.AsyncClient) -> None:
        experiment = (await client.get("/api/experiment")).json()
        page = (await client.get("/api/runs", params={"limit": 200})).json()
        assert experiment["metadata_keys"]
        assert set().union(*(row["metadata"] for row in page["rows"])) == set(experiment["metadata_keys"])
        assert all(set(row) == FIXED_FIELDS for row in page["rows"])

        expensive = (await client.get("/api/runs", params={"sort": "total_cost", "order": "desc", "limit": 200})).json()
        costs = [row["total_cost"] for row in expensive["rows"] if row["total_cost"] is not None]
        assert costs == sorted(costs, reverse=True)

        first = page["rows"][0]
        outcome = (await client.get("/api/runs", params={"outcome": first["outcome"]})).json()
        assert outcome["rows"] and all(row["outcome"] == first["outcome"] for row in outcome["rows"])
        key = next(iter(first["metadata"]))
        value = first["metadata"][key]
        filtered = (await client.get("/api/runs", params={key: value, "outcome": first["outcome"]})).json()
        assert filtered["rows"]
        assert all(row["metadata"].get(key) == value and row["outcome"] == first["outcome"] for row in filtered["rows"])

    _check(path, check)


@pytest.mark.parametrize(
    ("fixture", "group_by", "expected"),
    [
        ("support_pipeline", "model_name", [(5, {"resolved": 5}), (5, {"escalated": 3, "resolved": 2})]),
        ("avalon_mini", "winReason", [(1, {"evil": 1}), (2, {"evil": 2}), (2, {"good": 2})]),
    ],
)
def test_grouped_batch_contract_and_discovered_selector_keys(
    tmp_path: Path, fixture: str, group_by: str, expected: list[tuple[int, dict[str, int]]],
) -> None:
    path = _fixture_database(tmp_path, fixture)

    async def check(client: httpx.AsyncClient) -> None:
        experiment = (await client.get("/api/experiment")).json()
        assert group_by in experiment["metadata_keys"]
        payload = (await client.get("/api/runs", params={"group_by": group_by, "limit": 1})).json()
        assert len(payload["rows"]) == 1
        assert [(group["run_count"], group["outcome_distribution"]) for group in payload["groups"]] == expected
        assert all(set(group) == GROUP_FIELDS for group in payload["groups"])

    _check(path, check)


def test_thousand_run_sorted_pages_meet_batch_latency_envelope(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.db"
    with SqliteStore(path) as store:
        for index in range(1000):
            store.insert_run(Run(
                id=f"run-{index:04d}", experiment_id="synthetic", source_path=f"source-{index:04d}",
                metadata={"cohort": f"c{index % 5}", "ordinal": str(index)},
                outcome="even" if index % 2 == 0 else "odd", started_at=None, ended_at=None,
                duration_s=float(index), n_events=index % 17, n_turns=index % 11,
                agent_ids=[], phases=[], tokens_in=None, tokens_out=None,
                total_cost=index / 1000, ingest_warnings=index % 3, n_repaired=index % 2,
            ), [])

    async def check(client: httpx.AsyncClient) -> None:
        windows = []
        for offset in (0, 200, 800):
            started = perf_counter()
            response = await client.get("/api/runs", params={
                "sort": "total_cost", "order": "desc", "limit": 200, "offset": offset,
            })
            elapsed = perf_counter() - started
            assert response.status_code == 200
            assert elapsed < 0.5
            payload = response.json()
            assert payload["total"] == 1000
            assert payload["offset"] == offset
            assert payload["limit"] == 200
            assert len(payload["rows"]) == 200
            windows.extend(row["id"] for row in payload["rows"])
            expected = [f"run-{index:04d}" for index in range(999 - offset, 799 - offset, -1)]
            assert [row["id"] for row in payload["rows"]] == expected
        assert len(set(windows)) == 600

    _check(path, check)
