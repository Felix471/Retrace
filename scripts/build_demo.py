"""Build the deterministic Retrace demonstration corpus."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "demo"
BASE_TIME = datetime(2026, 2, 2, 9, 0, tzinfo=UTC)

CONFIG = """retrace_mapping: 1
run_discovery:
  pattern: support-demo-*
  unit: dir
  events_file: events.jsonl
run:
  id: "{dir_name}"
  manifest: meta.json
  metadata:
    issue_area: issue_area
    model_name: model_name
    routing_variant: routing_variant
  outcome: outcome
event:
  turn: sequence
  timestamp: occurred_at
  agent_id: handler
  type:
    from: step_kind
    map:
      message: message
      tool_call: tool_call
      tool_result: tool_result
    default: other
  phase: workflow_stage
  content: body
  tokens_in: tokens_in
  tokens_out: tokens_out
  cost: cost
  metadata: rest
"""


@dataclass(frozen=True)
class Step:
    handler: str
    kind: str
    stage: str
    body: str
    tool: str | None = None
    data: dict[str, object] | None = None


def _json(value: object, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


def _steps(index: int) -> list[Step]:
    issue = ("account_access", "delivery_update", "billing_inquiry", "plan_change")[
        (index - 1) % 4
    ]
    opening = f"Synthetic case {index:02d} needs help with {issue.replace('_', ' ')}."
    # Runs 05 and 06 deliberately share content; run 06 alone retries a lookup.
    if index in {5, 6}:
        issue = "delivery_update"
        opening = "Synthetic case needs a delivery status review."
    steps = [
        Step("triage", "message", "intake", opening),
        Step("triage", "tool_call", "intake", "Looking up the best support route.", "route_index", {"issue_area": issue}),
        Step("triage", "tool_result", "intake", "The support route was found.", "route_index", {"destination": issue}),
        Step("triage", "message", "handoff", "The case is ready for specialist analysis."),
        Step("specialist", "message", "analysis", "The specialist is reviewing synthetic context."),
        Step("specialist", "tool_call", "analysis", "Loading synthetic account context.", "profile_store", {"case_number": index}),
        Step("specialist", "tool_result", "analysis", "Synthetic account context was loaded.", "profile_store", {"records_found": 3}),
        Step("specialist", "message", "analysis", "The available details support the proposed next action."),
        Step("reviewer", "message", "quality_review", "The proposed action is being checked for quality."),
        Step("reviewer", "tool_call", "quality_review", "Checking the proposed action.", "policy_matrix", {"issue_area": issue}),
        Step("reviewer", "tool_result", "quality_review", "The action check completed.", "policy_matrix", {"decision": "complete"}),
        Step("reviewer", "message", "disposition", "The case has reached its recorded outcome."),
    ]
    if index == 6 or (index > 6 and index % 7 == 0):
        steps[6:6] = [
            Step("retry_worker", "tool_result", "analysis", "The first context lookup needs a retry.", "profile_store", {"status": "retry"}),
            Step("retry_worker", "tool_call", "analysis", "Retrying the synthetic context lookup.", "profile_store", {"attempt": 2}),
        ]
    target = 12 if index in {5, 6} else 10 + ((index * 3) % 7)
    if index == 6:
        target = 14
    if target < len(steps):
        steps = steps[: target - 1] + [steps[-1]]
    while len(steps) < target:
        position = len(steps) - 1
        steps.insert(position, Step("reviewer", "message", "quality_review", f"Quality checkpoint {position:02d} completed."))
    return steps


def _event(run_id: str, index: int, sequence: int, step: Step) -> dict[str, object]:
    timestamp = BASE_TIME + timedelta(days=(index - 1) // 5, minutes=index * 11, seconds=sequence * 17)
    tokens_in = 30 + index * 2 + sequence * 3
    tokens_out = 9 + index + sequence * 2
    value: dict[str, object] = {
        "body": step.body,
        "cost": (tokens_in * 2 + tokens_out * 5) / 1_000_000,
        "handler": step.handler,
        "occurred_at": timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "record_id": f"{run_id}-event-{sequence:02d}",
        "sequence": sequence,
        "step_kind": step.kind,
        "ticket_id": f"SYN-{2000 + index}",
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "workflow_stage": step.stage,
    }
    if step.tool:
        value.update(operation_id=f"{run_id}-{step.tool}-{sequence:02d}", tool_name=step.tool)
    if step.data is not None:
        value["tool_data"] = step.data
    return value


def _sidecar(run_id: str, index: int) -> dict[str, object]:
    modes = {3: "1.3", 8: "2.2", 14: "2.6", 25: "3.1", 33: "3.2"}
    mode = modes[index]
    return {
        "retrace_tags": 1,
        "run_id": run_id,
        "run_note": "Pre-tagged synthetic example.",
        "tags": [{
            "confidence": 0.8,
            "created_at": f"2026-02-{10 + index % 9:02d}T12:00:00Z",
            "event_ids": [f"{run_id}:0"],
            "mode": mode,
            "note": "Demonstration tag for the walkthrough.",
            "source": "demo-builder",
        }],
    }


def build(output: Path) -> None:
    """Write a clean, byte-stable demo dataset beneath *output*."""
    output.mkdir(parents=True, exist_ok=True)
    for child in output.glob("support-demo-*"):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
    (output / "retrace.yaml").write_text(CONFIG, encoding="ascii", newline="\n")
    outcomes = ("resolved", "escalated", "deferred")
    models = ("support-small-v2", "support-medium-v2", "support-large-v2")
    routes = ("standard", "context_enriched", "priority_gate")
    tagged = {3, 8, 14, 25, 33}
    for index in range(1, 41):
        run_id = f"support-demo-{index:02d}"
        run_dir = output / run_id
        run_dir.mkdir()
        issue = "delivery_update" if index in {5, 6} else (
            "account_access", "delivery_update", "billing_inquiry", "plan_change"
        )[(index - 1) % 4]
        manifest = {
            "issue_area": issue,
            "model_name": models[(index - 1) % len(models)],
            "outcome": outcomes[(index - 1) % len(outcomes)],
            "routing_variant": routes[((index - 1) // 2) % len(routes)],
            "run_id": run_id,
        }
        (run_dir / "meta.json").write_text(_json(manifest), encoding="ascii", newline="\n")
        events = "".join(
            _json(_event(run_id, index, sequence, step))
            for sequence, step in enumerate(_steps(index), 1)
        )
        (run_dir / "events.jsonl").write_text(events, encoding="ascii", newline="\n")
        if index in tagged:
            (run_dir / "retrace.json").write_text(
                _json(_sidecar(run_id, index), pretty=True), encoding="ascii", newline="\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    build(parser.parse_args().output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
