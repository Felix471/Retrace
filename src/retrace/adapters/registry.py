"""Registry, data-driven sniffing, and mapping configuration resolution."""

from __future__ import annotations

import json
from collections.abc import Iterator
from importlib import resources
from json import JSONDecodeError
from pathlib import Path
from typing import BinaryIO

import yaml

from retrace.adapters.mapping_schema import (
    MappingConfig,
    MappingConfigError,
    load_mapping_config,
    validate_mapping_config,
)

_BUILTIN_PACKAGE = "retrace.adapters.builtin"
__all__ = [
    "ConfigResolutionError",
    "builtin_names",
    "load_builtin",
    "resolve_config",
    "sniff_config",
]


class ConfigResolutionError(LookupError):
    """Raised when no explicit, local, or built-in mapping can be selected."""


def builtin_names() -> tuple[str, ...]:
    """Return packaged built-in names in stable registry order."""
    package = resources.files(_BUILTIN_PACKAGE)
    return tuple(sorted(item.name.removesuffix(".yaml") for item in package.iterdir() if item.name.endswith(".yaml")))


def load_builtin(name: str) -> tuple[MappingConfig, str]:
    """Load and validate a packaged mapping through importlib resources."""
    if name not in builtin_names():
        raise MappingConfigError(f"builtin:{name}: $: unknown built-in adapter")
    resource = resources.files(_BUILTIN_PACKAGE).joinpath(f"{name}.yaml")
    try:
        data = yaml.safe_load(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        reason = " ".join(str(error).split())
        raise MappingConfigError(f"builtin:{name}: $: cannot load config: {reason}") from None
    try:
        return validate_mapping_config(data), f"builtin:{name}"
    except MappingConfigError as error:
        lines = (f"builtin:{name}: {line}" for line in str(error).splitlines())
        raise MappingConfigError("\n".join(lines)) from None


def _matching_paths(root: Path, pattern: str, unit: str) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.is_dir() or Path(pattern).is_absolute() or ".." in pattern.replace("\\", "/").split("/"):
        return []
    matches = sorted(root.glob(pattern.rstrip("/\\") if unit == "dir" else pattern))
    if unit == "dir":
        return [path for path in matches if path.is_dir()]
    return [path for path in matches if path.is_file()]


def _records(stream: BinaryIO) -> Iterator[dict[str, object]]:
    for line_no, raw_line in enumerate(stream, start=1):
        try:
            text = raw_line.decode("utf-8-sig" if line_no == 1 else "utf-8")
            value = json.loads(text)
        except (UnicodeDecodeError, JSONDecodeError):
            continue
        if isinstance(value, dict):
            yield value


def _first_record(path: Path) -> dict[str, object] | None:
    try:
        with path.open("rb") as stream:
            return next(_records(stream), None)
    except OSError:
        return None


def _first_document(paths: list[Path]) -> dict[str, object] | None:
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def sniff_config(config: MappingConfig, root: Path) -> bool:
    """Test a target using only the signature declared by *config*."""
    if config.sniff is None:
        return False
    discovery = config.run_discovery
    matches = _matching_paths(root, discovery.pattern, discovery.unit)
    if not matches:
        return False
    candidate = matches[0]
    if discovery.unit == "dir":
        candidate = candidate / str(discovery.events_file)
    record = (_first_document(matches) if discovery.unit == "json"
              else _first_record(candidate))
    return record is not None and all(
        field in record for field in config.sniff.required_fields
    )


def resolve_config(root: Path, explicit: Path | None = None) -> tuple[MappingConfig, str]:
    """Resolve a mapping in explicit, target-local, then built-in order."""
    if explicit is not None:
        return load_mapping_config(explicit), str(explicit)

    local = (root if root.is_dir() else root.parent) / "retrace.yaml"
    if local.is_file():
        return load_mapping_config(local), str(local)

    for name in builtin_names():
        config, adapter_ref = load_builtin(name)
        if sniff_config(config, root):
            return config, adapter_ref

    raise ConfigResolutionError(
        f"{root}: no mapping configuration matched; run `retrace init` to create one"
    )
