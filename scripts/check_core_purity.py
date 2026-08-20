"""Check that core and UI sources remain independent of fixture formats."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from pathlib import Path

from retrace.core.model import Event, Run


@dataclass(frozen=True, order=True)
class Violation:
    path: str
    line: int
    token: str
    rule: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.token} ({self.rule})"


class ConfigurationError(ValueError):
    """Raised when checker configuration cannot be loaded."""


def _load_json(path: Path, description: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigurationError(f"{description} error: {path}: {error}") from None
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"{description} error: {path}:{error.lineno}: {error.msg}") from None


def load_config(path: Path) -> dict[str, object]:
    value = _load_json(path, "config")
    required = {
        "banned_identifiers": list,
        "boundary_rule": str,
        "scan_paths": list,
        "exact_allowlist": list,
        "tree_allowlist": list,
        "test_markers": list,
        "field_manifest": str,
        "ui_path": str,
        "ui_field_name_exclusions": list,
        "ui_field_name_exclusions_description": str,
        "config_control_file": str,
    }
    if not isinstance(value, dict):
        raise ConfigurationError(f"config error: {path}: top level must be an object")
    for key, expected in required.items():
        if key not in value or not isinstance(value[key], expected):
            raise ConfigurationError(f"config error: {path}: {key} must be {expected.__name__}")
    return value


def _files(root: Path, configured: list[str]) -> list[Path]:
    found: list[Path] = []
    ignored = {"__pycache__", ".pytest_cache", ".ruff_cache", "build"}
    for relative in configured:
        target = root / relative
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            found.extend(
                path
                for path in target.rglob("*")
                if path.is_file()
                and not any(part in ignored or part.endswith(".egg-info") for part in path.parts)
            )
    return sorted(set(found))


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def check(root: Path, config_path: Path | None = None) -> list[Violation]:
    root = root.resolve()
    config_path = config_path or root / "scripts" / "purity_config.json"
    config = load_config(config_path)
    tokens = [str(item) for item in config["banned_identifiers"]]
    if not tokens or any(not token for token in tokens):
        raise ConfigurationError(f"config error: {config_path}: banned_identifiers must be nonempty strings")
    domain_pattern = re.compile(
        r"(?<![A-Za-z])(" + "|".join(re.escape(token) for token in tokens) + ")",
        re.IGNORECASE,
    )
    exact = {str(item).rstrip("/") for item in config["exact_allowlist"]}
    trees = tuple(str(item).rstrip("/") + "/" for item in config["tree_allowlist"])
    markers = tuple(str(item).lower() for item in config["test_markers"])
    control = str(config["config_control_file"])
    scan_paths = [str(item) for item in config["scan_paths"]] + list(trees)
    violations: list[Violation] = []

    for path in _files(root, scan_paths):
        relative = _relative(path, root)
        if relative == control or relative in exact or relative.startswith(trees):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        test_allowed = relative.startswith("tests/") and any(marker in text.lower() for marker in markers)
        if test_allowed:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for match in domain_pattern.finditer(line):
                violations.append(Violation(relative, line_no, match.group(0), "banned identifier"))

    manifest_path = root / str(config["field_manifest"])
    manifest = _load_json(manifest_path, "field manifest")
    if not isinstance(manifest, dict) or any(not isinstance(value, list) for value in manifest.values()):
        raise ConfigurationError(f"field manifest error: {manifest_path}: expected object of lists")
    model_fields = {
        field.name for model in (Event, Run) for field in dataclass_fields(model)
    }
    platform_fields = {str(item) for item in config["ui_field_name_exclusions"]}
    fields = sorted(
        {str(field) for values in manifest.values() for field in values}
        - model_fields
        - platform_fields
    )
    if fields:
        field_pattern = re.compile(
            r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(field) for field in fields)
            + r")(?![A-Za-z0-9_])"
        )
        for path in _files(root, [str(config["ui_path"])]):
            relative = _relative(path, root)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line_no, line in enumerate(lines, 1):
                for match in field_pattern.finditer(line):
                    violations.append(
                        Violation(relative, line_no, match.group(0), "fixture field name in UI")
                    )
    return sorted(violations)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    config_path = Path(args[1]) if len(args) > 1 else None
    try:
        violations = check(root, config_path)
    except ConfigurationError as error:
        print(f"core purity: ERROR: {error}")
        return 2
    if violations:
        for violation in violations:
            print(violation.render())
        return 1
    print("core purity: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
