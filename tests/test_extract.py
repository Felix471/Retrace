from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

import jmespath
import pytest

from retrace.adapters.extract import EventFields, Extractor, FieldStats, RunFields
from retrace.adapters.mapping_schema import MappingConfigError, validate_mapping_config
from retrace.adapters.multisource import MultiSourceExtractor


def _config(
    *,
    event: dict[str, object] | None = None,
    run: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "retrace_mapping": 1,
        "run_discovery": {"pattern": "records/*.jsonl"},
        "run": {"id": "{file_stem}", **(run or {})},
        "event": event or {},
    }


def _extractor(
    *,
    event: dict[str, object] | None = None,
    run: dict[str, object] | None = None,
) -> Extractor:
    return Extractor(validate_mapping_config(_config(event=event, run=run)))


def test_extracts_every_event_and_run_slot() -> None:
    extractor = _extractor(
        event={
            "where": "entry.visible",
            "turn": "entry.index",
            "timestamp": "entry.at",
            "agent_id": "identity.code",
            "role": "identity.capacity",
            "type": {
                "from": "entry.kind",
                "map": {"note": "message"},
                "default": "other",
            },
            "phase": "entry.segment",
            "content": "entry.body.text",
            "tokens_in": "metrics.prompt",
            "tokens_out": "metrics.response",
            "cost": "metrics.amount",
            "metadata": "rest",
        },
        run={
            "metadata": {"tier": "context.tier", "labels": "context.labels"},
            "outcome": "summary.state",
        },
    )
    retained = {"trace": [3, 5]}
    event = extractor.extract_event_fields(
        {
            "entry": {
                "visible": True,
                "index": "17",
                "at": "2026-08-19T12:34:56-04:00",
                "kind": "note",
                "segment": 3,
                "body": {"text": 88},
            },
            "identity": {"code": 4021, "capacity": "operator"},
            "metrics": {"prompt": 12.0, "response": "5", "amount": "0.03125"},
            "retained": retained,
        }
    )

    assert event == EventFields(
        turn=17,
        timestamp=datetime(2026, 8, 19, 16, 34, 56, tzinfo=UTC),
        agent_id="4021",
        role="operator",
        type="message",
        phase="3",
        content="88",
        tokens_in=12,
        tokens_out=5,
        cost=0.03125,
        metadata={"retained": retained},
    )
    assert event.metadata["retained"] is retained

    labels = ["blue", "small"]
    extracted_run = extractor.extract_run_fields(
        {"context": {"tier": "trial", "labels": labels}, "summary": {"state": 204}}
    )
    assert extracted_run == RunFields(
        metadata={"tier": "trial", "labels": labels},
        outcome="204",
    )
    assert extracted_run.metadata["labels"] is labels
    assert all(extractor.stats.fields[f"event.{slot}"].hits == 1 for slot in (
        "turn",
        "timestamp",
        "agent_id",
        "role",
        "type",
        "phase",
        "content",
        "tokens_in",
        "tokens_out",
        "cost",
    ))
    assert extractor.stats.fields["run.metadata.tier"].hits == 1
    assert extractor.stats.fields["run.metadata.labels"].hits == 1
    assert extractor.stats.fields["run.outcome"].hits == 1
    assert extractor.stats.total_warnings == 0


@pytest.mark.parametrize(
    ("slot", "expected"),
    (
        ("turn", None),
        ("timestamp", None),
        ("agent_id", None),
        ("role", None),
        ("type", "other"),
        ("phase", None),
        ("content", None),
        ("tokens_in", None),
        ("tokens_out", None),
        ("cost", None),
    ),
)
@pytest.mark.parametrize("record", ({}, {"value": None}), ids=("absent", "null"))
def test_mapped_missing_values_are_misses(
    slot: str,
    expected: object,
    record: dict[str, object],
) -> None:
    extractor = _extractor(event={slot: "value"})

    result = extractor.extract_event_fields(record)

    assert result is not None
    assert getattr(result, slot) == expected
    assert extractor.stats.for_slot(slot) == FieldStats(hits=0, misses=1, failures=0)
    assert extractor.stats.total_warnings == 0


@pytest.mark.parametrize(
    ("slot", "raw", "expected"),
    (
        ("turn", "not-numeric", None),
        ("timestamp", "not-a-time", None),
        ("agent_id", {"code": 1}, None),
        ("role", ["operator"], None),
        ("type", "unsupported", "other"),
        ("phase", {"name": "early"}, None),
        ("content", ["text"], None),
        ("tokens_in", True, None),
        ("tokens_out", 1.25, None),
        ("cost", False, None),
    ),
)
def test_type_mismatches_are_failures(
    slot: str,
    raw: object,
    expected: object,
) -> None:
    extractor = _extractor(event={slot: "value"})

    result = extractor.extract_event_fields({"value": raw})

    assert result is not None
    assert getattr(result, slot) == expected
    assert extractor.stats.for_slot(slot) == FieldStats(hits=0, misses=0, failures=1)
    assert extractor.stats.total_warnings == 1


def test_numeric_overflow_is_a_failure() -> None:
    extractor = _extractor(event={"cost": "value"})

    result = extractor.extract_event_fields({"value": 10**10_000})

    assert result is not None
    assert result.cost is None
    assert extractor.stats.for_slot("cost") == FieldStats(failures=1)
    assert extractor.stats.total_warnings == 1


@pytest.mark.parametrize(
    "raw",
    (float("nan"), float("inf"), "-inf", "Infinity"),
    ids=("nan-float", "infinity-float", "negative-infinity-string", "infinity-string"),
)
def test_non_finite_cost_is_a_failure(raw: object) -> None:
    extractor = _extractor(event={"cost": "value"})

    result = extractor.extract_event_fields({"value": raw})

    assert result is not None
    assert result.cost is None
    assert extractor.stats.for_slot("cost") == FieldStats(failures=1)
    assert extractor.stats.total_warnings == 1


def test_finite_cost_still_passes() -> None:
    extractor = _extractor(event={"cost": "value"})

    result = extractor.extract_event_fields({"value": "1.25"})

    assert result is not None
    assert result.cost == 1.25
    assert extractor.stats.for_slot("cost") == FieldStats(hits=1)


def test_non_finite_integer_is_a_failure() -> None:
    extractor = _extractor(event={"tokens_in": "value"})

    result = extractor.extract_event_fields({"value": float("inf")})

    assert result is not None
    assert result.tokens_in is None
    assert extractor.stats.for_slot("tokens_in") == FieldStats(failures=1)


@pytest.mark.parametrize("raw", (float("nan"), float("inf")), ids=("nan", "infinity"))
def test_non_finite_timestamp_is_a_failure(raw: float) -> None:
    extractor = _extractor(event={"timestamp": "value"})

    result = extractor.extract_event_fields({"value": raw})

    assert result is not None
    assert result.timestamp is None
    assert extractor.stats.for_slot("timestamp") == FieldStats(failures=1)


def test_multisource_non_finite_cost_is_a_failure() -> None:
    config = validate_mapping_config(
        _config(
            event={
                "sources": [
                    {"name": "neutral", "path": "items", "fields": {"cost": "amount"}}
                ]
            }
        )
    )
    extractor = MultiSourceExtractor(config)

    events = extractor.extract_events({"items": [{"amount": "Infinity"}]})

    assert len(events) == 1
    assert events[0].cost is None
    assert extractor.stats.for_slot("event.sources.neutral.cost") == FieldStats(failures=1)


@pytest.mark.parametrize(
    ("expression", "record", "expected"),
    (
        ("a.b.c", {"a": {"b": {"c": "deep"}}}, "deep"),
        (
            "items[-1].text",
            {"items": [{"text": "first"}, {"text": "last"}]},
            "last",
        ),
    ),
)
def test_nested_and_indexed_expressions(
    expression: str,
    record: dict[str, object],
    expected: str,
) -> None:
    extractor = _extractor(event={"content": expression})

    result = extractor.extract_event_fields(record)

    assert result is not None
    assert result.content == expected
    assert extractor.stats.for_slot("content").hits == 1


def test_unmapped_slots_keep_defaults_without_counter_movement() -> None:
    extractor = _extractor()

    result = extractor.extract_event_fields({"value": 8})
    extracted_run = extractor.extract_run_fields({"status": "ready"})

    assert result == EventFields(
        turn=None,
        timestamp=None,
        agent_id=None,
        role=None,
        type="other",
        phase=None,
        content=None,
        tokens_in=None,
        tokens_out=None,
        cost=None,
        metadata={},
    )
    assert extracted_run == RunFields(metadata={}, outcome=None)
    assert all(counter == FieldStats() for counter in extractor.stats.fields.values())
    assert extractor.stats.total_warnings == 0


def test_where_uses_jmespath_truthiness_and_filters_before_slots() -> None:
    extractor = _extractor(event={"where": "gate", "turn": "value"})
    rejected: list[dict[str, object]] = [
        {},
        {"gate": None},
        {"gate": False},
        {"gate": ""},
        {"gate": []},
        {"gate": {}},
    ]
    kept: list[dict[str, object]] = [
        {"gate": True, "value": "2"},
        {"gate": 0, "value": "2"},
        {"gate": 1, "value": "2"},
        {"gate": "yes", "value": "2"},
    ]

    assert all(extractor.extract_event_fields(record) is None for record in rejected)
    kept_results = [extractor.extract_event_fields(record) for record in kept]

    assert all(result is not None and result.turn == 2 for result in kept_results)
    assert extractor.stats.filtered_records == 6
    assert extractor.stats.filtered == 6
    assert extractor.stats.for_slot("turn") == FieldStats(hits=4, misses=0, failures=0)
    assert extractor.stats.total_warnings == 0


@pytest.mark.parametrize(
    ("type_config", "raw", "expected", "stats"),
    (
        (
            {"from": "kind", "map": {"note": "message"}},
            "note",
            "message",
            FieldStats(hits=1),
        ),
        (
            {"from": "kind", "map": {"note": "message"}, "default": "system"},
            "unknown",
            "system",
            FieldStats(hits=1),
        ),
        (
            {"from": "kind", "map": {"note": "message"}},
            "unknown",
            "other",
            FieldStats(failures=1),
        ),
        (
            {"from": "kind", "map": {"True": "tool_call"}},
            True,
            "tool_call",
            FieldStats(hits=1),
        ),
        (
            {"from": "kind", "map": {}, "default": "system"},
            None,
            "other",
            FieldStats(misses=1),
        ),
    ),
    ids=("mapped", "default", "no-default", "boolean-key", "null-does-not-default"),
)
def test_type_mapping_and_default_semantics(
    type_config: dict[str, object],
    raw: object,
    expected: str,
    stats: FieldStats,
) -> None:
    extractor = _extractor(event={"type": type_config})

    result = extractor.extract_event_fields({"kind": raw})

    assert result is not None
    assert result.type == expected
    assert extractor.stats.for_slot("type") == stats
    assert extractor.stats.total_warnings == stats.failures


def test_metadata_rest_excludes_consumed_leading_keys() -> None:
    event_config: dict[str, object] = {
        "where": "gate",
        "turn": "sequence",
        "agent_id": "identity.code",
        "content": "items[0].body",
        "type": {"from": "kind", "map": {"note": "message"}},
        "metadata": "rest",
    }
    extractor = _extractor(event=event_config)
    retained = {"nested": [1, 2]}
    record: dict[str, object] = {
        "gate": True,
        "sequence": 4,
        "identity": {"code": "x"},
        "items": [{"body": "hello"}],
        "kind": "note",
        "sequence_extra": 5,
        "identity_extra": 6,
        "items_extra": 7,
        "kind_extra": 8,
        "retained": retained,
    }

    result = extractor.extract_event_fields(record)

    assert result is not None
    assert result.metadata == {
        "gate": True,
        "sequence_extra": 5,
        "identity_extra": 6,
        "items_extra": 7,
        "kind_extra": 8,
        "retained": retained,
    }
    assert result.metadata["retained"] is retained

    without_rest = _extractor(event={key: value for key, value in event_config.items() if key != "metadata"})
    result_without_rest = without_rest.extract_event_fields(record)
    assert result_without_rest is not None
    assert result_without_rest.metadata == {}


def test_scripted_stats_are_exact_across_event_and_run_calls() -> None:
    extractor = _extractor(
        event={
            "where": "gate",
            "turn": "sequence",
            "agent_id": "actor",
            "type": "kind",
            "cost": "charge",
        },
        run={"metadata": {"group": "group"}, "outcome": "status"},
    )
    event_records: list[dict[str, object]] = [
        {
            "gate": True,
            "sequence": "1",
            "actor": 9,
            "kind": "message",
            "charge": "0.5",
        },
        {"gate": True, "actor": {}, "kind": "unknown", "charge": False},
        {"gate": False, "sequence": 2, "actor": "x", "kind": "message", "charge": 1},
        {"gate": 0, "sequence": "bad"},
    ]

    results = [extractor.extract_event_fields(record) for record in event_records]

    assert results[0] is not None
    assert results[1] is not None
    assert results[2] is None
    assert results[3] is not None
    expected = FieldStats(hits=1, misses=1, failures=1)
    for slot in ("turn", "agent_id", "type", "cost"):
        assert extractor.stats.for_slot(slot) == expected
    assert extractor.stats.filtered_records == 1
    assert extractor.stats.total_warnings == 4

    first_run = extractor.extract_run_fields({"group": ["a"], "status": 201})
    second_run = extractor.extract_run_fields({"status": {}})

    assert first_run == RunFields(metadata={"group": ["a"]}, outcome="201")
    assert second_run == RunFields(metadata={"group": None}, outcome=None)
    assert extractor.stats.fields["run.metadata.group"] == FieldStats(hits=1, misses=1)
    assert extractor.stats.fields["run.outcome"] == FieldStats(hits=1, failures=1)
    assert extractor.stats.total_warnings == 5


INVALID_EXPRESSIONS = (
    pytest.param({"where": "["}, {}, "event.where", id="where"),
    pytest.param({"turn": "["}, {}, "event.turn", id="turn"),
    pytest.param({"timestamp": "["}, {}, "event.timestamp", id="timestamp"),
    pytest.param({"agent_id": "["}, {}, "event.agent_id", id="agent-id"),
    pytest.param({"role": "["}, {}, "event.role", id="role"),
    pytest.param({"type": "["}, {}, "event.type", id="plain-type"),
    pytest.param({"type": {"from": "["}}, {}, "event.type.from", id="mapped-type"),
    pytest.param({"phase": "["}, {}, "event.phase", id="phase"),
    pytest.param({"content": "["}, {}, "event.content", id="content"),
    pytest.param({"tokens_in": "["}, {}, "event.tokens_in", id="tokens-in"),
    pytest.param({"tokens_out": "["}, {}, "event.tokens_out", id="tokens-out"),
    pytest.param({"cost": "["}, {}, "event.cost", id="cost"),
    pytest.param({}, {"metadata": {"group": "["}}, "run.metadata.group", id="run-metadata"),
    pytest.param({}, {"outcome": "["}, "run.outcome", id="run-outcome"),
)


@pytest.mark.parametrize(("event", "run", "path"), INVALID_EXPRESSIONS)
def test_invalid_expressions_raise_one_line_mapping_errors(
    event: dict[str, object],
    run: dict[str, object],
    path: str,
) -> None:
    config = validate_mapping_config(_config(event=event, run=run))

    with pytest.raises(MappingConfigError) as captured:
        Extractor(config)

    message = str(captured.value)
    assert message.startswith(f"{path}: invalid JMESPath expression:")
    assert "\n" not in message


def test_expressions_compile_once_per_configured_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event: dict[str, object] = {
        "where": "visible",
        "turn": "turn_value",
        "timestamp": "at",
        "agent_id": "actor",
        "role": "capacity",
        "type": {"from": "kind", "map": {"note": "message"}},
        "phase": "segment",
        "content": "body",
        "tokens_in": "usage.input",
        "tokens_out": "usage.output",
        "cost": "usage.amount",
    }
    run: dict[str, object] = {
        "id": "{path_token}",
        "metadata": {"tier": "context.tier", "group": "context.group"},
        "outcome": "summary.state",
    }
    config = validate_mapping_config(_config(event=event, run=run))
    real_compile = jmespath.compile
    calls: list[str] = []

    def compile_spy(expression: str) -> Any:
        calls.append(expression)
        return real_compile(expression)

    monkeypatch.setattr(jmespath, "compile", compile_spy)
    extractor = Extractor(config)
    expected_calls = Counter(
        [
            "visible",
            "turn_value",
            "at",
            "actor",
            "capacity",
            "kind",
            "segment",
            "body",
            "usage.input",
            "usage.output",
            "usage.amount",
            "context.tier",
            "context.group",
            "summary.state",
        ]
    )
    assert Counter(calls) == expected_calls
    call_count = len(calls)

    extractor.extract_event_fields({"visible": True})
    extractor.extract_event_fields({"visible": False})
    extractor.extract_run_fields({})

    assert len(calls) == call_count
