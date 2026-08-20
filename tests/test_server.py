from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest

from retrace.adapters.registry import resolve_config
from retrace.cli.main import _pick_free_port, _resolve_bind_host
from retrace.core.ingest import _config_hash, ingest, stable_root_hash
from retrace.core.store import SqliteStore
from retrace.server.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures"


def _database(tmp_path: Path, fixture: str) -> tuple[Path, str]:
    root = FIXTURES / fixture
    path = tmp_path / f"{fixture}.db"
    config, adapter_ref = resolve_config(root)
    with SqliteStore(path) as store:
        ingest(config, root, store, adapter_ref=adapter_ref)
    return path, adapter_ref


async def _get(app: object, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with (
        app.router.lifespan_context(app),  # type: ignore[attr-defined]
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        return await client.get(path)


@pytest.mark.parametrize(
    ("fixture", "runs", "events", "warnings", "keys"),
    [
        ("avalon_mini", 5, 652, 3, ["config", "winReason"]),
        (
            "support_pipeline",
            10,
            120,
            2,
            ["issue_area", "model_name", "routing_variant"],
        ),
    ],
)
def test_experiment_api(
    tmp_path: Path,
    fixture: str,
    runs: int,
    events: int,
    warnings: int,
    keys: list[str],
) -> None:
    path, adapter_ref = _database(tmp_path, fixture)
    response = asyncio.run(_get(create_app(path), "/api/experiment"))
    assert response.status_code == 200
    assert response.json() == {
        "experiment_id": stable_root_hash(FIXTURES / fixture),
        "root_path": (FIXTURES / fixture).resolve().as_posix(),
        "adapter_ref": adapter_ref,
        "adapter_config_hash": _config_hash(resolve_config(FIXTURES / fixture)[0]),
        "run_count": runs,
        "total_events": events,
        "total_ingest_warnings": warnings,
        "metadata_keys": keys,
    }


def test_static_shell_and_scripts(tmp_path: Path) -> None:
    path, _ = _database(tmp_path, "avalon_mini")

    async def requests() -> list[httpx.Response]:
        app = create_app(path)
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            return [
                await client.get("/"),
                await client.get("/ui/app.js"),
                await client.get("/ui/vendor/preact.module.js"),
            ]

    shell, app_js, vendor_js = asyncio.run(requests())
    assert shell.headers["content-type"].startswith("text/html")
    assert "http://" not in shell.text and "https://" not in shell.text
    for response in (app_js, vendor_js):
        assert "javascript" in response.headers["content-type"]
        assert response.content


def test_app_lifespan_can_restart(tmp_path: Path) -> None:
    path, _ = _database(tmp_path, "avalon_mini")
    app = create_app(path)
    assert asyncio.run(_get(app, "/api/experiment")).status_code == 200
    assert asyncio.run(_get(app, "/api/experiment")).status_code == 200


def test_host_resolution(capsys: pytest.CaptureFixture[str]) -> None:
    assert _resolve_bind_host(None) == "127.0.0.1"
    assert _resolve_bind_host("0.0.0.0") == "0.0.0.0"
    assert "Warning:" in capsys.readouterr().out


def test_free_port_is_bindable() -> None:
    port = _pick_free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", port))


@pytest.mark.parametrize(
    ("name", "version"),
    [("preact.module.js", "10.26.9"), ("htm.module.js", "3.1.1")],
)
def test_vendor_license_headers(name: str, version: str) -> None:
    first_line = (REPO_ROOT / "src/retrace/ui/vendor" / name).read_text(
        encoding="utf-8"
    ).splitlines()[0]
    assert "MIT" in first_line
    assert version in first_line
