"""Discover run inputs from a validated mapping configuration."""

from __future__ import annotations

import json
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, replace
from json import JSONDecodeError
from pathlib import Path, PurePosixPath, PureWindowsPath
from string import Formatter
from typing import TypeAlias

import jmespath
from jmespath.exceptions import JMESPathError

from retrace.adapters.mapping_schema import MappingConfig, MappingConfigError

__all__ = [
    "DiscoveryError",
    "DiscoveryReport",
    "JsonlRecord",
    "RunSource",
    "discover_runs",
    "discover_runs_with_report",
    "iter_jsonl_records",
    "load_json_document",
]

JsonlRecord: TypeAlias = tuple[int, dict[str, object] | str]


class DiscoveryError(ValueError):
    """Raised when a discovery pattern cannot find any filesystem matches."""


@dataclass(frozen=True)
class RunSource:
    """A single discovered run and its optional sidecar data."""

    run_id: str
    root: Path
    events_path: Path
    manifest: dict[str, object] | None
    warnings: tuple[str, ...] = ()
    line_no: int | None = None
    document: dict[str, object] | None = None


@dataclass(frozen=True)
class DiscoveryReport:
    """Line discovery results and recoverable input failures."""

    sources: list[RunSource]
    line_failures: list[tuple[Path, int, str]]
    per_file_failure_counts: dict[Path, int]


@dataclass(frozen=True)
class _Candidate:
    root: Path
    events_path: Path
    relative_path: str
    warnings: tuple[str, ...] = ()


def _validate_pattern(pattern: str) -> None:
    normalized = pattern.replace("\\", "/")
    if (
        Path(pattern).is_absolute()
        or PurePosixPath(pattern).is_absolute()
        or PureWindowsPath(pattern).is_absolute()
    ):
        raise MappingConfigError("run_discovery.pattern: absolute patterns are not allowed")
    if ".." in normalized.split("/"):
        raise MappingConfigError("run_discovery.pattern: '..' path components are not allowed")


def _warning(path: Path, message: str) -> str:
    return f"{path}: {message}"


def _inside_root(path: Path, resolved_root: Path) -> bool:
    try:
        path.resolve().relative_to(resolved_root)
    except (OSError, ValueError):
        return False
    return True


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _find_candidates(config: MappingConfig, root: Path) -> list[_Candidate]:
    discovery = config.run_discovery
    pattern = discovery.pattern
    _validate_pattern(pattern)
    if discovery.unit in ("line", "json") and root.is_file():
        return [_Candidate(root, root, root.name)]
    if not root.is_dir():
        raise DiscoveryError(f"{root}: no matches for pattern {pattern!r}")

    resolved_root = root.resolve()
    glob_pattern = pattern.rstrip("/\\") if discovery.unit == "dir" else pattern
    matches = list(root.glob(glob_pattern))
    candidates: list[_Candidate] = []
    for match in matches:
        if not _inside_root(match, resolved_root):
            warnings.warn(
                _warning(match, f"skipped path resolving outside root {root}"),
                UserWarning,
                stacklevel=3,
            )
            continue
        if discovery.unit in ("file", "line", "json"):
            if match.is_file():
                candidates.append(
                    _Candidate(match, match, _relative_posix(match, root))
                )
            continue
        if not match.is_dir():
            continue
        events_path = match / str(discovery.events_file)
        if not events_path.is_file():
            warnings.warn(
                _warning(match, f"skipped directory; expected file {events_path}"),
                UserWarning,
                stacklevel=3,
            )
            continue
        if not _inside_root(events_path, resolved_root):
            warnings.warn(
                _warning(events_path, f"skipped path resolving outside root {root}"),
                UserWarning,
                stacklevel=3,
            )
            continue
        candidates.append(_Candidate(match, events_path, _relative_posix(match, root)))

    if not matches:
        raise DiscoveryError(f"{root}: no matches for pattern {pattern!r}")
    return sorted(candidates, key=lambda candidate: candidate.relative_path)


def iter_jsonl_records(path: Path) -> Iterator[JsonlRecord]:
    """Yield each usable JSON object or a reason for a bad physical line."""

    with path.open("rb") as stream:
        for line_no, raw_line in enumerate(stream, start=1):
            try:
                line = raw_line.decode("utf-8-sig" if line_no == 1 else "utf-8")
            except UnicodeDecodeError as error:
                yield line_no, f"invalid UTF-8: {error}"
                continue
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except JSONDecodeError as error:
                yield line_no, f"invalid JSON: {error.msg}"
                continue
            if not isinstance(value, dict):
                yield line_no, "invalid JSON: expected an object"
                continue
            yield line_no, value


def load_json_document(path: Path) -> tuple[dict[str, object] | None, str | None]:
    """Load one UTF-8 JSON object, returning a recoverable failure reason."""
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as error:
        return None, f"invalid UTF-8: {error}"
    except JSONDecodeError as error:
        return None, f"invalid JSON: {error.msg}"
    except OSError as error:
        return None, f"cannot read JSON: {error}"
    if not isinstance(value, dict):
        return None, "invalid JSON: expected an object"
    return value, None


def _render_run_id(template: str, candidate: _Candidate, unit: str) -> str:
    values = {
        "file_stem": candidate.events_path.stem,
        "dir_name": candidate.root.name if unit == "dir" else candidate.root.parent.name,
    }
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name is not None and field_name not in values:
            raise MappingConfigError(
                f"run.id: unknown template variable {field_name!r}; "
                "valid variables: file_stem, dir_name"
            )
    return template.format(**values)


def _load_manifest(
    manifest_name: str | None, candidate: _Candidate, unit: str
) -> tuple[dict[str, object] | None, tuple[str, ...]]:
    if manifest_name is None:
        return None, ()
    run_directory = candidate.root if unit == "dir" else candidate.root.parent
    path = run_directory / manifest_name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, (_warning(path, "manifest file is missing"),)
    except (JSONDecodeError, OSError, UnicodeError) as error:
        return None, (_warning(path, f"invalid manifest: {error}"),)
    if not isinstance(value, dict):
        return None, (_warning(path, "invalid manifest: expected a JSON object"),)
    return value, ()


def _make_ids_unique(sources: list[RunSource]) -> list[RunSource]:
    reserved = {source.run_id for source in sources}
    used: dict[str, Path] = {}
    result: list[RunSource] = []
    for source in sources:
        base_id = source.run_id
        if base_id not in used:
            used[base_id] = source.root
            result.append(source)
            continue
        first_path = used[base_id]
        suffix = 2
        unique_id = f"{base_id}~{suffix}"
        while unique_id in used or unique_id in reserved:
            suffix += 1
            unique_id = f"{base_id}~{suffix}"
        duplicate_warning = _warning(
            source.root,
            f"duplicate run id {base_id!r} also used by {first_path}; assigned {unique_id!r}",
        )
        used[unique_id] = source.root
        result.append(
            replace(
                source,
                run_id=unique_id,
                warnings=(*source.warnings, duplicate_warning),
            )
        )
    return result


def discover_runs(config: MappingConfig, root: Path) -> list[RunSource]:
    """Resolve the configured file or directory layout beneath *root*."""

    unit = config.run_discovery.unit
    if unit in ("line", "json"):
        return discover_runs_with_report(config, root).sources
    candidates = _find_candidates(config, root)
    sources: list[RunSource] = []
    for candidate in candidates:
        manifest, manifest_warnings = _load_manifest(config.run.manifest, candidate, unit)
        sources.append(
            RunSource(
                run_id=_render_run_id(config.run.id, candidate, unit),
                root=candidate.root,
                events_path=candidate.events_path,
                manifest=manifest,
                warnings=(*candidate.warnings, *manifest_warnings),
            )
        )
    return _make_ids_unique(sources)


def discover_runs_with_report(config: MappingConfig, root: Path) -> DiscoveryReport:
    """Discover runs, retaining recoverable per-line failures in a report."""

    if config.run_discovery.unit not in ("line", "json"):
        return DiscoveryReport(discover_runs(config, root), [], {})

    try:
        id_expression = jmespath.compile(config.run.id)
    except JMESPathError as error:
        raise MappingConfigError(f"run.id: invalid JMESPath expression: {error}") from error

    candidates = _find_candidates(config, root)
    sources: list[RunSource] = []
    failures: list[tuple[Path, int, str]] = []
    failure_counts: dict[Path, int] = {}
    used_ids: set[str] = set()
    for candidate in candidates:
        failure_counts[candidate.events_path] = 0
        if config.run_discovery.unit == "json":
            item, failure = load_json_document(candidate.events_path)
            if failure is not None or item is None:
                failures.append((candidate.events_path, 1, failure or "invalid JSON"))
                failure_counts[candidate.events_path] = 1
                continue
            records = [(None, item)]
        else:
            records = iter_jsonl_records(candidate.events_path)
        for line_no, item in records:
            if isinstance(item, str):
                failures.append((candidate.events_path, line_no, item))
                failure_counts[candidate.events_path] += 1
                continue

            value = id_expression.search(item)
            fallback_reason: str | None = None
            if value is None or isinstance(value, (dict, list)):
                fallback_reason = "run id is null or non-scalar"
            else:
                run_id = str(value)
                if run_id in used_ids:
                    fallback_reason = f"duplicate run id {run_id!r}"

            source_warnings: tuple[str, ...] = ()
            if fallback_reason is not None:
                run_id = (f"{candidate.events_path.stem}#L{line_no}"
                          if line_no is not None else candidate.events_path.stem)
                source_warnings = (
                    _warning(
                        candidate.events_path,
                        f"{fallback_reason}; using fallback id {run_id!r}",
                    ),
                )
            used_ids.add(run_id)
            sources.append(
                RunSource(
                    run_id=run_id,
                    root=candidate.events_path,
                    events_path=candidate.events_path,
                    manifest=None,
                    warnings=source_warnings,
                    line_no=line_no,
                    document=item if config.run_discovery.unit == "json" else None,
                )
            )

    return DiscoveryReport(_make_ids_unique(sources), failures, failure_counts)
