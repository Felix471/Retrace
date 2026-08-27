from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "retrace"
OUTBOUND_MODULES = {
    "aiohttp",
    "http.client",
    "httpx",
    "requests",
    "urllib.request",
    "urllib3",
    "websockets",
}
DOM_NAMESPACE_URLS = {
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/2000/svg",
}


def _outbound_imports(tree: ast.AST) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        else:
            continue
        violations.extend(
            (node.lineno, name) for name in names if name in OUTBOUND_MODULES
        )
    return violations


def test_source_has_no_outbound_capable_imports() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line_number, name in _outbound_imports(tree):
            relative = path.relative_to(ROOT).as_posix()
            violations.append(f"{relative}:{line_number}: {name}")
    assert not violations, "Outbound-capable imports found:\n" + "\n".join(violations)


def test_outbound_import_scanner_catches_standard_spellings() -> None:
    snippets = (
        "import requests",
        "import urllib.request",
        "from urllib.request import urlopen",
        "from urllib import request",
        "from http import client",
    )
    for snippet in snippets:
        assert _outbound_imports(ast.parse(snippet)), snippet


def test_served_ui_has_no_external_urls() -> None:
    violations: list[str] = []
    ui_root = SRC / "ui"
    for path in sorted(ui_root.rglob("*")):
        if path.suffix not in {".js", ".html", ".css"}:
            continue
        is_vendor = path.parent == ui_root / "vendor"
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if is_vendor and line.lstrip().startswith("//"):
                continue
            checked = line
            for namespace in DOM_NAMESPACE_URLS:
                checked = checked.replace(namespace, "")
            if "http://" in checked or "https://" in checked:
                relative = path.relative_to(ROOT).as_posix()
                violations.append(f"{relative}:{line_number}")
    assert not violations, "External URLs found in served UI:\n" + "\n".join(violations)
