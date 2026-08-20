"""Discover run inputs from a validated mapping configuration."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, replace
from json import JSONDecodeError
from pathlib import Path, PurePosixPath, PureWindowsPath
from string import Formatter

from retrace.adapters.mapping_schema import MappingConfig, MappingConfigError

__all__ = ["DiscoveryError", "RunSource", "discover_runs"]


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
        if discovery.unit == "file":
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
    if unit == "line":
        raise MappingConfigError("run_discovery.unit: 'line' is not supported yet")
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
