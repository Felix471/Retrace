from __future__ import annotations

import json
from pathlib import Path

import pytest

from retrace.adapters.registry import resolve_config
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "fixtures" / "support_pipeline"
EXPECTED_IDS = [f"case-{number:02d}" for number in range(1, 11)]


def test_builtin_adapter_ingests_complete_corpus(tmp_path: Path) -> None:
    config, adapter_ref = resolve_config(FIXTURE)
    assert adapter_ref == "builtin:support_pipeline"

    with SqliteStore(tmp_path / "cache.db") as store:
        ingest(config, FIXTURE, store)
        runs = store.list_runs()

        assert [run.id for run in runs] == EXPECTED_IDS
        assert all(run.tokens_in is not None for run in runs)
        assert all(run.tokens_out is not None for run in runs)
        assert all(run.total_cost is not None for run in runs)
        assert {run.outcome for run in runs} == {"escalated", "resolved"}
        for run in runs:
            manifest = json.loads((FIXTURE / run.id / "meta.json").read_text(encoding="utf-8"))
            assert run.outcome == manifest["outcome"]
            assert run.metadata == {
                field: manifest[field]
                for field in ("issue_area", "model_name", "routing_variant")
            }

        event_types = {
            event.type
            for run in runs
            for event in store.get_events(run.id, limit=1000)[0]
        }
        assert {"tool_call", "tool_result"} <= event_types

        malformed = store.get_run("case-07")
        assert malformed is not None
        assert malformed.ingest_warnings == 2

        first = store.get_run("case-01")
        assert first is not None
        assert (first.n_events, first.n_turns) == (12, 12)
        assert (first.tokens_in, first.tokens_out) == (504, 174)
        assert first.total_cost == pytest.approx(0.001878)
        assert first.duration_s == 209
