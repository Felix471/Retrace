"""Validated, guarded persistence for run-tag sidecars."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrace.core.mast import FAILURE_MODES_BY_ID
from retrace.core.store import SqliteStore


class TagValidationError(ValueError):
    """A tag payload is not valid."""


class TagPathError(ValueError):
    """A sidecar write target violates the write-path invariant."""


class CorruptSidecarError(ValueError):
    """An existing sidecar cannot safely be updated."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"Refusing to overwrite corrupt sidecar {path}: {reason}")
        self.path = path


def guard_sidecar_path(target: Path, experiment_root: Path) -> Path:
    """Return the resolved target iff it is a sidecar inside the experiment root."""
    if target.name != "retrace.json" and not target.name.endswith(".retrace.json"):
        raise TagPathError(f"Not a Retrace sidecar path: {target}")
    resolved = target.resolve(strict=False)
    root = experiment_root.resolve(strict=False)
    boundary = root if root.is_dir() else root.parent
    if not resolved.is_relative_to(boundary):
        raise TagPathError(f"Sidecar path escapes experiment root: {target}")
    return resolved


def _validate_tag(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TagValidationError("each tag must be an object")
    mode = value.get("mode")
    if not isinstance(mode, str) or mode not in FAILURE_MODES_BY_ID:
        raise TagValidationError(f"unknown MAST mode: {mode!r}")
    event_ids = value.get("event_ids", [])
    if not isinstance(event_ids, list) or not all(isinstance(item, str) for item in event_ids):
        raise TagValidationError("event_ids must be a list of strings")
    note = value.get("note", "")
    source = value.get("source", "manual")
    confidence = value.get("confidence")
    if not isinstance(note, str) or not isinstance(source, str):
        raise TagValidationError("note and source must be strings")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))):
        raise TagValidationError("confidence must be null or a number")
    created_at = value.get("created_at")
    if created_at is None:
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if not isinstance(created_at, str):
        raise TagValidationError("created_at must be an ISO-8601 string")
    try:
        datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise TagValidationError("created_at must be an ISO-8601 string") from exc
    return {
        "mode": mode,
        "event_ids": event_ids,
        "note": note,
        "source": source,
        "confidence": confidence,
        "created_at": created_at,
    }


class TagService:
    """Read and atomically update sidecars belonging to one store."""

    def __init__(self, store: SqliteStore) -> None:
        self.store = store
        self._cache: dict[Path, tuple[int, int, dict[str, Any]]] = {}

    def sidecar_path(self, run_id: str) -> tuple[Path, bool]:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        unit = self.store.meta_get("discovery_unit")
        root_value = self.store.meta_get("root_path")
        if unit not in {"dir", "file", "line", "json"} or root_value is None:
            raise TagPathError("Store is missing valid discovery metadata")
        source = Path(run.source_path)
        target = source.parent / ("retrace.json" if unit == "dir" else f"{source.stem}.retrace.json")
        return guard_sidecar_path(target, Path(root_value)), unit == "line"

    def _read_document(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        cached = self._cache.get(path)
        key = (stat.st_mtime_ns, stat.st_size)
        if cached is not None and cached[:2] == key:
            return copy.deepcopy(cached[2])
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("retrace_tags") != 1:
            raise ValueError("unsupported sidecar structure")
        self._cache[path] = (*key, copy.deepcopy(value))
        return value

    def _entry(self, document: dict[str, Any], run_id: str, shared: bool) -> dict[str, Any]:
        if shared:
            runs = document.get("runs")
            if not isinstance(runs, dict):
                raise ValueError("shared sidecar has no runs object")
            entry = runs.get(run_id, {"tags": [], "run_note": ""})
        else:
            if document.get("run_id") != run_id:
                raise ValueError("single-run sidecar belongs to another run")
            entry = document
        if not isinstance(entry, dict) or not isinstance(entry.get("tags"), list) or not isinstance(entry.get("run_note", ""), str):
            raise TypeError("invalid tag entry")
        return entry

    def get_tags_only(self, run_id: str) -> dict[str, Any]:
        """Read validated tags without loading events or decorating anchors."""
        path, shared = self.sidecar_path(run_id)
        warning = None
        try:
            document = self._read_document(path)
            entry = self._entry(document, run_id, shared)
            tags = [_validate_tag(tag) for tag in entry["tags"]]
            run_note = entry.get("run_note", "")
        except FileNotFoundError:
            tags, run_note = [], ""
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            tags, run_note = [], ""
            warning = f"Could not read sidecar {path}: {exc}"
        result: dict[str, Any] = {"run_id": run_id, "tags": tags, "run_note": run_note}
        if warning is not None:
            result["warning"] = warning
        return result

    def get(self, run_id: str) -> dict[str, Any]:
        result = self.get_tags_only(run_id)
        events, _ = self.store.get_events(run_id, offset=0, limit=2_147_483_647)
        present = {event.id for event in events}
        decorated = [
            {**tag, "detached_event_ids": [item for item in tag["event_ids"] if item not in present]}
            for tag in result["tags"]
        ]
        result["tags"] = decorated
        return result

    def put(self, run_id: str, tags: list[object], run_note: str = "") -> dict[str, Any]:
        path, shared = self.sidecar_path(run_id)
        validated = [_validate_tag(tag) for tag in tags]
        if not isinstance(run_note, str):
            raise TagValidationError("run_note must be a string")
        if path.exists():
            try:
                document = self._read_document(path)
                self._entry(document, run_id, shared)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise CorruptSidecarError(path, str(exc)) from exc
        else:
            document = {"retrace_tags": 1, "runs": {}} if shared else {"retrace_tags": 1, "run_id": run_id}
        entry = {"tags": validated, "run_note": run_note}
        if shared:
            document["runs"][run_id] = entry
        else:
            document.update(entry)
        path.parent.mkdir(parents=False, exist_ok=True)
        guard_sidecar_path(path, Path(self.store.meta_get("root_path") or ""))
        payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".tmp-", delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temp_path = Path(temporary.name)
        os.replace(temp_path, path)
        self._cache.pop(path, None)
        return self.get(run_id)


__all__ = [
    "CorruptSidecarError", "TagPathError", "TagService", "TagValidationError",
    "guard_sidecar_path",
]
