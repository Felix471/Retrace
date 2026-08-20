"""FastAPI application for browsing an ingested experiment."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from retrace.core.store import SqliteStore


def create_app(db_path: Path) -> FastAPI:
    """Create an application whose store is scoped to its lifespan."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = SqliteStore(db_path)
        app.state.store = store
        try:
            yield
        finally:
            store.close()
            del app.state.store

    app = FastAPI(lifespan=lifespan)
    ui_dir = files("retrace.ui")

    @app.get("/api/experiment")
    async def experiment(request: Request) -> dict[str, object]:
        store: SqliteStore = request.app.state.store
        run_count, total_events, total_ingest_warnings = store.experiment_summary()
        runs = store.list_runs()
        metadata_keys = sorted({key for run in runs for key in run.metadata})
        return {
            "experiment_id": store.meta_get("experiment_id"),
            "root_path": store.meta_get("root_path"),
            "adapter_ref": store.meta_get("adapter_ref"),
            "run_count": run_count,
            "total_events": total_events,
            "total_ingest_warnings": total_ingest_warnings,
            "metadata_keys": metadata_keys,
        }

    @app.get("/", response_class=FileResponse)
    def shell() -> FileResponse:
        return FileResponse(str(ui_dir.joinpath("index.html")), media_type="text/html")

    app.mount("/ui", StaticFiles(directory=str(ui_dir)), name="ui")
    return app


__all__ = ["create_app"]
