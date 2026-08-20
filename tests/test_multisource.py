from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jmespath
import pytest

from retrace.adapters.extract import FieldStats
from retrace.adapters.mapping_schema import MappingConfigError, validate_mapping_config
from retrace.adapters.multisource import MultiSourceEvent, MultiSourceExtractor

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_SAMPLE = REPO_ROOT / "fixtures" / "avalon_mini" / "games.jsonl"
LOCAL_SAMPLE = REPO_ROOT / "reference-logs" / "avalon_sample.jsonl"

SAMPLE_INPUTS = (
    pytest.param(COMMITTED_SAMPLE, id="committed-sample"),
    pytest.param(
        LOCAL_SAMPLE,
        id="local-sample",
        marks=pytest.mark.skipif(not LOCAL_SAMPLE.exists(), reason="local sample is absent"),
    ),
)


def _config(
    sources: list[dict[str, object]],
    *,
    merge: dict[str, object] | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {"sources": sources}
    if merge is not None:
        event["merge"] = merge
    return {
        "retrace_mapping": 1,
        "run_discovery": {"pattern": "records/*.jsonl"},
        "run": {"id": "{file_stem}"},
        "event": event,
    }


def _extractor(
    sources: list[dict[str, object]],
    *,
    merge: dict[str, object] | None = None,
) -> MultiSourceExtractor:
    return MultiSourceExtractor(validate_mapping_config(_config(sources, merge=merge)))


def _first_record(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as stream:
        return json.loads(stream.readline())


def _provenance(event: MultiSourceEvent) -> dict[str, object]:
    value = event.metadata["_retrace"]
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("sample_path", SAMPLE_INPUTS)
def test_sample_line_explodes_to_seventy_ordered_events(sample_path: Path) -> None:
    record = _first_record(sample_path)
    extractor = _extractor(
        [
            {
                "name": "discussions",
                "path": "discussions",
                "type": "message",
                "priority": 0,
                "fields": {
                    "turn": "round",
                    "timestamp": "timestamp",
                    "agent_id": "speakerId",
                    "phase": "phase",
                    "content": "content",
                    "metadata": "rest",
                },
            },
            {
                "name": "team_proposals",
                "path": "teamProposals",
                "type": "other",
                "phase": "proposal",
                "priority": 1,
                "fields": {
                    "turn": "questRound",
                    "agent_id": "proposedBy",
                    "metadata": "rest",
                },
            },
            {
                "name": "quests",
                "path": "quests",
                "type": "other",
                "phase": "quest",
                "priority": 2,
                "fields": {
                    "turn": "round",
                    "agent_id": "proposedBy",
                    "metadata": "rest",
                },
            },
        ],
        merge={"sort_by": "turn"},
    )

    events = extractor.extract_events(record)

    assert len(events) == 70
    assert [event.ordinal for event in events] == list(range(70))
    assert Counter(_provenance(event)["source"] for event in events) == {
        "discussions": 60,
        "team_proposals": 6,
        "quests": 4,
    }
    priorities = {"discussions": 0, "team_proposals": 1, "quests": 2}
    for turn in (1, 2, 3, 4):
        within_turn = [event for event in events if event.turn == turn]
        order = [
            (
                priorities[str(_provenance(event)["source"])],
                int(_provenance(event)["source_ordinal"]),
            )
            for event in within_turn
        ]
        assert order == sorted(order)

    arrays = {
        "discussions": record["discussions"],
        "team_proposals": record["teamProposals"],
        "quests": record["quests"],
    }
    for event in events:
        provenance = _provenance(event)
        source_name = str(provenance["source"])
        source_ordinal = int(provenance["source_ordinal"])
        source_array = arrays[source_name]
        assert isinstance(source_array, list)
        source_record = source_array[source_ordinal]
        assert isinstance(source_record, dict)
        if source_name == "discussions":
            assert event.content == source_record["content"]
            assert event.structured is None
            assert event.metadata == {"_retrace": provenance}
        else:
            assert event.content == json.dumps(source_record, indent=2, ensure_ascii=False)
            assert event.structured is source_record

    assert extractor.stats.records_in_source_arrays == 70
    assert extractor.stats.source_record_counts == {
        "discussions": 60,
        "team_proposals": 6,
        "quests": 4,
    }
    assert extractor.stats.none_sort_key_events == 0
    assert extractor.stats.total_warnings == 0


def test_none_sort_keys_follow_all_keyed_events() -> None:
    extractor = _extractor(
        [
            {
                "name": "later",
                "path": "alpha",
                "priority": 2,
                "fields": {"turn": "position", "content": "text"},
            },
            {
                "name": "earlier",
                "path": "beta",
                "priority": 0,
                "fields": {"turn": "position", "content": "text"},
            },
        ],
        merge={"sort_by": "turn"},
    )
    record = {
        "alpha": [{"position": 2, "text": "a2"}, {"text": "a-none"}],
        "beta": [{"position": 1, "text": "b1"}, {"text": "b-none"}],
    }

    events = extractor.extract_events(record)

    assert [(item.turn, _provenance(item)) for item in events] == [
        (1, {"source": "earlier", "source_ordinal": 0}),
        (2, {"source": "later", "source_ordinal": 0}),
        (None, {"source": "earlier", "source_ordinal": 1}),
        (None, {"source": "later", "source_ordinal": 1}),
    ]
    assert extractor.stats.none_sort_key_events == 2
    assert extractor.stats.for_source_slot("earlier", "turn") == FieldStats(
        hits=1, misses=1
    )
    assert extractor.stats.for_source_slot("later", "turn") == FieldStats(
        hits=1, misses=1
    )
    assert extractor.stats.total_warnings == 2


def test_repeated_extraction_is_deterministic_and_gapless() -> None:
    extractor = _extractor(
        [
            {
                "name": "alpha",
                "path": "left_items",
                "priority": 1,
                "fields": {"turn": "sequence", "content": "body"},
            },
            {
                "name": "beta",
                "path": "right_items",
                "priority": 0,
                "fields": {"turn": "sequence", "content": "body"},
            },
        ],
        merge={"sort_by": "turn"},
    )
    record = {
        "left_items": [{"sequence": 2, "body": "a"}],
        "right_items": [
            {"sequence": 2, "body": "b"},
            {"sequence": 1, "body": "c"},
        ],
    }

    first = extractor.extract_events(record)
    second = extractor.extract_events(record)

    assert first == second
    assert [item.ordinal for item in first] == list(range(len(first)))
    assert [item.ordinal for item in second] == list(range(len(second)))


def test_neutral_two_source_shape_merges_on_timestamps_with_exact_stats() -> None:
    extractor = _extractor(
        [
            {
                "name": "signals",
                "path": "stream_a",
                "priority": 1,
                "fields": {
                    "timestamp": "emitted",
                    "agent_id": "owner.code",
                    "content": "payload",
                },
            },
            {
                "name": "markers",
                "path": "stream_b",
                "priority": 0,
                "fields": {
                    "timestamp": "seen",
                    "agent_id": "author",
                    "content": "label",
                },
            },
        ],
        merge={"sort_by": "timestamp"},
    )
    record = {
        "stream_a": [
            {"emitted": "2026-08-19T12:00:02Z", "owner": {"code": 4}, "payload": 8},
            {"emitted": "2026-08-19T12:00:03Z", "owner": {"code": 5}, "payload": "a3"},
        ],
        "stream_b": [
            {"seen": "2026-08-19T12:00:01Z", "author": "x", "label": "b1"},
            {"seen": "2026-08-19T12:00:02Z", "author": "y", "label": "b2"},
        ],
    }

    events = extractor.extract_events(record)

    assert [event.content for event in events] == ["b1", "b2", "8", "a3"]
    assert [event.timestamp for event in events] == [
        datetime(2026, 8, 19, 12, 0, 1, tzinfo=UTC),
        datetime(2026, 8, 19, 12, 0, 2, tzinfo=UTC),
        datetime(2026, 8, 19, 12, 0, 2, tzinfo=UTC),
        datetime(2026, 8, 19, 12, 0, 3, tzinfo=UTC),
    ]
    assert [_provenance(event) for event in events] == [
        {"source": "markers", "source_ordinal": 0},
        {"source": "markers", "source_ordinal": 1},
        {"source": "signals", "source_ordinal": 0},
        {"source": "signals", "source_ordinal": 1},
    ]
    for source in ("signals", "markers"):
        assert extractor.stats.for_source_slot(source, "path") == FieldStats(hits=1)
        assert extractor.stats.for_source_slot(source, "timestamp") == FieldStats(hits=2)
        assert extractor.stats.for_source_slot(source, "agent_id") == FieldStats(hits=2)
        assert extractor.stats.for_source_slot(source, "content") == FieldStats(hits=2)
    assert extractor.stats.records_in_source_arrays == 4
    assert extractor.stats.total_warnings == 0


def test_fixed_literals_fill_misses_and_failures_but_hits_win() -> None:
    extractor = _extractor(
        [
            {
                "name": "entries",
                "path": "entries",
                "type": "system",
                "phase": "fixed-phase",
                "role": "fixed-role",
                "fields": {
                    "role": "actor.role",
                    "type": {
                        "from": "kind",
                        "map": {"call": "tool_call"},
                        "default": "message",
                    },
                    "content": "body",
                },
            }
        ]
    )
    record = {
        "entries": [
            {"actor": {"role": "mapped-role"}, "kind": "call", "body": "one"},
            {"kind": "unknown", "body": "two"},
            {"actor": {"role": {}}, "kind": None, "body": "three"},
        ]
    }

    events = extractor.extract_events(record)

    assert [event.role for event in events] == ["mapped-role", "fixed-role", "fixed-role"]
    assert [event.type for event in events] == ["tool_call", "message", "system"]
    assert [event.phase for event in events] == ["fixed-phase"] * 3
    assert extractor.stats.for_source_slot("entries", "role") == FieldStats(
        hits=1, misses=1, failures=1
    )
    assert extractor.stats.for_source_slot("entries", "type") == FieldStats(
        hits=2, misses=1
    )
    assert extractor.stats.total_warnings == 1


def test_no_merge_orders_only_by_priority_and_source_ordinal() -> None:
    extractor = _extractor(
        [
            {"name": "later", "path": "alpha", "priority": 3},
            {"name": "earlier", "path": "beta", "priority": 0},
        ]
    )
    record = {"alpha": [{"value": "a0"}, {"value": "a1"}], "beta": [7, 8]}

    events = extractor.extract_events(record)

    assert [_provenance(event) for event in events] == [
        {"source": "earlier", "source_ordinal": 0},
        {"source": "earlier", "source_ordinal": 1},
        {"source": "later", "source_ordinal": 0},
        {"source": "later", "source_ordinal": 1},
    ]
    assert [event.content for event in events] == ["7", "8", '{\n  "value": "a0"\n}', '{\n  "value": "a1"\n}']
    assert extractor.stats.none_sort_key_events == 0
    assert extractor.stats.total_warnings == 0


@pytest.mark.parametrize("keep_rest", (False, True), ids=("without-rest", "with-rest"))
def test_reserved_provenance_replaces_source_collision(keep_rest: bool) -> None:
    fields = {"metadata": "rest"} if keep_rest else {}
    extractor = _extractor(
        [{"name": "entries", "path": "entries", "fields": fields}]
    )
    retained = {"nested": [1]}
    source_record = {
        "_retrace": {"source": "spoofed", "source_ordinal": 99},
        "retained": retained,
    }

    event = extractor.extract_events({"entries": [source_record]})[0]

    assert event.metadata["_retrace"] == {"source": "entries", "source_ordinal": 0}
    if keep_rest:
        assert event.metadata["retained"] is retained
    else:
        assert event.metadata == {
            "_retrace": {"source": "entries", "source_ordinal": 0}
        }
    assert extractor.stats.provenance_collisions == 1
    assert extractor.stats.for_source_slot("entries", "metadata") == FieldStats(failures=1)
    assert extractor.stats.total_warnings == 1


def test_missing_invalid_arrays_and_non_object_records_update_exact_stats() -> None:
    extractor = _extractor(
        [
            {
                "name": "valid",
                "path": "items",
                "role": "fixed-role",
                "fields": {"agent_id": "value", "content": "@"},
            },
            {"name": "missing", "path": "absent"},
            {"name": "invalid", "path": "not_array"},
            {"name": "empty", "path": "empty_items"},
        ]
    )
    object_record = {"value": 1}

    events = extractor.extract_events(
        {
            "items": [object_record, "text", None],
            "not_array": {"value": 3},
            "empty_items": [],
        }
    )

    assert [event.content for event in events] == [
        json.dumps(object_record, indent=2, ensure_ascii=False),
        '"text"',
        "null",
    ]
    assert [event.structured for event in events] == [object_record, None, None]
    assert all(event.role == "fixed-role" for event in events)
    assert extractor.stats.records_in_source_arrays == 3
    assert extractor.stats.source_record_counts == {
        "valid": 3,
        "missing": 0,
        "invalid": 0,
        "empty": 0,
    }
    assert extractor.stats.sources_without_arrays == 2
    assert extractor.stats.for_source_slot("valid", "path") == FieldStats(hits=1)
    assert extractor.stats.for_source_slot("missing", "path") == FieldStats(misses=1)
    assert extractor.stats.for_source_slot("invalid", "path") == FieldStats(failures=1)
    assert extractor.stats.for_source_slot("empty", "path") == FieldStats(hits=1)
    assert extractor.stats.for_source_slot("valid", "agent_id") == FieldStats(
        hits=1, misses=2
    )
    assert extractor.stats.for_source_slot("valid", "content") == FieldStats(
        misses=2, failures=1
    )
    assert extractor.stats.total_warnings == 2


def test_content_hit_or_pretty_fallback_controls_structured_value() -> None:
    extractor = _extractor(
        [
            {
                "name": "entries",
                "path": "entries",
                "fields": {"content": "payload"},
            }
        ]
    )
    records = [
        {"payload": "hello"},
        {"payload": 7},
        {"other": "missing"},
        {"payload": ["not", "text"]},
    ]

    events = extractor.extract_events({"entries": records})

    assert [event.content for event in events[:2]] == ["hello", "7"]
    assert [event.structured for event in events[:2]] == [None, None]
    for event, source_record in zip(events[2:], records[2:], strict=True):
        assert event.content == json.dumps(source_record, indent=2, ensure_ascii=False)
        assert event.structured is source_record
    assert extractor.stats.for_source_slot("entries", "content") == FieldStats(
        hits=2, misses=1, failures=1
    )
    assert extractor.stats.total_warnings == 1


def test_flat_config_is_rejected() -> None:
    config = validate_mapping_config(
        {
            "retrace_mapping": 1,
            "run_discovery": {"pattern": "records/*.jsonl"},
            "run": {"id": "{file_stem}"},
            "event": {"content": "body"},
        }
    )

    with pytest.raises(
        MappingConfigError,
        match=r"^event\.sources: multi-source extractor requires the sources form$",
    ):
        MultiSourceExtractor(config)


INVALID_CONFIGS = (
    pytest.param(
        [{"name": "entries", "path": "["}],
        None,
        "event.sources[0].path",
        id="source-path",
    ),
    pytest.param(
        [{"name": "entries", "path": "entries", "fields": {"turn": "["}}],
        None,
        "event.sources[0].fields.turn",
        id="source-field",
    ),
    pytest.param(
        [
            {
                "name": "entries",
                "path": "entries",
                "fields": {"type": {"from": "["}},
            }
        ],
        None,
        "event.sources[0].fields.type.from",
        id="mapped-type",
    ),
    pytest.param(
        [{"name": "entries", "path": "entries"}],
        {"sort_by": "unknown_slot"},
        "event.merge.sort_by",
        id="unknown-sort-slot",
    ),
)


@pytest.mark.parametrize(("sources", "merge", "path"), INVALID_CONFIGS)
def test_invalid_compiled_surfaces_have_actionable_one_line_errors(
    sources: list[dict[str, object]],
    merge: dict[str, object] | None,
    path: str,
) -> None:
    config = validate_mapping_config(_config(sources, merge=merge))

    with pytest.raises(MappingConfigError) as captured:
        MultiSourceExtractor(config)

    message = str(captured.value)
    assert message.startswith(f"{path}:")
    assert "\n" not in message


def test_repaired_sort_slot_changes_merge_order() -> None:
    extractor = _extractor(
        [
            {
                "name": "repaired",
                "path": "changing",
                "priority": 1,
                "fields": {"turn": "sequence", "content": "body"},
                "repairs": [{"field": "turn", "strategy": "ordinal", "base": 1}],
            },
            {
                "name": "steady",
                "path": "stable",
                "priority": 0,
                "fields": {"turn": "sequence", "content": "body"},
            },
        ],
        merge={"sort_by": "turn"},
    )
    changing = [{"sequence": 9, "body": "a0"}, {"sequence": 8, "body": "a1"}]
    stable = [{"sequence": 1, "body": "b0"}]

    events = extractor.extract_events({"changing": changing, "stable": stable})

    assert [(event.turn, _provenance(event)) for event in events] == [
        (1, {"source": "steady", "source_ordinal": 0}),
        (1, {"source": "repaired", "source_ordinal": 0, "repaired": {"turn": 9}}),
        (2, {"source": "repaired", "source_ordinal": 1, "repaired": {"turn": 8}}),
    ]
    assert events[1].structured is changing[0]
    assert events[2].structured is changing[1]
    assert extractor.stats.repair_fire_counts[("repaired", "turn")] == 2
    assert extractor.stats.n_repaired_by_source == {"repaired": 2, "steady": 0}
    assert extractor.stats.n_repaired == 2
    assert extractor.stats.total_warnings == 2


def test_source_expressions_compile_once(monkeypatch: pytest.MonkeyPatch) -> None:
    config = validate_mapping_config(
        _config(
            [
                {
                    "name": "alpha",
                    "path": "left_items",
                    "fields": {"turn": "sequence", "content": "body"},
                },
                {
                    "name": "beta",
                    "path": "right_items",
                    "fields": {
                        "timestamp": "emitted",
                        "type": {"from": "kind", "map": {"note": "message"}},
                    },
                },
            ]
        )
    )
    real_compile = jmespath.compile
    calls: list[str] = []

    def compile_spy(expression: str) -> Any:
        calls.append(expression)
        return real_compile(expression)

    monkeypatch.setattr(jmespath, "compile", compile_spy)
    extractor = MultiSourceExtractor(config)
    expected = Counter(
        ["left_items", "sequence", "body", "right_items", "emitted", "kind"]
    )
    assert Counter(calls) == expected
    call_count = len(calls)

    extractor.extract_events({"left_items": [], "right_items": []})
    extractor.extract_events({"left_items": [], "right_items": []})

    assert len(calls) == call_count
