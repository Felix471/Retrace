from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

import httpx

from retrace.adapters.registry import resolve_config
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore
from retrace.server.app import create_app

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
STRUCTURAL_PAIR = ("support-demo-05", "support-demo-06")
CONTENT_PAIR = ("support-demo-01", "support-demo-02")


def _files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != "README.md"
    }


def _database(tmp_path: Path) -> Path:
    database = tmp_path / "demo.db"
    config, _ = resolve_config(DEMO)
    with SqliteStore(database) as store:
        report = ingest(config, DEMO, store)
        assert report.line_failures == []
    return database


def test_regeneration_is_byte_identical_and_small(tmp_path: Path) -> None:
    generated = tmp_path / "demo"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_demo.py"), str(generated)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert _files(generated) == _files(DEMO)
    assert sum(len(value) for value in _files(DEMO).values()) < 2_000_000


def test_resolution_and_check_command(tmp_path: Path) -> None:
    config, adapter = resolve_config(DEMO)
    assert config.run_discovery.unit == "dir"
    assert Path(adapter).resolve() == (DEMO / "retrace.yaml").resolve()
    result = subprocess.run(
        [sys.executable, "-m", "retrace.cli", "check", str(DEMO)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Runs: 40 runs" in result.stdout
    assert "Line failures: 0" in result.stdout
    assert "Warnings: 0 total" in result.stdout


def test_demo_api_smoke_compare_and_sidecars(tmp_path: Path) -> None:
    database = _database(tmp_path)

    async def check() -> None:
        app = create_app(database)
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            runs = (await client.get("/api/runs", params={"limit": 100})).json()
            assert runs["total"] == 40 and len(runs["rows"]) == 40
            experiment = (await client.get("/api/experiment")).json()
            assert set(experiment["metadata_keys"]) == {
                "issue_area", "model_name", "routing_variant",
            }
            distribution = (await client.get("/api/tags/distribution")).json()
            assert distribution["tagged_runs"] == distribution["total_tags"] == 5
            populated = {mode["id"] for mode in distribution["modes"] if mode["total_tags"]}
            assert populated == {"1.3", "2.2", "2.6", "3.1", "3.2"}
            for run_id in ("support-demo-03", "support-demo-08", "support-demo-14", "support-demo-25", "support-demo-33"):
                tags = (await client.get(f"/api/runs/{run_id}/tags")).json()
                assert len(tags["tags"]) == 1 and tags.get("warning") is None
            for pair, kind in ((STRUCTURAL_PAIR, "structural"), (CONTENT_PAIR, "content")):
                compared = (await client.get(
                    "/api/compare", params={"a": pair[0], "b": pair[1], "limit": 2000}
                )).json()
                assert compared["first_divergence"]["kind"] == kind

    asyncio.run(check())


def test_walkthrough_run_ids_exist() -> None:
    text = (ROOT / "docs" / "walkthrough.md").read_text(encoding="ascii")
    run_ids = {path.name for path in DEMO.glob("support-demo-*") if path.is_dir()}
    cited = set(re.findall(r"support-demo-\d{2}", text))
    assert cited <= run_ids
    assert set(STRUCTURAL_PAIR + CONTENT_PAIR) <= cited
