from __future__ import annotations

import json
from pathlib import Path

import pytest

from retrace.adapters.mapping_schema import MappingConfigError, validate_mapping_config
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _flat_config(unit: str = "dir", *, phase: str = "phase"):
    discovery = {"pattern": "case-*", "unit": unit, "events_file": "events.jsonl"}
    if unit == "line":
        discovery = {"pattern": "*.jsonl", "unit": "line"}
    return validate_mapping_config({
        "retrace_mapping": 1,
        "run_discovery": discovery,
        "run": {
            "id": "{dir_name}" if unit != "line" else "id",
            "manifest": "meta.json" if unit != "line" else None,
            "metadata": {"kind": "kind"},
            "outcome": "outcome",
        },
        "event": {
            "turn": "turn", "timestamp": "at", "agent_id": "actor",
            "type": "type", "phase": phase, "content": "text",
            "tokens_in": "usage.in", "tokens_out": "usage.out", "cost": "cost",
        },
        "agents": {"path": "people", "key": "id", "attributes": {"role": "role"}},
    })


def _tree(root: Path) -> Path:
    run = root / "case-one"
    run.mkdir(parents=True)
    (run / "meta.json").write_text(json.dumps({
        "kind": "demo", "outcome": "ok",
        "people": [{"id": "a", "role": "operator"}],
    }), encoding="utf-8")
    records = [
        {"turn": 1, "at": "2026-01-01T00:00:00Z", "actor": "a", "type": "message",
         "phase": "open", "text": "hello", "usage": {"in": 2, "out": 3}, "cost": 0.1},
        {"turn": 2, "at": "2026-01-01T00:00:05Z", "actor": "a", "type": "message",
         "phase": "done", "usage": {"in": 5, "out": 7}, "cost": 0.2},
    ]
    text = json.dumps(records[0]) + "\n{bad\n" + json.dumps(records[1]) + "\n"
    (run / "events.jsonl").write_text(text, encoding="utf-8")
    return run / "events.jsonl"


def test_flat_ingest_summaries_roster_fallback_and_incremental(tmp_path: Path) -> None:
    events_path = _tree(tmp_path)
    config = _flat_config()
    with SqliteStore(tmp_path / "cache.db") as store:
        first = ingest(config, tmp_path, store)
        run = store.get_run("case-one")
        assert first.runs_ingested == 1
        assert run is not None
        assert (run.n_events, run.n_turns, run.tokens_in, run.tokens_out) == (2, 2, 7, 10)
        assert run.total_cost == pytest.approx(0.3)
        assert run.duration_s == 5
        assert run.agent_ids == ["a"]
        assert run.phases == ["open", "done"]
        assert run.ingest_warnings == 1
        stored, total = store.get_events("case-one")
        assert total == 2 and stored[0].role == "operator"
        assert stored[1].structured == json.loads(events_path.read_text().splitlines()[2])
        assert stored[1].content.startswith("{\n")

        second = ingest(config, tmp_path, store)
        assert second.runs_skipped_unchanged == 1
        assert second.processed_run_ids == []

        full = ingest(config, tmp_path, store, reingest=True)
        assert full.runs_replaced == 1


def test_progress_and_config_hash_warning(tmp_path: Path) -> None:
    _tree(tmp_path)
    calls: list[tuple[str, int, int]] = []
    with SqliteStore(tmp_path / "cache.db") as store:
        assert not ingest(_flat_config(), tmp_path, store, progress=lambda *x: calls.append(x)).config_hash_warning
        assert calls == [("case-one", 1, 1)]
        assert ingest(_flat_config(phase="turn"), tmp_path, store).config_hash_warning
        assert not ingest(_flat_config(phase="turn"), tmp_path, store).config_hash_warning


def test_flat_line_form_is_rejected(tmp_path: Path) -> None:
    with (
        SqliteStore(tmp_path / "cache.db") as store,
        pytest.raises(MappingConfigError, match="sources are required for line units"),
    ):
        ingest(_flat_config("line"), tmp_path, store)


def test_avalon_multisource_repairs_and_ids(tmp_path: Path) -> None:
    config = validate_mapping_config({
        "retrace_mapping": 1,
        "run_discovery": {"pattern": "games.jsonl", "unit": "line"},
        "run": {"id": "gameId", "metadata": {}, "outcome": "winner"},
        "event": {"sources": [
            {"name": "discussion", "path": "discussions", "priority": 0,
             "fields": {"turn": "round", "timestamp": "timestamp",
                        "agent_id": "speakerId", "phase": "phase", "content": "content"}},
            {"name": "proposal", "path": "teamProposals", "priority": 1,
             "fields": {"turn": "questRound", "agent_id": "proposedBy"}},
            {"name": "quest", "path": "quests", "priority": 2,
             "fields": {"turn": "round", "agent_id": "proposedBy", "metadata": "rest"},
             "repairs": [
                 {"field": "turn", "strategy": "ordinal", "base": 1},
                 {"field": "result", "strategy": "derive",
                  "expr": "contains(values(actions), 'fail')",
                  "map": {"True": "fail", "False": "success"}},
             ]},
        ], "merge": {"sort_by": "turn"}},
        "agents": {"path": "players", "key": "id", "attributes": {"role": "role"}},
    })
    ids = [
        "1776453329940-bvvf9", "1776470168031-kwy8o", "1776471689022-f5veq",
        "1776642987532-fob9d", "1776701240615-6ius8",
    ]
    with SqliteStore(tmp_path / "cache.db") as store:
        ingest(config, FIXTURES / "avalon_mini", store)
        assert [run.id for run in store.list_runs()] == ids
        assert {run.id: run.n_repaired for run in store.list_runs()} == dict(
            zip(ids, [0, 1, 0, 0, 2], strict=True)
        )
        first = store.get_run(ids[0])
        assert first is not None and first.n_events == 70
        events, total = store.get_events(ids[0])
        assert total == 70
        assert [events[0].id, events[-1].id] == [f"{ids[0]}:0", f"{ids[0]}:69"]


def test_line_ingest_streams_twice_and_inserts_in_physical_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = tmp_path / "runs.jsonl"
    records = [
        {"id": run_id, "items": [{"text": run_id}]}
        for run_id in ("third-name", "first-name", "second-name")
    ]
    aggregate.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8"
    )
    config = validate_mapping_config({
        "retrace_mapping": 1,
        "run_discovery": {"pattern": "*.jsonl", "unit": "line"},
        "run": {"id": "id"},
        "event": {"sources": [
            {"name": "neutral", "path": "items", "fields": {"content": "text"}},
        ]},
    })

    original_open = Path.open
    opens = 0

    class NoSlurp:
        def __init__(self, stream: object) -> None:
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            self.stream.close()

        def __iter__(self):
            return iter(self.stream)

        def read(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("aggregate input must not be slurped with read()")

        def readlines(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("aggregate input must not be slurped with readlines()")

    def guarded_open(path: Path, *args: object, **kwargs: object):
        nonlocal opens
        stream = original_open(path, *args, **kwargs)
        if path.resolve() == aggregate.resolve():
            opens += 1
            return NoSlurp(stream)
        return stream

    inserted: list[str] = []
    original_insert = SqliteStore.insert_run

    def recording_insert(store: SqliteStore, run: object, events: object) -> None:
        inserted.append(run.id)
        original_insert(store, run, events)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(SqliteStore, "insert_run", recording_insert)
    with SqliteStore(tmp_path / "cache.db") as store:
        report = ingest(config, tmp_path, store)

    expected = [record["id"] for record in records]
    assert opens == 2
    assert inserted == report.processed_run_ids == expected


def test_support_fixture_hand_computed_summary_and_malformed(tmp_path: Path) -> None:
    config = validate_mapping_config({
        "retrace_mapping": 1,
        "run_discovery": {"pattern": "case-*", "unit": "dir", "events_file": "events.jsonl"},
        "run": {"id": "{dir_name}", "manifest": "meta.json",
                "metadata": {"issue_area": "issue_area", "model": "model_name"},
                "outcome": "outcome"},
        "event": {"turn": "sequence", "timestamp": "occurred_at", "agent_id": "handler",
                  "type": {"from": "step_kind", "map": {"message": "message",
                           "tool_call": "tool_call", "tool_result": "tool_result"}},
                  "phase": "workflow_stage", "content": "body", "tokens_in": "tokens_in",
                  "tokens_out": "tokens_out", "cost": "cost"},
    })
    with SqliteStore(tmp_path / "cache.db") as store:
        ingest(config, FIXTURES / "support_pipeline", store)
        assert len(store.list_runs()) == 10
        run = store.get_run("case-01")
        assert run is not None
        assert (run.n_events, run.n_turns, run.tokens_in, run.tokens_out) == (12, 12, 504, 174)
        assert run.total_cost == pytest.approx(0.001878)
        assert run.duration_s == 209
        assert run.agent_ids == ["triage", "specialist", "reviewer"]
        malformed = store.get_run("case-07")
        assert malformed is not None and malformed.ingest_warnings == 2
