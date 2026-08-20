# Tagging

Retrace uses the Multi-Agent System Failure Taxonomy (MAST) from Cemri et al.
(2025), "Why Do Multi-Agent LLM Systems Fail?", arXiv:2503.13657. Its 14 modes
are grouped into three categories:

- Specification & system design: `1.1` Disobey task specification; `1.2`
  Disobey role specification; `1.3` Step repetition; `1.4` Loss of conversation
  history; `1.5` Unaware of termination conditions.
- Inter-agent misalignment: `2.1` Conversation reset; `2.2` Fail to ask for
  clarification; `2.3` Task derailment; `2.4` Information withholding; `2.5`
  Ignore other agent's input; `2.6` Reasoning-action mismatch.
- Task verification & termination: `3.1` Premature termination; `3.2` No or
  incomplete verification; `3.3` Incorrect verification.

In replay, select zero or more events, choose a mode, optionally add a note,
and add the tag. With anchoring enabled, selected event IDs become anchors;
without anchors the tag applies to the run. Existing tags can be inspected or
deleted. The batch view aggregates tag counts, including grouped distributions.

## Sidecar formats

Directory and one-file-per-run layouts use a single-run sidecar. Directory runs
write `retrace.json` beside their event file; file runs write
`<file-stem>.retrace.json`:

```json
{
  "retrace_tags": 1,
  "run_id": "run_042",
  "tags": [{
    "mode": "2.5",
    "event_ids": ["run_042:117"],
    "note": "planner ignored a review",
    "source": "manual",
    "confidence": null,
    "created_at": "2026-08-18T10:00:00Z"
  }],
  "run_note": ""
}
```

Line-per-run layouts share `<aggregate-file-stem>.retrace.json`:

```json
{
  "retrace_tags": 1,
  "runs": {
    "run_042": {
      "tags": [{
        "mode": "2.5",
        "event_ids": ["run_042:117"],
        "note": "planner ignored a review",
        "source": "manual",
        "confidence": null,
        "created_at": "2026-08-18T10:00:00Z"
      }],
      "run_note": ""
    }
  }
}
```

Writes use a temporary file, flush and `fsync`, then atomic `os.replace`.
The path guard permits only `retrace.json` or `*.retrace.json` within the
experiment boundary. Source logs are read-only; only explicit tag sidecars are
created or replaced. A corrupt existing sidecar is never overwritten, and read
errors produce a warning plus an empty tag result.

An anchor is detached when its event ID is no longer present; it remains stored
and is shown as detached rather than silently discarded. Event IDs contain the
run ID and ingest ordinal. Editing source files can reorder events, and changing
the mapping config changes `adapter_config_hash`; either can renumber ordinals
and detach anchors. Retrace warns when the backward-compatible `config_hash`
detects a mapping change.
