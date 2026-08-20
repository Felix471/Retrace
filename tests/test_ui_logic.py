from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOGIC = ROOT / "src" / "retrace" / "ui" / "logic.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _call(function: str, *arguments: object) -> Any:
    script = """
const logic = await import(process.argv[1]);
const request = JSON.parse(await new Promise(resolve => {
  let value = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", chunk => value += chunk);
  process.stdin.on("end", () => resolve(value));
}));
process.stdout.write(JSON.stringify(logic[request.function](...request.arguments)));
"""
    completed = subprocess.run(
        [
            NODE or "node",
            "--experimental-default-type=module",
            "--input-type=module",
            "-e",
            script,
            LOGIC.as_uri(),
        ],
        input=json.dumps({"function": function, "arguments": arguments}),
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _call_with_inputs(function: str, *arguments: object) -> dict[str, Any]:
    script = """
const logic = await import(process.argv[1]);
const request = JSON.parse(await new Promise(resolve => {
  let value = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", chunk => value += chunk);
  process.stdin.on("end", () => resolve(value));
}));
const result = logic[request.function](...request.arguments);
process.stdout.write(JSON.stringify({result, arguments: request.arguments}));
"""
    completed = subprocess.run(
        [NODE or "node", "--experimental-default-type=module", "--input-type=module", "-e", script, LOGIC.as_uri()],
        input=json.dumps({"function": function, "arguments": arguments}),
        encoding="utf-8", capture_output=True, check=True,
    )
    return json.loads(completed.stdout)


def test_group_by_turn_preserves_order_and_boundaries() -> None:
    events = [
        {"id": "a", "turn": 2},
        {"id": "b", "turn": 2},
        {"id": "c", "turn": 1},
        {"id": "d", "turn": 2},
    ]
    assert _call("groupByTurn", events) == [
        {"turn": 2, "events": events[:2]},
        {"turn": 1, "events": events[2:3]},
        {"turn": 2, "events": events[3:]},
    ]


def test_lane_assignment() -> None:
    assert _call("laneFor", "beta", ["alpha", "beta"]) == 1
    assert _call("laneFor", "overflow", ["alpha", "beta"]) == 2
    assert _call("laneFor", None, ["alpha", "beta"]) is None


def test_preview_edges_and_flattening() -> None:
    assert _call("previewOf", "12345", 5) == "12345"
    assert _call("previewOf", "123456", 5) == "12..."
    assert _call("previewOf", "  first\n  second\tthird  ", 30) == "first second third"


def test_hash_state_round_trip_and_missing_fields() -> None:
    state = {"runId": "run / one", "agent": "A&B", "phase": "round 1", "type": "tool", "q": "caf\u00e9 & tea"}
    encoded = _call("serializeHashState", state)
    assert _call("parseHashState", encoded) == state
    assert _call("parseHashState", "#/run/simple") == {
        "runId": "simple", "agent": None, "phase": None, "type": None, "q": None,
    }
    assert _call("parseHashState", "#elsewhere") == {
        "runId": None, "agent": None, "phase": None, "type": None, "q": None,
    }


@pytest.mark.parametrize("field", ["content", "agent_id", "role", "phase"])
def test_matches_search_across_fields(field: str) -> None:
    event = {"content": "", "agent_id": None, "role": None, "phase": None, field: "MiXeD value"}
    assert _call("matchesSearch", event, "mixed") is True
    assert _call("matchesSearch", event, "absent") is False
    assert _call("matchesSearch", event, "") is True


@pytest.mark.parametrize(
    ("text", "query", "expected_matches"),
    [
        ("nothing", "xyz", []),
        ("one ONE two", "one", ["one", "ONE"]),
        ("aaaa", "aa", ["aa", "aa"]),
        ("cat middle cat", "cat", ["cat", "cat"]),
        ("MixEd", "mixed", ["MixEd"]),
    ],
)
def test_highlight_segments(text: str, query: str, expected_matches: list[str]) -> None:
    segments = _call("highlightSegments", text, query)
    assert "".join(segment["text"] for segment in segments) == text
    assert [segment["text"] for segment in segments if segment["match"]] == expected_matches


def test_highlight_segments_empty_query() -> None:
    assert _call("highlightSegments", "Text", "") == [{"text": "Text", "match": False}]


def test_table_hash_state_round_trip_and_defaults() -> None:
    state = {
        "sort": "total_cost", "order": "desc", "outcome": "done & checked",
        "metadataKey": "group/name", "metadataValue": "A+B", "columns": ["group/name", "seed"],
        "groupBy": "model_name", "offset": 400,
    }
    encoded = _call("serializeTableHashState", state)
    assert _call("parseTableHashState", encoded) == state
    assert _call("parseTableHashState", "") == {
        "sort": "id", "order": "asc", "outcome": None, "metadataKey": None,
        "metadataValue": None, "groupBy": None, "columns": [], "offset": 0,
    }


def test_table_hash_state_without_grouping_remains_stable() -> None:
    state = _call("parseTableHashState", "#/?sort=n_turns&column=seed")
    assert state["groupBy"] is None
    assert _call("parseTableHashState", _call("serializeTableHashState", state)) == state


def test_outcome_bar_segments_are_proportional_and_ordered() -> None:
    assert _call("outcomeBarSegments", {"won": 1, "lost": 3}, 100) == [
        {"label": "won", "count": 1, "x": 0, "width": 25, "colorIndex": 0},
        {"label": "lost", "count": 3, "x": 25, "width": 75, "colorIndex": 1},
    ]


def test_outcome_bar_segments_absorb_remainder_and_omit_zeroes() -> None:
    segments = _call("outcomeBarSegments", {"a": 1, "unused": 0, "b": 1, "c": 1}, 10)
    assert [segment["label"] for segment in segments] == ["a", "b", "c"]
    assert sum(segment["width"] for segment in segments) == 10
    assert segments[-1]["x"] + segments[-1]["width"] == 10


def test_outcome_bar_single_outcome_fills_width() -> None:
    assert _call("outcomeBarSegments", {"only": 7}, 83)[0]["width"] == 83


def test_distribution_bars_scale_and_label_counts() -> None:
    modes = [
        {"id": "1.1", "runs_with_tag": 2, "total_tags": 3},
        {"id": "1.2", "runs_with_tag": 1, "total_tags": 1},
        {"id": "1.3", "runs_with_tag": 0, "total_tags": 0},
    ]
    assert _call("distributionBars", modes, 200) == [
        {"id": "1.1", "width": 200, "label": "2 runs, 3 tags"},
        {"id": "1.2", "width": 100, "label": "1 runs, 1 tags"},
        {"id": "1.3", "width": 0, "label": "0 runs, 0 tags"},
    ]


def test_distribution_bars_all_zero_never_divides_by_zero() -> None:
    assert _call("distributionBars", [{"id": "1.1", "runs_with_tag": 0, "total_tags": 0}], 200) == [
        {"id": "1.1", "width": 0, "label": "0 runs, 0 tags"}
    ]


def test_group_by_category_orders_ids_and_preserves_categories() -> None:
    modes = [
        {"id": "2.1", "category": "second"}, {"id": "1.2", "category": "first"},
        {"id": "1.1", "category": "first"},
    ]
    assert _call("groupByCategory", modes) == [
        {"category": "first", "modes": [modes[2], modes[1]]},
        {"category": "second", "modes": [modes[0]]},
    ]


def test_group_value_of_present_missing_and_null() -> None:
    run = {"metadata": {"model": "small", "empty": None}}
    assert _call("groupValueOf", run, "model") == "small"
    assert _call("groupValueOf", run, "absent") is None
    assert _call("groupValueOf", run, "empty") is None


def test_toggle_column_add_remove_order_and_no_duplicates() -> None:
    assert _call("toggleColumn", ["first"], "second") == ["first", "second"]
    assert _call("toggleColumn", ["first", "second"], "first") == ["second"]
    assert _call("toggleColumn", ["first", "second"], "second") == ["first"]
    assert _call("toggleColumn", ["first", "first"], "second") == ["first", "second"]


def test_cycle_sort_direction_and_field_switch() -> None:
    assert _call("cycleSort", {"sort": "id", "order": "asc"}, "id") == {"sort": "id", "order": "desc"}
    assert _call("cycleSort", {"sort": "id", "order": "desc"}, "id") == {"sort": "id", "order": "asc"}
    assert _call("cycleSort", {"sort": "id", "order": "desc"}, "n_turns") == {"sort": "n_turns", "order": "asc"}


def test_table_cell_formatter() -> None:
    assert _call("formatCell", None, "duration_s") == ""
    assert _call("formatCell", 1.236, "duration_s") == "1.24"
    assert _call("formatCell", 0.123456, "total_cost") == "0.1235"
    assert _call("formatCell", 7, "n_events") == "7"
    assert _call("formatCell", ["a", 2], "metadata") == '["a",2]'


def test_resolve_anchors_handles_colons_loading_and_detached_passthrough() -> None:
    tag = {
        "event_ids": ["team:run:1", "team:run:8", "team:run:99"],
        "detached_event_ids": ["team:run:99"],
    }
    assert _call("resolveAnchors", tag, [0, 1, 2], 10) == {
        "anchored": ["team:run:1", "team:run:8"],
        "detachedFromApi": ["team:run:99"],
        "needsLoad": [8],
    }


def test_toggle_selection_add_remove_order_and_deduplicate() -> None:
    assert _call("toggleSelection", ["a", "b"], "c") == ["a", "b", "c"]
    assert _call("toggleSelection", ["a", "b"], "a") == ["b"]
    assert _call("toggleSelection", ["a", "a"], "b") == ["a", "b"]


def test_tag_list_helpers_are_immutable() -> None:
    tags = [{"mode": "1.1"}, {"mode": "2.1"}]
    added = _call_with_inputs("tagListWith", tags, {"mode": "3.1"})
    assert added["result"] == [*tags, {"mode": "3.1"}]
    assert added["arguments"] == [tags, {"mode": "3.1"}]
    removed = _call_with_inputs("tagListWithout", tags, 0)
    assert removed["result"] == [tags[1]]
    assert removed["arguments"] == [tags, 0]
