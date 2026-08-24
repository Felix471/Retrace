from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from retrace.adapters.registry import resolve_config
from retrace.core.ingest import ingest
from retrace.core.model import Run
from retrace.core.store import SqliteStore
from retrace.server.app import RUN_SORT_FIELDS, create_app

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _database(tmp_path: Path, fixture: str) -> Path:
    root = FIXTURES / fixture
    path = tmp_path / f"{fixture}.db"
    config, _ = resolve_config(root)
    with SqliteStore(path) as store:
        ingest(config, root, store)
    return path


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


def _typed_database(tmp_path: Path) -> Path:
    path = tmp_path / "typed.db"
    with SqliteStore(path) as store:
        for run_id, metadata in (
            ("false-zero", {"flag": False, "level": 0, "kind": "plain"}),
            ("true-one", {"flag": True, "level": 1, "kind": "plain"}),
            ("true-zero", {"flag": True, "level": 0, "kind": "other"}),
        ):
            store.insert_run(
                Run(
                    id=run_id, experiment_id="typed", source_path=f"{run_id}.json",
                    metadata=metadata, outcome="ok", started_at=None, ended_at=None,
                    duration_s=None, n_events=0, n_turns=0, agent_ids=[], phases=[],
                    tokens_in=None, tokens_out=None, total_cost=None,
                    ingest_warnings=0, n_repaired=0,
                ),
                [],
            )
    return path


def test_typed_metadata_grouping_and_filtering(tmp_path: Path) -> None:
    path = _typed_database(tmp_path)

    async def check(client: httpx.AsyncClient) -> None:
        grouped = (await client.get("/api/runs", params={"group_by": "flag"})).json()
        assert {group["group_value"] for group in grouped["groups"]} == {False, True}
        for group in grouped["groups"]:
            matching = [
                row for row in grouped["rows"]
                if str(row["metadata"]["flag"]).lower()
                == str(group["group_value"]).lower()
            ]
            assert len(matching) == group["run_count"]

        for raw, expected in (("false", {"false-zero"}), ("0", {"false-zero"}),
                              ("true", {"true-one", "true-zero"}),
                              ("1", {"true-one", "true-zero"})):
            response = (await client.get("/api/runs", params={"flag": raw})).json()
            assert {row["id"] for row in response["rows"]} == expected

        levels = (await client.get("/api/runs", params={"level": "1"})).json()
        assert {row["id"] for row in levels["rows"]} == {"true-one"}
        level_groups = (
            await client.get("/api/runs", params={"group_by": "level"})
        ).json()["groups"]
        assert {group["group_value"] for group in level_groups} == {0, 1}
        strings = (await client.get("/api/runs", params={"kind": "plain"})).json()
        assert {row["id"] for row in strings["rows"]} == {"false-zero", "true-one"}

    _check(path, check)


@pytest.mark.parametrize("fixture", ["avalon_mini", "support_pipeline"])
def test_discovered_metadata_filters_and_columns(tmp_path: Path, fixture: str) -> None:
    path = _database(tmp_path, fixture)
    with SqliteStore(path) as store:
        stored = store.list_runs()
        first = stored[0]
        keys = list(first.metadata)
        first_key = keys[0]
        second_key = keys[1]
        first_value = first.metadata[first_key]
        alternate = next(
            run.metadata[first_key]
            for run in stored
            if run.metadata[first_key] != first_value
        )

    async def check(client: httpx.AsyncClient) -> None:
        single = (await client.get("/api/runs", params={first_key: first_value})).json()
        assert single["total"] > 0
        assert all(row["metadata"][first_key] == first_value for row in single["rows"])

        multiple = (
            await client.get(
                "/api/runs", params=[(first_key, first_value), (first_key, alternate)]
            )
        ).json()
        assert {row["metadata"][first_key] for row in multiple["rows"]} == {
            first_value,
            alternate,
        }

        second_value = first.metadata[second_key]
        both = (
            await client.get(
                "/api/runs", params={first_key: first_value, second_key: second_value}
            )
        ).json()
        assert all(
            row["metadata"][first_key] == first_value
            and row["metadata"][second_key] == second_value
            for row in both["rows"]
        )
        outcome = (await client.get("/api/runs", params={"outcome": first.outcome})).json()
        assert all(row["outcome"] == first.outcome for row in outcome["rows"])
        empty = (await client.get("/api/runs", params={first_key: "missing"})).json()
        assert empty["rows"] == []
        assert empty["total"] == 0
        assert set(single["rows"][0]) == {
            "id", "outcome", "n_events", "n_turns", "ingest_warnings",
            "n_repaired", "duration_s", "metadata", "total_cost",
        }

    _check(path, check)


@pytest.mark.parametrize("field", RUN_SORT_FIELDS)
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_sort_matrix_and_determinism(tmp_path: Path, field: str, order: str) -> None:
    fixture = "avalon_mini" if field != "total_cost" else "support_pipeline"
    path = _database(tmp_path, fixture)

    async def check(client: httpx.AsyncClient) -> None:
        rows = (await client.get("/api/runs", params={"sort": field, "order": order})).json()[
            "rows"
        ]
        present = [row for row in rows if row[field] is not None]
        missing = [row for row in rows if row[field] is None]
        values = [row[field] for row in present]
        assert values == sorted(values, reverse=order == "desc")
        assert rows == present + missing
        for value in set(values):
            tied = [row["id"] for row in present if row[field] == value]
            assert tied == sorted(tied)

    _check(path, check)


def test_none_last_validation_and_pagination(tmp_path: Path) -> None:
    path = _database(tmp_path, "avalon_mini")

    async def check(client: httpx.AsyncClient) -> None:
        for order in ("asc", "desc"):
            rows = (
                await client.get("/api/runs", params={"sort": "total_cost", "order": order})
            ).json()["rows"]
            assert [row["id"] for row in rows] == sorted(row["id"] for row in rows)

        invalid = await client.get("/api/runs", params={"sort": "unknown"})
        assert invalid.status_code == 422
        assert all(field in invalid.text for field in RUN_SORT_FIELDS)
        assert (await client.get("/api/runs", params={"order": "sideways"})).status_code == 422

        full = (await client.get("/api/runs", params={"sort": "duration_s"})).json()
        first = (await client.get("/api/runs", params={"limit": 2, "sort": "duration_s"})).json()
        second = (
            await client.get(
                "/api/runs", params={"limit": 3, "offset": 2, "sort": "duration_s"}
            )
        ).json()
        assert first["rows"] + second["rows"] == full["rows"]
        assert first["total"] == second["total"] == full["total"] == 5
        clamped = (await client.get("/api/runs", params={"limit": 9999})).json()
        assert clamped["limit"] == 1000

    _check(path, check)


def test_groups_share_filters_and_do_not_paginate(tmp_path: Path) -> None:
    path = _database(tmp_path, "support_pipeline")

    async def check(client: httpx.AsyncClient) -> None:
        grouped = (
            await client.get("/api/runs", params={"group_by": "model_name", "limit": 1})
        ).json()
        assert len(grouped["rows"]) == 1
        assert [group["run_count"] for group in grouped["groups"]] == [5, 5]
        assert [group["outcome_distribution"] for group in grouped["groups"]] == [
            {"resolved": 5},
            {"escalated": 3, "resolved": 2},
        ]

        absent = (await client.get("/api/runs", params={"group_by": "absent"})).json()
        assert absent["total"] == 10
        assert [(group["group_value"], group["run_count"]) for group in absent["groups"]] == [
            (None, 10)
        ]
        filtered = (
            await client.get(
                "/api/runs", params={"group_by": "model_name", "outcome": "escalated"}
            )
        ).json()
        assert filtered["total"] == 3
        assert sum(group["run_count"] for group in filtered["groups"]) == 3

    _check(path, check)


def test_hostile_filters_are_data_and_store_survives(tmp_path: Path) -> None:
    path = _database(tmp_path, "support_pipeline")
    hostile_key = 'x"].value; DROP TABLE runs; --'
    hostile_value = "' OR 1=1; $.metadata[*] --"

    async def check(client: httpx.AsyncClient) -> None:
        url = f"/api/runs?{quote(hostile_key)}={quote(hostile_value)}"
        hostile = await client.get(url)
        assert hostile.status_code == 200
        assert hostile.json()["total"] == 0
        normal = await client.get("/api/runs", params={"model_name": "support-lite-v1"})
        assert normal.status_code == 200
        assert normal.json()["total"] == 5

    _check(path, check)
