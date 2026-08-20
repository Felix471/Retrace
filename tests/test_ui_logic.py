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
