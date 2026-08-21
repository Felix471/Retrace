from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from retrace.adapters.registry import resolve_config
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore
from retrace.server.app import create_app

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
APP_JS = ROOT / "src" / "retrace" / "ui" / "app.js"
EVENT_FIELDS = {
    "id", "run_id", "ordinal", "turn", "timestamp", "agent_id", "role", "type",
    "phase", "content", "structured", "tokens_in", "tokens_out", "cost", "refs",
    "metadata", "badge", "repaired",
}


def _database(tmp_path: Path, fixture: str) -> Path:
    path = tmp_path / f"{fixture}.db"
    root = FIXTURES / fixture
    config, _ = resolve_config(root)
    with SqliteStore(path) as store:
        ingest(config, root, store)
    return path


@pytest.mark.parametrize("fixture", ["avalon_mini", "support_pipeline"])
def test_compare_payload_has_every_ui_field(tmp_path: Path, fixture: str) -> None:
    async def check() -> None:
        app = create_app(_database(tmp_path, fixture))
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            runs = (await client.get("/api/runs", params={"limit": 1000})).json()["rows"]
            assert len(runs) >= 2 and all(isinstance(row["id"], str) for row in runs)
            payload = (await client.get("/api/compare", params={"a": runs[0]["id"], "b": runs[1]["id"], "limit": 500})).json()
            assert {"run_a", "run_b", "counts", "first_divergence", "first_structural_divergence", "first_content_divergence", "pairs", "total", "offset", "limit"} <= payload.keys()
            for run in (payload["run_a"], payload["run_b"]):
                assert {"id", "outcome", "n_events", "ingest_warnings", "agent_ids"} <= run.keys()
            assert {"matches", "content_diffs", "only_a", "only_b"} == payload["counts"].keys()
            for pair in payload["pairs"]:
                assert pair["status"] in {"match", "content-diff", "only-a", "only-b"}
                assert {"status", "index_a", "index_b", "event_a", "event_b"} == pair.keys()
                for event in (pair["event_a"], pair["event_b"]):
                    if event is not None:
                        assert EVENT_FIELDS == event.keys()

    asyncio.run(check())


def test_content_divergence_window_is_jumpable(tmp_path: Path) -> None:
    async def check() -> None:
        app = create_app(_database(tmp_path, "support_pipeline"))
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = (await client.get("/api/compare", params={"a": "case-01", "b": "case-02", "limit": 1})).json()
            index = first["first_content_divergence"]
            assert index is not None
            page = (await client.get("/api/compare", params={"a": "case-01", "b": "case-02", "offset": index // 500 * 500, "limit": 500})).json()
            pair = page["pairs"][index - page["offset"]]
            assert pair["status"] == "content-diff"
            assert pair["index_a"] is not None and pair["index_b"] is not None

    asyncio.run(check())


def test_event_row_is_one_shared_component() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert source.count("class EventRow extends Component") == 1
    assert "class Replay extends Component" in source and "class Compare extends Component" in source
    replay = source[source.index("class Replay extends Component"):source.index("class BatchTable extends Component")]
    compare = source[source.index("class Compare extends Component"):source.index("class Replay extends Component")]
    assert "<${EventRow}" in replay
    assert compare.count("<${EventRow}") == 2
    assert "compareBannerState(summary)" in compare
    assert "banner.secondaryIndex" in compare
