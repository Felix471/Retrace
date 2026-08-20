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
        text=True,
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


def test_repaired_fields() -> None:
    assert _call("repairedFields", {}) == []
    assert _call(
        "repairedFields", {"_retrace": {"repaired": {"turn": 4, "result": "fail"}}}
    ) == [
        {"field": "turn", "original": 4},
        {"field": "result", "original": "fail"},
    ]


def test_badge_classes() -> None:
    for value in ("message", "tool_call", "tool_result", "system", "other"):
        assert _call("badgeClassFor", value) == value
    assert _call("badgeClassFor", "custom") == "other"
