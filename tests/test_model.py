import json
from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone

import pytest

from retrace.core.model import (
    Event,
    Experiment,
    Run,
    coerce_event_type,
    parse_timestamp,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-04-17T19:15:29Z", datetime(2026, 4, 17, 19, 15, 29, tzinfo=UTC)),
        ("2026-04-17T15:15:29-04:00", datetime(2026, 4, 17, 19, 15, 29, tzinfo=UTC)),
        ("2026-04-17T19:15:29", datetime(2026, 4, 17, 19, 15, 29, tzinfo=UTC)),
        (1776453329, datetime(2026, 4, 17, 19, 15, 29, tzinfo=UTC)),
        (1776453329395, datetime(2026, 4, 17, 19, 15, 29, 395000, tzinfo=UTC)),
        (1776453329.25, datetime(2026, 4, 17, 19, 15, 29, 250000, tzinfo=UTC)),
    ],
)
def test_parse_timestamp(value: str | float, expected: datetime) -> None:
    parsed, warned = parse_timestamp(value)

    assert parsed == expected
    assert parsed is not None
    assert parsed.tzinfo is UTC
    assert warned is False


@pytest.mark.parametrize("value", [None, "not-a-timestamp"])
def test_parse_timestamp_warns_for_missing_or_invalid(value: str | None) -> None:
    assert parse_timestamp(value) == (None, True)


@pytest.mark.parametrize(
    "value",
    ["message", "tool_call", "tool_result", "system", "other"],
)
def test_coerce_event_type_accepts_valid_values(value: str) -> None:
    assert coerce_event_type(value) == (value, False)


@pytest.mark.parametrize("value", ["chat", "", None])
def test_coerce_event_type_warns_for_invalid_values(value: str | None) -> None:
    assert coerce_event_type(value) == ("other", True)


def _full_event() -> Event:
    return Event(
        id="run-a:2",
        run_id="run-a",
        ordinal=2,
        turn=1,
        timestamp=datetime(
            2026,
            4,
            17,
            21,
            15,
            29,
            tzinfo=timezone(timedelta(hours=2)),
        ),
        agent_id="agent-a",
        role="reviewer",
        type="tool_call",
        phase="analysis",
        content='{"query": "status"}',
        structured={"request": {"query": "status"}, "ok": True},
        tokens_in=12,
        tokens_out=7,
        cost=0.0125,
        refs=["run-a:0"],
        metadata={"_retrace": {"source": "primary"}, "label": "example"},
    )


def _full_run() -> Run:
    return Run(
        id="run-a",
        experiment_id="experiment-a",
        source_path="inputs/run-a.jsonl",
        metadata={"_retrace": {"origin": "fixture"}, "group": "baseline"},
        outcome="complete",
        started_at=datetime(2026, 4, 17, 19, 15, 29, tzinfo=UTC),
        ended_at=datetime(
            2026,
            4,
            17,
            15,
            16,
            29,
            tzinfo=timezone(timedelta(hours=-4)),
        ),
        duration_s=60.0,
        n_events=3,
        n_turns=2,
        agent_ids=["agent-a", "agent-b"],
        phases=["analysis", "review"],
        tokens_in=30,
        tokens_out=18,
        total_cost=0.025,
        ingest_warnings=2,
        n_repaired=1,
    )


def test_event_round_trip_with_all_fields() -> None:
    event = _full_event()

    data = event.to_dict()

    assert data["timestamp"] == "2026-04-17T19:15:29Z"
    assert data["metadata"] == event.metadata
    json.dumps(data)
    assert Event.from_dict(data) == event


def test_event_round_trip_with_optional_fields_none() -> None:
    event = Event(
        id="run-empty:0",
        run_id="run-empty",
        ordinal=0,
        turn=None,
        timestamp=None,
        agent_id=None,
        role=None,
        type="other",
        phase=None,
        content="",
        structured=None,
        tokens_in=None,
        tokens_out=None,
        cost=None,
    )

    data = event.to_dict()

    json.dumps(data)
    assert Event.from_dict(data) == event


def test_event_constructor_and_from_dict_coerce_unknown_type() -> None:
    constructor_data = vars(_full_event()).copy()
    constructor_data["type"] = "chat"
    data = _full_event().to_dict()
    data["type"] = "chat"

    assert Event(**constructor_data).type == "other"
    assert Event.from_dict(data).type == "other"


def test_run_round_trip() -> None:
    run = _full_run()

    data = run.to_dict()

    assert data["started_at"] == "2026-04-17T19:15:29Z"
    assert data["ended_at"] == "2026-04-17T19:16:29Z"
    json.dumps(data)
    assert Run.from_dict(data) == run


def test_experiment_round_trip() -> None:
    experiment = Experiment(
        id="experiment-a",
        root_path="inputs",
        adapter_ref="generic-adapter",
        runs=[_full_run()],
    )

    data = experiment.to_dict()

    assert isinstance(data["runs"][0], dict)
    json.dumps(data)
    assert Experiment.from_dict(data) == experiment


def test_mutable_defaults_are_independent() -> None:
    first_event = Event(
        id="run-a:0",
        run_id="run-a",
        ordinal=0,
        turn=None,
        timestamp=None,
        agent_id=None,
        role=None,
        type="other",
        phase=None,
        content="",
        structured=None,
        tokens_in=None,
        tokens_out=None,
        cost=None,
    )
    second_event = Event(
        id="run-b:0",
        run_id="run-b",
        ordinal=0,
        turn=None,
        timestamp=None,
        agent_id=None,
        role=None,
        type="other",
        phase=None,
        content="",
        structured=None,
        tokens_in=None,
        tokens_out=None,
        cost=None,
    )
    first_experiment = Experiment(id="a", root_path="a", adapter_ref="adapter")
    second_experiment = Experiment(id="b", root_path="b", adapter_ref="adapter")

    first_event.refs.append("run-a:1")
    first_event.metadata["label"] = "first"
    first_experiment.runs.append(_full_run())

    assert second_event.refs == []
    assert second_event.metadata == {}
    assert second_experiment.runs == []


def test_dataclass_field_names_are_exact() -> None:
    assert [item.name for item in fields(Event)] == [
        "id",
        "run_id",
        "ordinal",
        "turn",
        "timestamp",
        "agent_id",
        "role",
        "type",
        "phase",
        "content",
        "structured",
        "tokens_in",
        "tokens_out",
        "cost",
        "refs",
        "metadata",
    ]
    assert [item.name for item in fields(Run)] == [
        "id",
        "experiment_id",
        "source_path",
        "metadata",
        "outcome",
        "started_at",
        "ended_at",
        "duration_s",
        "n_events",
        "n_turns",
        "agent_ids",
        "phases",
        "tokens_in",
        "tokens_out",
        "total_cost",
        "ingest_warnings",
        "n_repaired",
    ]
    assert [item.name for item in fields(Experiment)] == [
        "id",
        "root_path",
        "adapter_ref",
        "runs",
    ]
