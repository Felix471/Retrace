"""FastAPI application for browsing an ingested experiment."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from retrace.core.model import VALID_EVENT_TYPES, Event
from retrace.core.store import SqliteStore


class RunResponse(BaseModel):
    """A run and the distinct values available for event filtering."""

    id: str
    experiment_id: str
    source_path: str
    metadata: dict[str, Any]
    outcome: str | None
    started_at: str | None
    ended_at: str | None
    duration_s: float | None
    n_events: int
    n_turns: int
    agent_ids: list[str]
    phases: list[str]
    tokens_in: int | None
    tokens_out: int | None
    total_cost: float | None
    ingest_warnings: int
    n_repaired: int
    agents: list[str]
    types: list[str]


class RunListItem(BaseModel):
    """The compact run representation used by the replay picker."""

    id: str
    outcome: str | None
    n_events: int
    n_turns: int
    ingest_warnings: int
    n_repaired: int
    duration_s: float | None


class RepairedFieldResponse(BaseModel):
    """One field repaired during ingest and its original value."""

    field: str
    original: Any


class EventResponse(BaseModel):
    """One event in its stable run order."""

    id: str
    run_id: str
    ordinal: int
    turn: int | None
    timestamp: str | None
    agent_id: str | None
    role: str | None
    type: str
    phase: str | None
    content: str
    structured: dict[str, Any] | None
    tokens_in: int | None
    tokens_out: int | None
    cost: float | None
    refs: list[str]
    metadata: dict[str, Any]
    badge: str = Field(
        description="Closed event type used to select the replay badge style."
    )
    repaired: list[RepairedFieldResponse] = Field(
        description="Repaired fields and their original values, in stored order."
    )


class EventsResponse(BaseModel):
    """A page of filtered events and its filtered total."""

    events: list[EventResponse]
    total: int
    offset: int
    limit: int = Field(description="Applied page size, capped at 2000.")


def _event_payload(event: Event) -> dict[str, Any]:
    data = event.to_dict()
    event_type = data["type"]
    metadata = data["metadata"]
    provenance = metadata.get("_retrace")
    repaired_metadata = provenance.get("repaired") if isinstance(provenance, dict) else None
    repaired = (
        [
            {"field": field, "original": original}
            for field, original in repaired_metadata.items()
        ]
        if isinstance(repaired_metadata, dict)
        else []
    )
    return {
        **data,
        "badge": event_type if event_type in VALID_EVENT_TYPES else "other",
        "repaired": repaired,
    }


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

    @app.get("/api/runs", response_model=list[RunListItem])
    async def runs(request: Request) -> list[RunListItem]:
        store: SqliteStore = request.app.state.store
        return [
            RunListItem(
                id=run.id,
                outcome=run.outcome,
                n_events=run.n_events,
                n_turns=run.n_turns,
                ingest_warnings=run.ingest_warnings,
                n_repaired=run.n_repaired,
                duration_s=run.duration_s,
            )
            for run in store.list_runs()
        ]

    @app.get("/api/runs/{run_id}", response_model=RunResponse)
    async def run_summary(request: Request, run_id: str) -> dict[str, Any]:
        store: SqliteStore = request.app.state.store
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {
            **run.to_dict(),
            "agents": store.distinct_agents(run_id),
            "phases": store.distinct_phases(run_id),
            "types": store.distinct_types(run_id),
        }

    @app.get("/api/runs/{run_id}/events", response_model=EventsResponse)
    async def run_events(
        request: Request,
        run_id: str,
        agent: str | None = None,
        phase: str | None = None,
        event_type: Annotated[str | None, Query(alias="type")] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=0)] = 500,
    ) -> dict[str, Any]:
        store: SqliteStore = request.app.state.store
        if store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        applied_limit = min(limit, 2000)
        events, total = store.get_events(
            run_id,
            agent=agent,
            phase=phase,
            type=event_type,
            offset=offset,
            limit=applied_limit,
        )
        return {
            "events": [_event_payload(event) for event in events],
            "total": total,
            "offset": offset,
            "limit": applied_limit,
        }

    @app.get("/", response_class=FileResponse)
    def shell() -> FileResponse:
        return FileResponse(str(ui_dir.joinpath("index.html")), media_type="text/html")

    app.mount("/ui", StaticFiles(directory=str(ui_dir)), name="ui")
    return app


__all__ = [
    "EventResponse",
    "EventsResponse",
    "RunListItem",
    "RunResponse",
    "create_app",
]
