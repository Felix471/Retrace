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
from pydantic import BaseModel, Field, field_validator

from retrace.core.aggregate import aggregate_runs
from retrace.core.mast import FAILURE_MODE_CATEGORIES
from retrace.core.model import VALID_EVENT_TYPES, Event
from retrace.core.store import SqliteStore
from retrace.core.tags import CorruptSidecarError, TagPathError, TagService, TagValidationError

RUN_SORT_FIELDS = (
    "id",
    "outcome",
    "n_events",
    "n_turns",
    "duration_s",
    "total_cost",
    "ingest_warnings",
    "n_repaired",
)
RUN_RESERVED_PARAMS = {"group_by", "sort", "order", "limit", "offset"}


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
    metadata: dict[str, Any]
    total_cost: float | None


class RunGroupResponse(BaseModel):
    """Aggregate statistics for one metadata value."""

    group_value: Any | None
    run_count: int
    outcome_distribution: dict[str, int]
    mean_turns: float
    median_turns: float
    mean_cost: float | None
    cost_excluded: int
    mean_duration: float | None
    duration_excluded: int


class RunsResponse(BaseModel):
    """A bounded page of filtered runs and optional unpaginated groups."""

    rows: list[RunListItem]
    total: int
    offset: int
    limit: int = Field(description="Applied page size, capped at 1000.")
    groups: list[RunGroupResponse] | None = None


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


class FailureModeResponse(BaseModel):
    """One mode in the fixed MAST failure vocabulary."""

    id: str
    name: str
    category: str
    description: str


class FailureModeCategoryResponse(BaseModel):
    """An ordered MAST category and its ordered failure modes."""

    category: str
    modes: list[FailureModeResponse]


class TagVocabularyResponse(BaseModel):
    """The ordered categories in the fixed MAST vocabulary."""

    categories: list[FailureModeCategoryResponse]


class TagModel(BaseModel):
    """One persisted MAST annotation."""

    mode: str
    event_ids: list[str] = Field(default_factory=list)
    note: str = ""
    source: str = "manual"
    confidence: float | None = None
    created_at: str | None = None

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        from retrace.core.mast import FAILURE_MODES_BY_ID

        if value not in FAILURE_MODES_BY_ID:
            raise ValueError("unknown MAST mode")
        return value


class StoredTagModel(TagModel):
    """A stored tag decorated with detached anchors."""

    created_at: str
    detached_event_ids: list[str] = Field(default_factory=list)


class TagsPutRequest(BaseModel):
    """Replacement tag state for one run."""

    tags: list[TagModel]
    run_note: str = ""


class TagsResponse(BaseModel):
    """Current tag state and any non-fatal read warning."""

    run_id: str
    tags: list[StoredTagModel]
    run_note: str
    warning: str | None = None


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
        app.state.tags = TagService(store)
        try:
            yield
        finally:
            store.close()
            del app.state.tags
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

    @app.get("/api/tags/vocabulary", response_model=TagVocabularyResponse)
    async def tag_vocabulary() -> TagVocabularyResponse:
        return TagVocabularyResponse(
            categories=[
                FailureModeCategoryResponse(
                    category=category,
                    modes=[
                        FailureModeResponse.model_validate(mode, from_attributes=True)
                        for mode in modes
                    ],
                )
                for category, modes in FAILURE_MODE_CATEGORIES
            ]
        )

    @app.get("/api/runs", response_model=RunsResponse)
    async def runs(
        request: Request,
        group_by: str | None = None,
        sort: str = "id",
        order: str = "asc",
        limit: Annotated[int, Query(ge=0)] = 200,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> RunsResponse:
        store: SqliteStore = request.app.state.store
        if sort not in RUN_SORT_FIELDS:
            valid = ", ".join(RUN_SORT_FIELDS)
            raise HTTPException(status_code=422, detail=f"sort must be one of: {valid}")
        if order not in {"asc", "desc"}:
            raise HTTPException(status_code=422, detail="order must be one of: asc, desc")

        filters: dict[str, list[str]] = {}
        for key, value in request.query_params.multi_items():
            if key not in RUN_RESERVED_PARAMS:
                filters.setdefault(key, []).append(value)
        filtered = store.list_runs(filters=filters)
        run_rows = [run for run in filtered if not isinstance(run, tuple)]
        run_rows.sort(key=lambda run: run.id)
        present = [run for run in run_rows if getattr(run, sort) is not None]
        missing = [run for run in run_rows if getattr(run, sort) is None]
        present.sort(key=lambda run: getattr(run, sort), reverse=order == "desc")
        sorted_rows = present + missing
        applied_limit = min(limit, 1000)
        page = sorted_rows[offset : offset + applied_limit]
        rows = [
            RunListItem(
                id=run.id,
                outcome=run.outcome,
                n_events=run.n_events,
                n_turns=run.n_turns,
                ingest_warnings=run.ingest_warnings,
                n_repaired=run.n_repaired,
                duration_s=run.duration_s,
                metadata=run.metadata,
                total_cost=run.total_cost,
            )
            for run in page
        ]
        groups = None
        if group_by is not None:
            groups = [RunGroupResponse.model_validate(group, from_attributes=True) for group in aggregate_runs(store, filters, group_by)]
        return RunsResponse(
            rows=rows,
            total=len(sorted_rows),
            offset=offset,
            limit=applied_limit,
            groups=groups,
        )

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

    @app.get("/api/runs/{run_id}/tags", response_model=TagsResponse)
    async def run_tags(request: Request, run_id: str) -> dict[str, Any]:
        store: SqliteStore = request.app.state.store
        if store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            service: TagService = request.app.state.tags
            return service.get(run_id)
        except TagPathError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put("/api/runs/{run_id}/tags", response_model=TagsResponse)
    async def replace_run_tags(
        request: Request, run_id: str, payload: TagsPutRequest
    ) -> dict[str, Any]:
        store: SqliteStore = request.app.state.store
        if store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            tags = [tag.model_dump(exclude_none=False) for tag in payload.tags]
            service: TagService = request.app.state.tags
            return service.put(run_id, tags, payload.run_note)
        except TagValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (CorruptSidecarError, TagPathError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
    "FailureModeCategoryResponse",
    "FailureModeResponse",
    "RunGroupResponse",
    "RunListItem",
    "RunResponse",
    "RunsResponse",
    "TagModel",
    "TagVocabularyResponse",
    "TagsPutRequest",
    "TagsResponse",
    "create_app",
]
