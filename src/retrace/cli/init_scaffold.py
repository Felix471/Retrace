"""Infer and render a reviewable mapping draft from arbitrary JSONL logs."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from retrace.adapters.discovery import iter_jsonl_records


class ScaffoldError(ValueError):
    """Raised when a log tree cannot provide a useful draft."""


@dataclass(frozen=True)
class Candidate:
    """A field candidate and the observations supporting it."""

    field: str
    hit_rate: float
    hits: int
    total: int
    shape: str
    score: float


@dataclass(frozen=True)
class Layout:
    """Detected filesystem and record layout."""

    unit: str
    pattern: str
    files: tuple[Path, ...]
    records: tuple[dict[str, object], ...]
    arrays: tuple[str, ...] = ()
    events_file: str | None = None
    manifest: str | None = None
    manifests: tuple[dict[str, object], ...] = ()


_HINTS: dict[str, tuple[str, ...]] = {
    "timestamp": ("ts", "time", "timestamp", "date", "*_at"),
    "turn": ("round", "turn", "step", "sequence", "seq", "index"),
    "agent_id": ("agent", "speaker", "actor", "handler", "sender", "*_id"),
    "role": ("role", "persona", "function"),
    "type": ("type", "kind", "event", "step_kind"),
    "phase": ("phase", "stage", "section", "*_stage"),
    "content": ("content", "text", "body", "message", "msg"),
    "tokens_in": ("tokens_in", "input_tokens", "prompt_tokens"),
    "tokens_out": ("tokens_out", "output_tokens", "completion_tokens"),
    "cost": ("cost", "price", "amount"),
}


def _name_matches(name: str, hints: tuple[str, ...]) -> bool:
    lowered = name.lower()
    words = tuple(part for part in re.split(r"[^a-z0-9]+|(?=[A-Z])", name) if part)
    normalized_words = {word.lower() for word in words}
    for hint in hints:
        if hint.startswith("*") and lowered.endswith(hint[1:]):
            return True
        if lowered == hint or hint in normalized_words:
            return True
    return False


def is_timestamp(value: object) -> tuple[bool, str]:
    """Recognize ISO-8601 strings and plausible epoch seconds/milliseconds."""
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False, ""
        return True, "ISO-8601"
    if type(value) in (int, float) and math.isfinite(float(value)):
        number = abs(float(value))
        if 1_000_000_000 <= number <= 99_999_999_999_999:
            return True, "epoch-like"
    return False, ""


def is_small_cardinality(values: list[object], total: int) -> bool:
    """Return whether scalar observations behave like a categorical field."""
    if not values or any(isinstance(value, (dict, list)) for value in values):
        return False
    distinct = len({str(value) for value in values})
    return distinct <= max(12, math.ceil(total * 0.2))


def _shape(slot: str, values: list[object], total: int) -> tuple[list[bool], str]:
    if slot == "timestamp":
        checks = [is_timestamp(value) for value in values]
        labels = Counter(label for valid, label in checks if valid)
        return [valid for valid, _ in checks], labels.most_common(1)[0][0] if labels else "timestamp"
    if slot == "turn":
        return [type(value) is int for value in values], "integer"
    if slot in {"tokens_in", "tokens_out", "cost"}:
        return [type(value) in (int, float) for value in values], "numeric"
    if slot == "content":
        return [isinstance(value, str) and len(value.strip()) >= 8 for value in values], "free text"
    if slot in {"agent_id", "role", "type", "phase"}:
        small = is_small_cardinality(values, total)
        return [small and isinstance(value, (str, int)) for value in values], "small-cardinality"
    return [True for _ in values], "present"


def detect_candidates(records: list[dict[str, object]], slot: str) -> list[Candidate]:
    """Rank fields for an event slot using both names and valid value shapes."""
    total = len(records)
    if total == 0:
        return []
    keys = sorted({key for record in records for key in record})
    result: list[Candidate] = []
    for key in keys:
        values = [record[key] for record in records if key in record]
        checks, label = _shape(slot, values, total)
        hits = sum(checks)
        rate = hits / total
        name_score = 1.0 if _name_matches(key, _HINTS[slot]) else 0.0
        if name_score or hits:
            result.append(Candidate(key, rate, hits, total, label, rate + name_score))
    return sorted(result, key=lambda item: (-item.score, -item.hit_rate, item.field))


def unique_id_candidate(records: list[dict[str, object]]) -> Candidate | None:
    """Find an id-shaped string field unique across aggregate records."""
    total = len(records)
    candidates: list[Candidate] = []
    for key in sorted({key for record in records for key in record}):
        values = [record.get(key) for record in records]
        present = [value for value in values if isinstance(value, str) and value]
        lowered = key.lower()
        hinted = lowered in {"id", "uuid"} or lowered.endswith(("_id", "id"))
        if hinted and len(present) == total and len(set(present)) == total:
            candidates.append(Candidate(key, 1.0, total, total, "unique string", 2.0))
    return candidates[0] if candidates else None


def _jsonl_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".jsonl" else []
    return sorted(item for item in path.rglob("*.jsonl") if item.is_file())


def _sample_file(path: Path, limit: int) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for _, item in iter_jsonl_records(path):
        if isinstance(item, dict):
            records.append(item)
            if len(records) >= limit:
                break
    return records


def _document_samples(path: Path, limit: int) -> tuple[list[Path], list[dict[str, object]]]:
    candidates = [path] if path.is_file() else sorted(
        item for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in {".json", ".jsonl"}
    )
    files: list[Path] = []
    records: list[dict[str, object]] = []
    for candidate in candidates:
        if _sample_file(candidate, 1):
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            files.append(candidate)
            records.append(value)
            if len(records) >= limit:
                break
    return files, records


def detect_layout(path: Path, sample: int = 200) -> Layout:
    """Inspect a path read-only and classify its JSONL organization."""
    if sample < 1:
        raise ScaffoldError("--sample must be at least 1")
    if not path.exists():
        raise ScaffoldError(f"{path}: path does not exist")
    files = _jsonl_files(path)
    sampled = {file: _sample_file(file, sample) for file in files}
    document_files, documents = _document_samples(path, sample)
    if documents and not any(sampled.values()):
        suffixes = {file.suffix.lower() for file in document_files}
        if path.is_file() or len(document_files) == 1:
            pattern = document_files[0].name
        elif len(suffixes) == 1:
            pattern = f"**/*{next(iter(suffixes))}"
        else:
            pattern = "**/*"
        arrays = tuple(
            key for key, value in documents[0].items()
            if isinstance(value, list) and value
            and (all(isinstance(item, dict) for item in value)
                 or all(isinstance(item, str) for item in value))
        )
        return Layout("json", pattern, tuple(document_files), tuple(documents), arrays)

    if not files:
        raise ScaffoldError(f"{path}: no parseable JSONL lines found")
    if not any(sampled.values()):
        raise ScaffoldError(f"{path}: no parseable JSONL lines found (empty or binary files)")

    first_records = next(records for records in sampled.values() if records)
    arrays = tuple(
        key
        for key, value in first_records[0].items()
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value)
    )
    if arrays:
        pattern = files[0].name if path.is_file() or len(files) == 1 else "**/*.jsonl"
        return Layout("line", pattern, tuple(files), tuple(first_records), arrays)

    if path.is_dir():
        parents = {file.parent for file in files}
        names = Counter(file.name for file in files)
        event_name, count = names.most_common(1)[0]
        if len(parents) > 1 and count == len(parents):
            manifests = []
            manifest_names: Counter[str] = Counter()
            for parent in sorted(parents):
                for item in sorted(parent.glob("*.json")):
                    try:
                        value = yaml.safe_load(item.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, yaml.YAMLError):
                        continue
                    if isinstance(value, dict):
                        manifests.append(value)
                        manifest_names[item.name] += 1
                        break
            manifest = manifest_names.most_common(1)[0][0] if manifest_names else None
            common = min(parents).parent
            pattern = "*/" if common == path else "**/"
            records = tuple(record for file in files if file.name == event_name for record in sampled[file])
            return Layout("dir", pattern, tuple(files), records, events_file=event_name,
                          manifest=manifest, manifests=tuple(manifests))

    records = tuple(record for file in files for record in sampled[file])
    pattern = files[0].name if path.is_file() else "*.jsonl"
    return Layout("file", pattern, tuple(files), records)


def _scalar(value: object) -> str:
    return yaml.safe_dump(value, default_flow_style=True, allow_unicode=False).strip().removesuffix("...").strip()


def _evidence(candidate: Candidate, runner_up: Candidate | None = None) -> str:
    percent = 100 * candidate.hit_rate
    text = f"{percent:.0f}% of {candidate.total} sampled records, {candidate.shape}"
    if runner_up is not None:
        text += f"; runner-up {runner_up.field} {runner_up.hit_rate * 100:.0f}%"
    return text


def _best(records: list[dict[str, object]], slot: str) -> tuple[Candidate | None, Candidate | None]:
    ranked = detect_candidates(records, slot)
    best = ranked[0] if ranked and ranked[0].hit_rate >= 0.5 and _name_matches(ranked[0].field, _HINTS[slot]) else None
    return best, ranked[1] if len(ranked) > 1 else None


def _todo(slot: str, records: list[dict[str, object]], indent: str) -> str:
    keys = Counter(key for record in records for key in record).most_common(5)
    hints = ", ".join(key for key, _ in keys) or "no observed keys"
    return f"{indent}# TODO: {slot}; observed keys: {hints}"


def _field_lines(records: list[dict[str, object]], indent: str) -> list[str]:
    lines: list[str] = []
    for slot in _HINTS:
        best, runner = _best(records, slot)
        if best is None:
            lines.append(_todo(slot, records, indent))
            continue
        if slot == "type":
            values = sorted({str(record[best.field]) for record in records if best.field in record})
            known = {"message", "tool_call", "tool_result", "system"}
            lines.append(f"{indent}type:  # {_evidence(best, runner)}")
            lines.append(f"{indent}  from: {_scalar(best.field)}  # inferred categorical source")
            lines.append(f"{indent}  map:  # observed values mapped to the closed enum")
            for value in values:
                mapped = value if value.lower() in known else "other"
                lines.append(f"{indent}    {_scalar(value)}: {mapped}  # observed category")
            lines.append(f"{indent}  default: other  # safe fallback for unseen categories")
        else:
            lines.append(f"{indent}{slot}: {_scalar(best.field)}  # {_evidence(best, runner)}")
    lines.append(f"{indent}metadata: rest  # preserve fields not mapped above")
    return lines


def _agent_array(record: dict[str, object]) -> tuple[str, str, list[str]] | None:
    for path, value in record.items():
        if not isinstance(value, list) or not value or not all(isinstance(item, dict) for item in value):
            continue
        records = list(value)
        keys = sorted({key for item in records for key in item})
        id_keys = [key for key in keys if key.lower() == "id" or key.lower().endswith("_id")]
        attrs = [key for key in keys if _name_matches(key, _HINTS["role"])]
        if id_keys and attrs:
            return path, id_keys[0], attrs
    return None


def _sniff_rank(key: str, records: tuple[dict[str, object], ...]) -> tuple[int, str]:
    """Put structurally distinctive names ahead of generic payload names."""
    lowered = key.lower()
    values = [record[key] for record in records]
    if lowered in {"id", "uuid"} or lowered.endswith(("_id", "id")):
        rank = 0
    elif _name_matches(key, _HINTS["type"]):
        rank = 1
    elif all(isinstance(value, list) and value for value in values):
        rank = 2
    elif all(isinstance(value, (dict, list)) for value in values):
        rank = 3
    elif _name_matches(key, _HINTS["content"]):
        rank = 9
    else:
        rank = 5
    return rank, lowered


def _sniff_lines(records: tuple[dict[str, object], ...]) -> list[str]:
    total = len(records)
    coverage = Counter(key for record in records for key in record)
    common = sorted(
        (key for key, hits in coverage.items() if hits == total),
        key=lambda key: _sniff_rank(key, records),
    )
    if common:
        lines = [
            "sniff:  # distinctive keys shared by every sampled record",
            "  required_fields:  # registry checks these on the first parseable record",
        ]
        lines.extend(
            f"    - {_scalar(key)}  # present in {total}/{total} sampled records"
            for key in common[:4]
        )
        return lines

    candidates = sorted(coverage.items(), key=lambda item: (-item[1], item[0]))[:6]
    hints = ", ".join(f"{key} {hits / total * 100:.0f}%" for key, hits in candidates)
    return [
        "# TODO: sniff; no key was present in every sampled record",
        f"# Top coverage candidates: {hints}",
    ]


def render_draft(layout: Layout) -> str:
    """Render an ASCII-only, schema-valid YAML mapping with explicit evidence."""
    heading = ("# Draft mapping generated from sampled JSON document records."
               if layout.unit == "json"
               else "# Draft mapping generated from sampled JSONL records.")
    matched_kind = "JSON document" if layout.unit == "json" else "JSONL file"
    lines = [
        heading,
        "# Review every evidence comment and resolve TODOs before relying on the data.",
        "retrace_mapping: 1  # required mapping schema version",
        "run_discovery:  # inferred filesystem layout",
        f"  pattern: {_scalar(layout.pattern)}  # matched {len(layout.files)} {matched_kind}(s)",
        f"  unit: {layout.unit}  # detected {layout.unit}-based layout",
    ]
    if layout.events_file:
        lines.append(f"  events_file: {_scalar(layout.events_file)}  # common JSONL filename")
    lines.append("run:  # inferred run-level extraction")
    if layout.unit in ("line", "json"):
        candidate = unique_id_candidate(list(layout.records))
        run_id = candidate.field if candidate else "@"
        reason = _evidence(candidate) if candidate else "TODO: no unique id found; uses whole record"
        lines.append(f"  id: {_scalar(run_id)}  # {reason}")
    elif layout.unit == "dir":
        lines.append('  id: "{dir_name}"  # directory name is unique per discovered run')
    else:
        lines.append('  id: "{file_stem}"  # filename stem is unique per discovered run')
    if layout.manifest:
        lines.append(f"  manifest: {_scalar(layout.manifest)}  # detected JSON manifest")
    run_records = list(layout.manifests or layout.records)
    outcome_candidates: list[Candidate] = []
    for hint in ("winner", "outcome", "result", "status"):
        values = [record[hint] for record in run_records if hint in record]
        if values and is_small_cardinality(values, len(run_records)):
            outcome_candidates.append(Candidate(hint, len(values) / len(run_records), len(values), len(run_records), "small-cardinality", 2.0))
    used_run: set[str] = set()
    if outcome_candidates:
        candidate = outcome_candidates[0]
        used_run.add(candidate.field)
        lines.append(f"  outcome: {_scalar(candidate.field)}  # {_evidence(candidate)}")
    else:
        lines.append("  # TODO: outcome; no outcome-like categorical key observed")
    metadata = []
    for key in sorted({key for record in run_records for key in record}):
        values = [record[key] for record in run_records if key in record]
        if key not in used_run and is_small_cardinality(values, len(run_records)) and all(not isinstance(v, (dict, list)) for v in values):
            metadata.append((key, len(values), len(run_records)))
    if metadata:
        lines.append("  metadata:  # small-cardinality run fields")
        for key, hits, total in metadata[:8]:
            lines.append(f"    {_scalar(key)}: {_scalar(key)}  # {hits / total * 100:.0f}% of {total} sampled runs")

    lines.append("event:  # inferred event extraction")
    if layout.unit in ("line", "json"):
        top = layout.records[0]
        agent = _agent_array(top)
        event_arrays = [name for name in layout.arrays if agent is None or name != agent[0]]
        lines.append("  sources:  # object arrays in aggregate records, in source order")
        for priority, name in enumerate(event_arrays):
            records = [item for record in layout.records for item in record.get(name, []) if isinstance(item, dict)]
            shape = "object array" if records else "string array"
            lines.extend([
                f"    - name: {_scalar(name)}  # observed {shape}",
                f"      path: {_scalar(name)}  # present in sampled aggregate records",
                "      type: other  # array has no categorical source-level event type",
                f"      priority: {priority}  # preserves observed array order",
                "      fields:  # inferred from records in this array",
                *(_field_lines(records, "        ") if records else []),
            ])
        lines.append("  merge:  # deterministic merge of array sources")
        lines.append("    sort_by: turn  # best shared ordering slot; missing values remain stable")
        if agent:
            path, key, attrs = agent
            lines.extend([
                "agents:  # detected object array with id-like and role-like fields",
                f"  path: {_scalar(path)}  # observed top-level object array",
                f"  key: {_scalar(key)}  # id-like field in agent objects",
                "  attributes:  # role-like observed attributes",
                *[f"    {_scalar(attr)}: {_scalar(attr)}  # present on agent objects" for attr in attrs],
            ])
    else:
        lines.extend(_field_lines(list(layout.records), "  "))

    lines.extend(_sniff_lines(layout.records))
    return ("\n".join(lines) + "\n").encode("ascii", "backslashreplace").decode("ascii")


def scaffold(path: Path, sample: int = 200) -> str:
    """Detect and render a mapping draft for *path*."""
    return render_draft(detect_layout(path, sample))


__all__ = [
    "Candidate", "Layout", "ScaffoldError", "detect_candidates", "detect_layout",
    "is_small_cardinality", "is_timestamp", "render_draft", "scaffold", "unique_id_candidate",
]
