"""Disposable SQLite cache for parsed runs and events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, Self, TypeAlias

from retrace.core.model import Event, Run

SCHEMA_VERSION = 1

RunFilterValue: TypeAlias = Sequence[Any]
RunFilters: TypeAlias = Mapping[str, RunFilterValue]
GroupedRun: TypeAlias = tuple[Any, Run]
ExperimentSummary: TypeAlias = tuple[int, int, int]


class Store(Protocol):
    """Typed interface used by ingestion and API layers."""

    def get_run(self, run_id: str) -> Run | None: ...

    def list_runs(
        self, filters: RunFilters | None = None, group_by: str | None = None
    ) -> list[Run] | list[GroupedRun]: ...

    def get_events(
        self,
        run_id: str,
        agent: str | None = None,
        phase: str | None = None,
        type: str | None = None,
        offset: int = 0,
        limit: int = 500,
    ) -> tuple[list[Event], int]: ...


_RUN_COLUMNS = (
    "id, experiment_id, source_path, metadata, outcome, started_at, ended_at, "
    "duration_s, n_events, n_turns, agent_ids, phases, tokens_in, tokens_out, "
    "total_cost, ingest_warnings, n_repaired"
)
_EVENT_COLUMNS = (
    "id, run_id, ordinal, turn, timestamp, agent_id, role, type, phase, content, "
    "structured, tokens_in, tokens_out, cost, refs, metadata"
)

_CREATE_SCHEMA = """
CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    metadata TEXT NOT NULL,
    outcome TEXT,
    started_at TEXT,
    ended_at TEXT,
    duration_s REAL,
    n_events INTEGER NOT NULL,
    n_turns INTEGER NOT NULL,
    agent_ids TEXT NOT NULL,
    phases TEXT NOT NULL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    total_cost REAL,
    ingest_warnings INTEGER NOT NULL,
    n_repaired INTEGER NOT NULL
);
CREATE INDEX idx_runs_outcome_id ON runs(outcome, id);
CREATE INDEX idx_runs_source_path_id ON runs(source_path, id);
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    turn INTEGER,
    timestamp TEXT,
    agent_id TEXT,
    role TEXT,
    type TEXT NOT NULL,
    phase TEXT,
    content TEXT NOT NULL,
    structured TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost REAL,
    refs TEXT NOT NULL,
    metadata TEXT NOT NULL
);
CREATE INDEX idx_events_run_ordinal ON events(run_id, ordinal);
CREATE INDEX idx_events_run_agent ON events(run_id, agent_id);
CREATE INDEX idx_events_run_phase ON events(run_id, phase);
CREATE TABLE files (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL
);
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _metadata_filter_value(value: object) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return None


def _metadata_matches(value: object, accepted: Sequence[Any]) -> bool:
    canonical = _metadata_filter_value(value)
    if canonical is None:
        return False
    if any(raw == canonical for raw in accepted):
        return True
    if isinstance(value, bool):
        alternate = "1" if value else "0"
        return any(raw == alternate for raw in accepted)
    return False


def _run_values(run: Run) -> tuple[Any, ...]:
    data = run.to_dict()
    return (
        data["id"], data["experiment_id"], data["source_path"], _json(data["metadata"]),
        data["outcome"], data["started_at"], data["ended_at"], data["duration_s"],
        data["n_events"], data["n_turns"], _json(data["agent_ids"]), _json(data["phases"]),
        data["tokens_in"], data["tokens_out"], data["total_cost"],
        data["ingest_warnings"], data["n_repaired"],
    )


def _event_values(event: Event) -> tuple[Any, ...]:
    data = event.to_dict()
    return (
        data["id"], data["run_id"], data["ordinal"], data["turn"], data["timestamp"],
        data["agent_id"], data["role"], data["type"], data["phase"], data["content"],
        None if data["structured"] is None else _json(data["structured"]),
        data["tokens_in"], data["tokens_out"], data["cost"], _json(data["refs"]),
        _json(data["metadata"]),
    )


def _row_to_run(row: sqlite3.Row) -> Run:
    data = dict(row)
    for name in ("metadata", "agent_ids", "phases"):
        data[name] = json.loads(data[name])
    data.pop("group_value", None)
    return Run.from_dict(data)


def _row_to_event(row: sqlite3.Row) -> Event:
    data = dict(row)
    for name in ("structured", "refs", "metadata"):
        if data[name] is not None:
            data[name] = json.loads(data[name])
    return Event.from_dict(data)


class SqliteStore:
    """SQLite-backed disposable cache, usable as a context manager."""

    def __init__(self, path: Path | str) -> None:
        self.path = path
        self._connection = sqlite3.connect(str(path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def _ensure_schema(self) -> None:
        row = self._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name = ?", ("table", "meta")
        ).fetchone()
        version = None
        if row is not None:
            version_row = self._connection.execute(
                "SELECT value FROM meta WHERE key = ?", ("schema_version",)
            ).fetchone()
            version = None if version_row is None else version_row[0]
        required = {"runs", "events", "files", "meta"}
        present = {
            item[0]
            for item in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?", ("table",)
            )
        }
        if version != str(SCHEMA_VERSION) or not required <= present:
            with self._connection:
                self._connection.executescript(
                    "DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS runs; "
                    "DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS meta;" + _CREATE_SCHEMA
                )
                self._connection.executemany(
                    "INSERT INTO meta(key, value) VALUES (?, ?)",
                    (("schema_version", str(SCHEMA_VERSION)), ("adapter_ref", "")),
                )

    def _write_run(self, run: Run, events: Iterable[Event]) -> None:
        self._connection.execute(
            f"INSERT INTO runs({_RUN_COLUMNS}) VALUES ({','.join('?' * 17)})",
            _run_values(run),
        )
        self._connection.executemany(
            f"INSERT INTO events({_EVENT_COLUMNS}) VALUES ({','.join('?' * 16)})",
            (_event_values(event) for event in events),
        )

    def insert_run(self, run: Run, events: Iterable[Event]) -> None:
        with self._connection:
            self._write_run(run, events)

    def replace_run(self, run: Run, events: Iterable[Event]) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM runs WHERE id = ?", (run.id,))
            self._write_run(run, events)

    def delete_runs_for_source(self, source_path: str) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM runs WHERE source_path = ?", (source_path,))

    def get_run(self, run_id: str) -> Run | None:
        row = self._connection.execute(
            f"SELECT {_RUN_COLUMNS} FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        return None if row is None else _row_to_run(row)

    @staticmethod
    def _runs_query(filters: RunFilters | None, group_by: str | None) -> tuple[str, list[Any]]:
        params: list[Any] = []
        select = _RUN_COLUMNS
        clauses: list[str] = []
        for key, accepted in (filters or {}).items():
            if key not in {"outcome", "source_path"}:
                continue
            values = list(accepted)
            if not values:
                clauses.append("0")
                continue
            placeholders = ",".join("?" for _ in values)
            clauses.append(f"{key} IN ({placeholders})")
            params.extend(values)
        query = f"SELECT {select} FROM runs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        return query + " ORDER BY id", params

    def list_runs(
        self, filters: RunFilters | None = None, group_by: str | None = None
    ) -> list[Run] | list[GroupedRun]:
        query, params = self._runs_query(filters, group_by)
        rows = self._connection.execute(query, params).fetchall()
        runs = [_row_to_run(row) for row in rows]
        # JSON scalar matching is type-sensitive, so metadata filters are applied
        # after the indexed reserved SQL filters while the parsed types are available.
        metadata_filters = {
            key: accepted
            for key, accepted in (filters or {}).items()
            if key not in {"outcome", "source_path"}
        }
        runs = [
            run for run in runs
            if all(_metadata_matches(run.metadata.get(key), accepted)
                   for key, accepted in metadata_filters.items())
        ]
        if group_by is None:
            return runs
        return [(run.metadata.get(group_by), run) for run in runs]

    @staticmethod
    def _events_where(
        run_id: str, agent: str | None, phase: str | None, event_type: str | None
    ) -> tuple[str, list[Any]]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        for column, value in (("agent_id", agent), ("phase", phase), ("type", event_type)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        return " AND ".join(clauses), params

    def get_events(
        self,
        run_id: str,
        agent: str | None = None,
        phase: str | None = None,
        type: str | None = None,
        offset: int = 0,
        limit: int = 500,
    ) -> tuple[list[Event], int]:
        if offset < 0 or limit < 0:
            raise ValueError("offset and limit must be non-negative")
        where, params = self._events_where(run_id, agent, phase, type)
        total = self._connection.execute(
            f"SELECT COUNT(*) FROM events WHERE {where}", params
        ).fetchone()[0]
        rows = self._connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events WHERE {where} "
            "ORDER BY ordinal, id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [_row_to_event(row) for row in rows], total

    def _distinct(self, column: str, run_id: str) -> list[str]:
        rows = self._connection.execute(
            f"SELECT DISTINCT {column} FROM events "
            f"WHERE run_id = ? AND {column} IS NOT NULL ORDER BY {column}",
            (run_id,),
        )
        return [row[0] for row in rows]

    def distinct_agents(self, run_id: str) -> list[str]:
        return self._distinct("agent_id", run_id)

    def distinct_phases(self, run_id: str) -> list[str]:
        return self._distinct("phase", run_id)

    def distinct_types(self, run_id: str) -> list[str]:
        return self._distinct("type", run_id)

    def fingerprints(self) -> dict[str, tuple[float, int]]:
        rows = self._connection.execute("SELECT path, mtime, size FROM files ORDER BY path")
        return {row[0]: (row[1], row[2]) for row in rows}

    def set_fingerprint(self, path: str, mtime: float, size: int) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO files(path, mtime, size) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size",
                (path, mtime, size),
            )

    def delete_fingerprint(self, path: str) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM files WHERE path = ?", (path,))

    def meta_get(self, key: str) -> str | None:
        row = self._connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]

    def meta_set(self, key: str, value: str) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def experiment_summary(self) -> ExperimentSummary:
        row = self._connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(n_events), 0), "
            "COALESCE(SUM(ingest_warnings), 0) FROM runs"
        ).fetchone()
        return row[0], row[1], row[2]

    def journal_mode(self) -> str:
        """Return the active SQLite journal mode (primarily for diagnostics)."""
        return self._connection.execute("PRAGMA journal_mode").fetchone()[0]

    def events_query_plan(
        self,
        run_id: str,
        agent: str | None = None,
        phase: str | None = None,
        type: str | None = None,
    ) -> list[str]:
        """Return SQLite's plan details for the paginated events hot query."""
        where, params = self._events_where(run_id, agent, phase, type)
        rows = self._connection.execute(
            f"EXPLAIN QUERY PLAN SELECT {_EVENT_COLUMNS} FROM events WHERE {where} "
            "ORDER BY ordinal, id LIMIT ? OFFSET ?",
            [*params, 500, 0],
        )
        return [row[3] for row in rows]

    def runs_query_plan(self, filters: RunFilters | None = None) -> list[str]:
        """Return SQLite's plan details for the runs hot query."""
        query, params = self._runs_query(filters, None)
        rows = self._connection.execute("EXPLAIN QUERY PLAN " + query, params)
        return [row[3] for row in rows]


__all__ = ["SCHEMA_VERSION", "ExperimentSummary", "GroupedRun", "SqliteStore", "Store"]
