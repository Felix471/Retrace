from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from retrace.adapters.registry import load_builtin
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "fixtures" / "avalon_mini"
RAW_SAMPLE = REPO_ROOT / "reference-logs" / "avalon_sample.jsonl"
EXPECTED_IDS = [
    "1776453329940-bvvf9",
    "1776470168031-kwy8o",
    "1776471689022-f5veq",
    "1776642987532-fob9d",
    "1776701240615-6ius8",
]


@pytest.mark.parametrize(
    "target",
    [
        pytest.param(FIXTURE, id="fixture"),
        pytest.param(
            RAW_SAMPLE,
            id="raw-sample",
            marks=pytest.mark.skipif(not RAW_SAMPLE.is_file(), reason="local raw sample unavailable"),
        ),
    ],
)
def test_builtin_adapter_ingests_complete_corpus(target: Path, tmp_path: Path) -> None:
    config, adapter_ref = load_builtin("avalon")
    assert adapter_ref == "builtin:avalon"

    with SqliteStore(tmp_path / "cache.db") as store:
        ingest(config, target, store)
        runs = store.list_runs()
        assert [run.id for run in runs] == EXPECTED_IDS
        assert [run.outcome for run in runs] == ["evil", "good", "evil", "evil", "good"]
        assert [run.n_repaired for run in runs] == [0, 1, 0, 0, 2]
        assert [run.n_events for run in runs] == [70, 159, 193, 104, 126]
        assert all(run.ingest_warnings == run.n_repaired for run in runs)
        assert all(run.metadata.get("winReason") for run in runs)
        assert all("config" in run.metadata for run in runs)

        first, _ = store.get_events(EXPECTED_IDS[0], limit=1000)
        assert Counter(event.type for event in first) == {"message": 60, "other": 10}
        assert Counter(event.phase for event in first if event.phase in {"proposal", "quest"}) == {
            "proposal": 6,
            "quest": 4,
        }
        assert Counter(event.metadata["_retrace"]["source"] for event in first) == {
            "discussions": 60,
            "team_proposals": 6,
            "quests": 4,
        }

        second, _ = store.get_events(EXPECTED_IDS[1], limit=1000)
        players = {event.agent_id: event for event in second if event.agent_id is not None}
        assert players
        for event in second:
            if event.agent_id is None:
                continue
            assert event.role is not None
            assert set(event.metadata["_retrace"]["agent"]) == {"model", "provider"}

        repaired = next(
            event
            for event in second
            if event.metadata["_retrace"]["source"] == "quests"
            and event.metadata["_retrace"]["source_ordinal"] == 4
        )
        assert repaired.turn == 5
        assert repaired.metadata["result"] == "success"
        assert repaired.metadata["_retrace"]["repaired"] == {"turn": 4, "result": "fail"}
        assert repaired.structured is not None
        assert repaired.structured["round"] == 4
        assert repaired.structured["result"] == "fail"
