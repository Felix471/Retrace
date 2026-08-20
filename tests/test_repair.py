from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jmespath
import pytest
import yaml

from retrace.adapters.mapping_schema import (
    EventSourceConfig,
    MappingConfigError,
    RepairRule,
    validate_mapping_config,
)
from retrace.adapters.multisource import MultiSourceEvent, MultiSourceExtractor
from retrace.adapters.repair import RepairPlan

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


def _validated_source(
    repairs: list[dict[str, object]],
    *,
    fields: dict[str, object] | None = None,
) -> EventSourceConfig:
    config = validate_mapping_config(
        {
            "retrace_mapping": 1,
            "run_discovery": {"pattern": "records/*.jsonl"},
            "run": {"id": "{file_stem}"},
            "event": {
                "sources": [
                    {
                        "name": "entries",
                        "path": "entries",
                        "fields": fields or {},
                        "repairs": repairs,
                    }
                ]
            },
        }
    )
    assert config.event.sources is not None
    return config.event.sources[0]


def _rules(
    repairs: list[dict[str, object]],
    *,
    fields: dict[str, object] | None = None,
) -> list[RepairRule]:
    source = _validated_source(repairs, fields=fields)
    return list(source.repairs)


def _provenance(event: MultiSourceEvent) -> dict[str, object]:
    value = event.metadata["_retrace"]
    assert isinstance(value, dict)
    return value


def test_ordinal_repair_reports_typed_values_originals_and_flags() -> None:
    plan = RepairPlan(
        _rules([{"field": "turn", "strategy": "ordinal", "base": 1}]),
        mapped_slots={"turn": "sequence"},
    )
    record = {"sequence": 4, "payload": {"kept": True}}
    original = copy.deepcopy(record)

    result = plan.apply(record, 4, slot_values={"turn": 4})

    assert result.slot_values["turn"] == 5
    assert result.repaired_values == {"turn": 5}
    assert result.originals == {"turn": 4}
    assert result.fired == (True,)
    assert result.evaluation_failures == (False,)
    assert result.fired_slot_fields == frozenset({"turn"})
    assert result.record_view == {"sequence": 5, "payload": {"kept": True}}
    assert record == original


@pytest.mark.parametrize(
    ("stored", "computed"),
    ((5, 5), (True, 1), (1, 1.0)),
    ids=("same-int", "plain-bool-int-equality", "plain-int-float-equality"),
)
def test_plain_equal_values_do_not_fire(stored: object, computed: object) -> None:
    plan = RepairPlan(
        _rules([{"field": "value", "strategy": "derive", "expr": "computed"}])
    )

    result = plan.apply(
        {"value": stored, "computed": computed},
        0,
        metadata={"value": stored},
    )

    assert result.metadata["value"] == stored
    assert result.originals == {}
    assert result.repaired_values == {}
    assert result.fired == (False,)
    assert not result.record_repaired


def test_typed_slot_equality_ignores_raw_source_spelling() -> None:
    plan = RepairPlan(
        _rules([{"field": "turn", "strategy": "ordinal", "base": 1}]),
        mapped_slots={"turn": "sequence"},
    )

    result = plan.apply({"sequence": "5"}, 4, slot_values={"turn": 5})

    assert result.fired == (False,)
    assert result.originals == {}
    assert result.record_view == {"sequence": "5"}


def test_missing_metadata_target_is_filled_and_preserves_none() -> None:
    plan = RepairPlan(
        _rules([{"field": "label", "strategy": "derive", "expr": "computed"}])
    )

    result = plan.apply({"computed": "filled"}, 0)

    assert result.metadata["label"] == "filled"
    assert result.originals == {"label": None}
    assert result.repaired_values == {"label": "filled"}
    assert result.record_view == {"computed": "filled", "label": "filled"}
    assert result.fired == (True,)


def test_boolean_yaml_map_keys_match_derived_booleans() -> None:
    parsed_map = yaml.safe_load("{true: accepted, false: declined}")
    rules = _rules(
        [
            {
                "field": "status",
                "strategy": "derive",
                "expr": "enabled",
                "map": parsed_map,
            }
        ]
    )
    assert rules[0].map == {"True": "accepted", "False": "declined"}
    plan = RepairPlan(rules)

    result = plan.apply(
        {"status": "declined", "enabled": True},
        0,
        metadata={"status": "declined"},
    )

    assert result.metadata["status"] == "accepted"
    assert result.originals == {"status": "declined"}
    assert result.fired == (True,)


def test_unmapped_derive_result_is_an_evaluation_failure() -> None:
    plan = RepairPlan(
        _rules(
            [
                {
                    "field": "status",
                    "strategy": "derive",
                    "expr": "computed",
                    "map": {"ready": "accepted"},
                }
            ]
        )
    )

    result = plan.apply(
        {"status": "unchanged", "computed": "unknown"},
        0,
        metadata={"status": "unchanged"},
    )

    assert result.metadata["status"] == "unchanged"
    assert result.originals == {}
    assert result.fired == (False,)
    assert result.evaluation_failures == (True,)


def test_matched_map_value_none_is_a_valid_computed_value() -> None:
    plan = RepairPlan(
        _rules(
            [
                {
                    "field": "status",
                    "strategy": "derive",
                    "expr": "command",
                    "map": {"clear": None},
                }
            ]
        )
    )

    result = plan.apply(
        {"status": "old", "command": "clear"},
        0,
        metadata={"status": "old"},
    )

    assert result.metadata["status"] is None
    assert result.originals == {"status": "old"}
    assert result.fired == (True,)
    assert result.evaluation_failures == (False,)


def test_original_preservation_can_restore_the_record_view() -> None:
    plan = RepairPlan(
        _rules(
            [
                {"field": "turn", "strategy": "ordinal", "base": 1},
                {"field": "label", "strategy": "derive", "expr": "replacement"},
            ]
        ),
        mapped_slots={"turn": "sequence"},
    )
    record = {
        "sequence": 8,
        "label": "old",
        "replacement": "new",
        "nested": {"items": [1, 2]},
    }
    original = copy.deepcopy(record)

    result = plan.apply(
        record,
        0,
        slot_values={"turn": 8},
        metadata={"label": "old", "replacement": "new", "nested": record["nested"]},
    )
    restored = dict(result.record_view)  # type: ignore[arg-type]
    restored["sequence"] = result.originals["turn"]
    restored["label"] = result.originals["label"]

    assert result.originals == {"turn": 8, "label": "old"}
    assert result.slot_values["turn"] == 1
    assert result.metadata["label"] == "new"
    assert restored == original
    assert record == original


def test_derive_expressions_compile_once(monkeypatch: pytest.MonkeyPatch) -> None:
    rules = _rules(
        [
            {"field": "first", "strategy": "derive", "expr": "computed.first"},
            {"field": "position", "strategy": "ordinal", "base": 0},
            {"field": "second", "strategy": "derive", "expr": "computed.second"},
        ]
    )
    real_compile = jmespath.compile
    calls: list[str] = []

    def compile_spy(expression: str) -> Any:
        calls.append(expression)
        return real_compile(expression)

    monkeypatch.setattr(jmespath, "compile", compile_spy)
    plan = RepairPlan(rules)
    assert calls == ["computed.first", "computed.second"]

    plan.apply({"computed": {"first": 1, "second": 2}}, 0)
    plan.apply({"computed": {"first": 1, "second": 2}}, 0)

    assert calls == ["computed.first", "computed.second"]


def test_invalid_derive_expression_has_one_line_location() -> None:
    rules = _rules([{"field": "value", "strategy": "derive", "expr": "["}])

    with pytest.raises(MappingConfigError) as captured:
        RepairPlan(rules, error_prefix="event.sources[0].repairs")

    message = str(captured.value)
    assert message.startswith(
        "event.sources[0].repairs[0].expr: invalid JMESPath expression:"
    )
    assert "\n" not in message


def test_duplicate_repair_fields_are_rejected() -> None:
    rules = _rules(
        [
            {"field": "value", "strategy": "ordinal", "base": 0},
            {"field": "value", "strategy": "derive", "expr": "computed"},
        ]
    )

    with pytest.raises(
        MappingConfigError,
        match=(
            r"^repairs\[1\]\.field: duplicate repair field 'value'; "
            r"first used at index 0$"
        ),
    ):
        RepairPlan(rules)


def _multi_config(
    sources: list[dict[str, object]],
    *,
    merge: dict[str, object] | None = None,
) -> MultiSourceExtractor:
    event: dict[str, object] = {"sources": sources}
    if merge is not None:
        event["merge"] = merge
    config = validate_mapping_config(
        {
            "retrace_mapping": 1,
            "run_discovery": {"pattern": "records/*.jsonl"},
            "run": {"id": "{file_stem}"},
            "event": event,
        }
    )
    return MultiSourceExtractor(config)


def _fixture_extractor() -> MultiSourceExtractor:
    return _multi_config(
        [
            {
                "name": "quests",
                "path": "quests",
                "type": "other",
                "fields": {"turn": "round", "metadata": "rest"},
                "repairs": [
                    {"field": "turn", "strategy": "ordinal", "base": 1},
                    {
                        "field": "result",
                        "strategy": "derive",
                        "expr": "contains(values(actions), 'fail')",
                        "map": {True: "fail", False: "success"},
                    },
                ],
            }
        ],
        merge={"sort_by": "turn"},
    )


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("sample_path", SAMPLE_INPUTS)
def test_five_line_repair_matrix_and_cross_file_totals(sample_path: Path) -> None:
    expected = ((0, 0, 0), (1, 1, 1), (0, 0, 0), (0, 0, 0), (2, 0, 2))
    totals = [0, 0, 0]

    for line_index, (record, expected_counts) in enumerate(
        zip(_records(sample_path), expected, strict=True),
        start=1,
    ):
        extractor = _fixture_extractor()
        source_records = record["quests"]
        assert isinstance(source_records, list)
        events = extractor.extract_events(record)
        actual = (
            extractor.stats.repair_fire_counts[("quests", "turn")],
            extractor.stats.repair_fire_counts[("quests", "result")],
            extractor.stats.n_repaired,
        )
        assert actual == expected_counts
        assert extractor.stats.n_repaired_by_source["quests"] == expected_counts[2]
        totals = [left + right for left, right in zip(totals, actual, strict=True)]

        repaired_events = [event for event in events if "repaired" in _provenance(event)]
        assert len(repaired_events) == expected_counts[2]
        if line_index in (1, 3, 4):
            assert all("repaired" not in _provenance(event) for event in events)
            assert extractor.stats.total_warnings == 0
        if line_index == 2:
            original = source_records[4]
            assert isinstance(original, dict)
            repaired = next(
                event
                for event in events
                if _provenance(event)["source_ordinal"] == 4
            )
            assert repaired.turn == 5
            assert repaired.metadata["result"] == "success"
            assert _provenance(repaired)["repaired"] == {
                "turn": 4,
                "result": "fail",
            }
            assert repaired.structured is original
            repaired_view = json.loads(repaired.content)
            assert repaired_view["round"] == 5
            assert repaired_view["result"] == "success"
            assert original["round"] == 4
            assert original["result"] == "fail"
            assert extractor.stats.total_warnings == 1
        if line_index == 5:
            by_source_ordinal = {
                int(_provenance(event)["source_ordinal"]): event for event in repaired_events
            }
            assert by_source_ordinal[2].turn == 3
            assert by_source_ordinal[3].turn == 4
            assert extractor.stats.total_warnings == 2

    assert tuple(totals) == (3, 1, 3)


def test_never_firing_rule_is_visible_without_warnings_or_marker() -> None:
    extractor = _multi_config(
        [
            {
                "name": "entries",
                "path": "entries",
                "fields": {"turn": "sequence", "content": "body"},
                "repairs": [{"field": "turn", "strategy": "ordinal", "base": 1}],
            }
        ]
    )

    event = extractor.extract_events(
        {"entries": [{"sequence": 1, "body": "unchanged"}]}
    )[0]

    assert extractor.stats.repair_fire_counts[("entries", "turn")] == 0
    assert extractor.stats.repair_evaluation_failures[("entries", "turn")] == 0
    assert extractor.stats.n_repaired == 0
    assert extractor.stats.total_warnings == 0
    assert "repaired" not in _provenance(event)
    assert event.structured is None


def test_evaluation_failure_is_counted_without_changing_the_event() -> None:
    extractor = _multi_config(
        [
            {
                "name": "entries",
                "path": "entries",
                "fields": {"metadata": "rest"},
                "repairs": [
                    {
                        "field": "status",
                        "strategy": "derive",
                        "expr": "computed",
                        "map": {"ready": "accepted"},
                    }
                ],
            }
        ]
    )

    event = extractor.extract_events(
        {"entries": [{"status": "old", "computed": "unknown"}]}
    )[0]

    assert event.metadata["status"] == "old"
    assert "repaired" not in _provenance(event)
    assert extractor.stats.repair_fire_counts[("entries", "status")] == 0
    assert extractor.stats.repair_evaluation_failures[("entries", "status")] == 1
    assert extractor.stats.n_repaired == 0
    assert extractor.stats.total_warnings == 1


def test_missing_mapped_slot_is_filled_and_counted_integration() -> None:
    extractor = _multi_config(
        [
            {
                "name": "entries",
                "path": "entries",
                "fields": {"turn": "sequence"},
                "repairs": [{"field": "turn", "strategy": "ordinal", "base": 1}],
            }
        ]
    )
    original = {"body": "missing sequence"}

    event = extractor.extract_events({"entries": [original]})[0]

    assert event.turn == 1
    assert _provenance(event)["repaired"] == {"turn": None}
    assert event.structured is original
    assert extractor.stats.repair_fire_counts[("entries", "turn")] == 1
    assert extractor.stats.n_repaired == 1
    assert extractor.stats.total_warnings == 1


def test_pretty_content_uses_repaired_view_and_structured_keeps_original() -> None:
    extractor = _multi_config(
        [
            {
                "name": "entries",
                "path": "entries",
                "fields": {"turn": "sequence", "metadata": "rest"},
                "repairs": [
                    {"field": "turn", "strategy": "ordinal", "base": 1},
                    {"field": "label", "strategy": "derive", "expr": "replacement"},
                ],
            }
        ]
    )
    original = {"sequence": 9, "label": "old", "replacement": "new"}

    event = extractor.extract_events({"entries": [original]})[0]

    assert event.turn == 1
    assert event.metadata["label"] == "new"
    assert _provenance(event)["repaired"] == {"turn": 9, "label": "old"}
    assert json.loads(event.content) == {
        "sequence": 1,
        "label": "new",
        "replacement": "new",
    }
    assert event.structured is original
    assert original == {"sequence": 9, "label": "old", "replacement": "new"}
    assert extractor.stats.repair_fire_counts == {
        ("entries", "turn"): 1,
        ("entries", "label"): 1,
    }
    assert extractor.stats.n_repaired == 1
    assert extractor.stats.total_warnings == 1


def test_repaired_content_slot_is_used_for_a_scalar_source_record() -> None:
    extractor = _multi_config(
        [
            {
                "name": "entries",
                "path": "entries",
                "fields": {"content": "payload"},
                "repairs": [
                    {
                        "field": "content",
                        "strategy": "derive",
                        "expr": "@",
                        "map": {"raw": "repaired text"},
                    }
                ],
            }
        ]
    )

    event = extractor.extract_events({"entries": ["raw"]})[0]

    assert event.content == "repaired text"
    assert event.structured is None
    assert _provenance(event)["repaired"] == {"content": None}
    assert extractor.stats.repair_fire_counts[("entries", "content")] == 1
    assert extractor.stats.n_repaired == 1
    assert extractor.stats.total_warnings == 1
