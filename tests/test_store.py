from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from retrace.core.model import Event, Run, parse_timestamp
from retrace.core.store import SCHEMA_VERSION, SqliteStore


def make_run(run_id: str = "r-1", **changes: object) -> Run:
    started, _ = parse_timestamp(0)
    ended, _ = parse_timestamp("2025-02-03T04:05:06Z")
    values = {
        "id": run_id,
        "experiment_id": "set-1",
        "source_path": "input/a.jsonl",
        "metadata": {"size": "small", "mode": "fast"},
        "outcome": "ok",
        "started_at": started,
        "ended_at": ended,
        "duration_s": 3.25,
        "n_events": 3,
        "n_turns": 2,
        "agent_ids": ["a", "b"],
        "phases": ["first", "second"],
        "tokens_in": 7,
        "tokens_out": None,
        "total_cost": 0.125,
        "ingest_warnings": 2,
        "n_repaired": 1,
    }
    values.update(changes)
    return Run(**values)  # type: ignore[arg-type]


def make_event(
    event_id: str,
    ordinal: int,
    *,
    run_id: str = "r-1",
    agent: str | None = "a",
    phase: str | None = "first",
    event_type: str = "message",
) -> Event:
    timestamp, _ = Event.parse_timestamp(1_700_000_000_000 if ordinal == 0 else None)
    return Event(
        id=event_id,
        run_id=run_id,
        ordinal=ordinal,
        turn=None if ordinal == 0 else ordinal,
        timestamp=timestamp,
        agent_id=agent,
        role=None if ordinal == 0 else "worker",
        type=event_type,
        phase=phase,
        content="unicode: 雪\ntext",
        structured=None if ordinal == 0 else {"nested": [1, True, None]},
        tokens_in=None if ordinal == 0 else ordinal,
        tokens_out=ordinal + 1,
        cost=None if ordinal == 0 else 0.01 * ordinal,
        refs=[] if ordinal == 0 else ["e-0"],
        metadata={"flag": True, "note": None},
    )


def test_round_trip_all_fields() -> None:
    run = make_run()
    events = [make_event("e-0", 0), make_event("e-1", 1), make_event("e-2", 2)]
    with SqliteStore(":memory:") as store:
        store.insert_run(run, events)
        assert store.get_run(run.id) == run
        actual, total = store.get_events(run.id)
        assert actual == events
        assert total == 3
        assert store.distinct_agents(run.id) == ["a"]
        assert store.distinct_phases(run.id) == ["first"]
        assert store.distinct_types(run.id) == ["message"]
        assert store.experiment_summary() == (1, 3, 2)


def test_event_filter_and_pagination_matrix() -> None:
    events = [
        make_event("e-0", 0, agent="a", phase="first", event_type="message"),
        make_event("e-1", 1, agent="b", phase="first", event_type="tool_call"),
        make_event("e-2", 2, agent="a", phase="second", event_type="tool_call"),
        make_event("e-3", 3, agent="a", phase="first", event_type="tool_call"),
        make_event("e-4", 4, agent=None, phase=None, event_type="other"),
    ]
    with SqliteStore(":memory:") as store:
        store.insert_run(make_run(n_events=5), events)
        for kwargs, expected in [
            ({"agent": "a"}, ["e-0", "e-2", "e-3"]),
            ({"phase": "first"}, ["e-0", "e-1", "e-3"]),
            ({"type": "tool_call"}, ["e-1", "e-2", "e-3"]),
            ({"agent": "a", "phase": "first", "type": "tool_call"}, ["e-3"]),
        ]:
            found, total = store.get_events("r-1", **kwargs)
            assert [event.id for event in found] == expected
            assert total == len(expected)
        page, total = store.get_events("r-1", offset=2, limit=2)
        assert [event.id for event in page] == ["e-2", "e-3"] and total == 5
        page, total = store.get_events("r-1", offset=4, limit=2)
        assert [event.id for event in page] == ["e-4"] and total == 5
        page, total = store.get_events("r-1", offset=20, limit=2)
        assert page == [] and total == 5


def test_list_runs_filters_groups_and_hostile_metadata() -> None:
    hostile = 'x"); DROP TABLE runs;--'
    runs = [
        make_run("r-1"),
        make_run("r-2", metadata={"size": "large", "mode": "fast"}, outcome="bad"),
        make_run("r-3", metadata={"size": "small", "mode": "slow"}),
        make_run("r-4", metadata={hostile: hostile}),
    ]
    with SqliteStore(":memory:") as store:
        for run in runs:
            store.insert_run(run, [])
        assert [run.id for run in store.list_runs({"size": ["small"]})] == ["r-1", "r-3"]
        assert [run.id for run in store.list_runs({"size": ["small", "large"]})] == [
            "r-1", "r-2", "r-3"
        ]
        assert [run.id for run in store.list_runs({"size": ["small"], "mode": ["fast"]})] == [
            "r-1"
        ]
        assert [run.id for run in store.list_runs({"outcome": ["bad"]})] == ["r-2"]
        assert [run.id for run in store.list_runs({hostile: [hostile]})] == ["r-4"]
        grouped = store.list_runs(group_by="size")
        assert [(group, run.id) for group, run in grouped] == [
            ("small", "r-1"), ("large", "r-2"), ("small", "r-3"), (None, "r-4")
        ]
        assert store.get_run("r-1") is not None


def test_list_runs_preserves_typed_groups_and_filters_scalars() -> None:
    runs = [
        make_run("false", metadata={"flag": False, "level": 0, "kind": "plain"}),
        make_run("true", metadata={"flag": True, "level": 1, "kind": "plain"}),
        make_run("other", metadata={"flag": [True], "level": {"value": 1}}),
    ]
    with SqliteStore(":memory:") as store:
        for run in runs:
            store.insert_run(run, [])

        assert [run.id for run in store.list_runs({"flag": ["false"]})] == ["false"]
        assert [run.id for run in store.list_runs({"flag": ["0"]})] == ["false"]
        assert [run.id for run in store.list_runs({"flag": ["true", "1"]})] == ["true"]
        assert [run.id for run in store.list_runs({"level": ["1"]})] == ["true"]
        assert [run.id for run in store.list_runs({"kind": ["plain"]})] == [
            "false", "true"
        ]
        assert [(value, run.id) for value, run in store.list_runs(group_by="flag")] == [
            (False, "false"), ([True], "other"), (True, "true")
        ]
        assert [(value, run.id) for value, run in store.list_runs(group_by="level")] == [
            (0, "false"), ({"value": 1}, "other"), (1, "true")
        ]


def test_hot_queries_use_indexes() -> None:
    with SqliteStore(":memory:") as store:
        store.insert_run(make_run(), [make_event("e-0", 0)])
        event_plan = " ".join(store.events_query_plan("r-1"))
        runs_plan = " ".join(store.runs_query_plan({"outcome": ["ok"]}))
        assert "USING INDEX idx_events_run_ordinal" in event_plan
        assert "USING INDEX idx_runs_outcome_id" in runs_plan


def test_insert_and_replace_are_atomic() -> None:
    original = make_run()
    original_events = [make_event("e-0", 0)]
    with SqliteStore(":memory:") as store:
        store.insert_run(original, original_events)
        replacement = replace(original, outcome="changed")
        duplicate = [make_event("new", 0), make_event("new", 1)]
        with pytest.raises(sqlite3.IntegrityError):
            store.replace_run(replacement, duplicate)
        assert store.get_run(original.id) == original
        assert store.get_events(original.id) == (original_events, 1)

        failed = make_run("r-2")
        duplicate = [make_event("same", 0, run_id="r-2"), make_event("same", 1, run_id="r-2")]
        with pytest.raises(sqlite3.IntegrityError):
            store.insert_run(failed, duplicate)
        assert store.get_run("r-2") is None


def test_schema_rebuild_wal_fingerprints_and_meta(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    with SqliteStore(path) as store:
        store.insert_run(make_run(), [])
        store.set_fingerprint("b", 2.5, 20)
        store.set_fingerprint("a", 1.5, 10)
        store.set_fingerprint("a", 3.5, 30)
        assert store.fingerprints() == {"a": (3.5, 30), "b": (2.5, 20)}
        store.meta_set("mapping_hash", "abc")
        assert store.meta_get("mapping_hash") == "abc"
        assert store.meta_get("adapter_ref") == ""
        assert store.journal_mode() == "wal"
        store.meta_set("schema_version", "different")

    with SqliteStore(path) as rebuilt:
        assert rebuilt.list_runs() == []
        assert rebuilt.fingerprints() == {}
        assert rebuilt.meta_get("schema_version") == str(SCHEMA_VERSION)


def test_delete_runs_for_source() -> None:
    with SqliteStore(":memory:") as store:
        store.insert_run(make_run("r-1", source_path="one"), [])
        store.insert_run(make_run("r-2", source_path="two"), [])
        store.delete_runs_for_source("one")
        assert [run.id for run in store.list_runs()] == ["r-2"]
