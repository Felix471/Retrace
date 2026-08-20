"""Generate a deterministic synthetic customer-support fixture."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent
BASE_TIME = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)
MALFORMED_RUN_ID = "case-07"
MALFORMED_AFTER_SEQUENCES = (4, 9)
MALFORMED_LINES = (
    b'{"record_id":"broken-01","tokens_in":9,"tokens_out":4,"cost":0.000013',
    b'{"record_id":"broken-02","tokens_in":7,"tokens_out":2,"cost":}',
)


@dataclass(frozen=True)
class RunSpec:
    """Stable inputs used to build one support run."""

    run_id: str
    ticket_id: str
    outcome: str
    model_name: str
    routing_variant: str
    issue_area: str
    opening: str
    specialist_note: str


@dataclass(frozen=True)
class EventStep:
    """One step in the fixed three-handler workflow."""

    handler: str
    step_kind: str
    workflow_stage: str
    body: str
    operation_label: str | None = None
    tool_name: str | None = None
    tool_data: dict[str, object] | None = None


RUN_SPECS = (
    RunSpec(
        "case-01",
        "TKT-1001",
        "resolved",
        "support-lite-v1",
        "standard",
        "duplicate_charge",
        "A customer reports a duplicate card charge.",
        "The duplicate entry is pending and can be released automatically.",
    ),
    RunSpec(
        "case-02",
        "TKT-1002",
        "resolved",
        "support-plus-v1",
        "context_enriched",
        "access_recovery",
        "A customer cannot access the account after changing devices.",
        "Identity checks passed and a secure access link can be issued.",
    ),
    RunSpec(
        "case-03",
        "TKT-1003",
        "escalated",
        "support-plus-v1",
        "priority_gate",
        "shipment_delay",
        "A time-sensitive shipment has not moved for three days.",
        "Carrier data is inconsistent, so an operations handoff is needed.",
    ),
    RunSpec(
        "case-04",
        "TKT-1004",
        "resolved",
        "support-lite-v1",
        "standard",
        "plan_change",
        "A customer wants to move to a monthly service plan.",
        "The plan is eligible for an immediate change without a fee.",
    ),
    RunSpec(
        "case-05",
        "TKT-1005",
        "escalated",
        "support-plus-v1",
        "priority_gate",
        "damaged_delivery",
        "A delivered device arrived with visible casing damage.",
        "The evidence needs manual warranty assessment before replacement.",
    ),
    RunSpec(
        "case-06",
        "TKT-1006",
        "resolved",
        "support-lite-v1",
        "context_enriched",
        "invoice_copy",
        "A customer needs an accessible copy of a recent invoice.",
        "The invoice is available and can be sent through the secure portal.",
    ),
    RunSpec(
        "case-07",
        "TKT-1007",
        "escalated",
        "support-plus-v1",
        "priority_gate",
        "identity_check",
        "An account profile contains conflicting identity details.",
        "Automated checks disagree, so a trained analyst must inspect the case.",
    ),
    RunSpec(
        "case-08",
        "TKT-1008",
        "resolved",
        "support-lite-v1",
        "standard",
        "alert_settings",
        "A customer is receiving alerts through an outdated channel.",
        "The preferred channel is verified and the alert profile can be updated.",
    ),
    RunSpec(
        "case-09",
        "TKT-1009",
        "resolved",
        "support-plus-v1",
        "context_enriched",
        "refund_status",
        "A customer cannot see the status of a recent refund.",
        "The refund cleared today and its reference can be shared safely.",
    ),
    RunSpec(
        "case-10",
        "TKT-1010",
        "resolved",
        "support-lite-v1",
        "standard",
        "address_update",
        "A customer needs to update the address on an open order.",
        "Fulfillment has not started, so the address can be changed now.",
    ),
)


def _event_steps(spec: RunSpec) -> tuple[EventStep, ...]:
    disposition = (
        "The case is approved for an automated resolution."
        if spec.outcome == "resolved"
        else "The case is assigned to a human operations team."
    )
    policy_result = "approved" if spec.outcome == "resolved" else "manual_check"
    return (
        EventStep("triage", "message", "intake", spec.opening),
        EventStep(
            "triage",
            "tool_call",
            "intake",
            "Looking up the best support route.",
            "route",
            "route_index",
            {"issue_area": spec.issue_area},
        ),
        EventStep(
            "triage",
            "tool_result",
            "intake",
            "The support route was found.",
            "route",
            "route_index",
            {"destination": spec.issue_area, "confidence": 0.96},
        ),
        EventStep(
            "triage",
            "message",
            "handoff",
            "The case is ready for specialist analysis.",
        ),
        EventStep(
            "specialist",
            "message",
            "analysis",
            "The specialist is reviewing the available account context.",
        ),
        EventStep(
            "specialist",
            "tool_call",
            "analysis",
            "Loading synthetic account context.",
            "context",
            "profile_store",
            {"ticket_id": spec.ticket_id},
        ),
        EventStep(
            "specialist",
            "tool_result",
            "analysis",
            "Synthetic account context was loaded.",
            "context",
            "profile_store",
            {"records_found": 3, "source": "fixture"},
        ),
        EventStep("specialist", "message", "analysis", spec.specialist_note),
        EventStep(
            "reviewer",
            "message",
            "quality_review",
            "The proposed action is being checked for policy and quality.",
        ),
        EventStep(
            "reviewer",
            "tool_call",
            "quality_review",
            "Checking the proposed action against support policy.",
            "policy",
            "policy_matrix",
            {"issue_area": spec.issue_area, "outcome": spec.outcome},
        ),
        EventStep(
            "reviewer",
            "tool_result",
            "quality_review",
            "The policy check completed.",
            "policy",
            "policy_matrix",
            {"decision": policy_result},
        ),
        EventStep("reviewer", "message", "disposition", disposition),
    )


def _timestamp(run_index: int, sequence: int) -> str:
    value = BASE_TIME + timedelta(days=run_index - 1, minutes=run_index * 7, seconds=sequence * 19)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _event(spec: RunSpec, run_index: int, sequence: int, step: EventStep) -> dict[str, object]:
    tokens_in = 26 + run_index * 3 + sequence * 2
    tokens_out = 7 + run_index + sequence
    cost = (tokens_in * 2 + tokens_out * 5) / 1_000_000
    event: dict[str, object] = {
        "body": step.body,
        "cost": cost,
        "handler": step.handler,
        "occurred_at": _timestamp(run_index, sequence),
        "record_id": f"{spec.run_id}-event-{sequence:02d}",
        "sequence": sequence,
        "step_kind": step.step_kind,
        "ticket_id": spec.ticket_id,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "workflow_stage": step.workflow_stage,
    }
    if step.operation_label is not None:
        event["operation_id"] = f"{spec.run_id}-{step.operation_label}"
    if step.tool_name is not None:
        event["tool_name"] = step.tool_name
    if step.tool_data is not None:
        event["tool_data"] = step.tool_data
    return event


def _encode_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _events_bytes(spec: RunSpec, run_index: int) -> bytes:
    lines: list[bytes] = []
    malformed_index = 0
    for sequence, step in enumerate(_event_steps(spec), start=1):
        lines.append(_encode_json(_event(spec, run_index, sequence, step)))
        if spec.run_id == MALFORMED_RUN_ID and sequence in MALFORMED_AFTER_SEQUENCES:
            lines.append(MALFORMED_LINES[malformed_index])
            malformed_index += 1
    return b"\n".join(lines) + b"\n"


def _manifest_bytes(spec: RunSpec) -> bytes:
    manifest: dict[str, object] = {
        "issue_area": spec.issue_area,
        "model_name": spec.model_name,
        "outcome": spec.outcome,
        "routing_variant": spec.routing_variant,
        "run_id": spec.run_id,
    }
    return _encode_json(manifest) + b"\n"


def _run_directory(output_dir: Path, run_id: str) -> Path:
    run_dir = output_dir / run_id
    output_root = output_dir.resolve()
    resolved_run_dir = run_dir.resolve()
    if not resolved_run_dir.is_relative_to(output_root):
        raise ValueError(f"Run directory escapes the output directory: {run_dir}")
    if run_dir.is_symlink():
        raise ValueError(f"Run directory must not be a symbolic link: {run_dir}")
    if run_dir.exists() and not run_dir.is_dir():
        raise NotADirectoryError(run_dir)
    return run_dir


def _write_artifact(output_dir: Path, path: Path, contents: bytes) -> None:
    if path.is_symlink():
        raise ValueError(f"Output file must not be a symbolic link: {path}")
    if not path.resolve().is_relative_to(output_dir.resolve()):
        raise ValueError(f"Output file escapes the output directory: {path}")
    if path.exists() and not path.is_file():
        raise IsADirectoryError(path)
    path.write_bytes(contents)


def generate_fixture(output_dir: Path) -> None:
    """Write all deterministic fixture artifacts beneath ``output_dir``."""
    if output_dir.exists() and not output_dir.is_dir():
        raise NotADirectoryError(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for run_index, spec in enumerate(RUN_SPECS, start=1):
        run_dir = _run_directory(output_dir, spec.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_artifact(
            output_dir,
            run_dir / "events.jsonl",
            _events_bytes(spec, run_index),
        )
        _write_artifact(output_dir, run_dir / "meta.json", _manifest_bytes(spec))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="directory that will contain the generated run directories",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the fixture generator command-line interface."""
    args = _build_parser().parse_args(argv)
    generate_fixture(args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
