"""Lint checkable presentation rules for user-facing documentation.

The README rule examines its first 30 physical lines. The command rule examines
fenced code blocks in the README section whose heading contains ``quickstart``
and in the walkthrough before the first non-title heading whose section contains
the configured corpus token. A Markdown section ends at the next heading of
equal or higher level. This deliberately simple heuristic does not parse Markdown.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "docs_presentation.json"


def _corpus_name() -> str:
    # Presentation vocabulary is data so the core-purity scanner stays meaningful.
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return str(config["corpus_name"])


@dataclass(frozen=True, order=True)
class Violation:
    path: str
    line: int
    reason: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}"


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    headings = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2)))
    return headings


def _section_end(lines: list[str], start: int, level: int) -> int:
    for index, next_level, _title in _headings(lines):
        if index > start and next_level <= level:
            return index
    return len(lines)


def _fenced_line_numbers(lines: list[str], start: int, end: int) -> list[int]:
    inside = False
    selected = []
    fence = re.compile(r"^\s*(`{3,}|~{3,})")
    marker = ""
    for index in range(start, end):
        match = fence.match(lines[index])
        if match:
            current = match.group(1)[0]
            if not inside:
                inside = True
                marker = current
            elif current == marker:
                inside = False
            continue
        if inside:
            selected.append(index)
    return selected


def check(root: Path) -> list[Violation]:
    token = _corpus_name().lower()
    violations: list[Violation] = []

    readme_path = root / "README.md"
    readme = readme_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(readme[:30]):
        if token in line.lower():
            violations.append(Violation("README.md", index + 1, "corpus name in first 30 lines"))

    for start, level, title in _headings(readme):
        if "quickstart" not in title.lower():
            continue
        end = _section_end(readme, start, level)
        for index in _fenced_line_numbers(readme, start + 1, end):
            if token in readme[index].lower():
                violations.append(
                    Violation("README.md", index + 1, "corpus name in quickstart command block")
                )

    walkthrough_path = root / "docs" / "walkthrough.md"
    walkthrough = walkthrough_path.read_text(encoding="utf-8").splitlines()
    secondary_start = len(walkthrough)
    headings = _headings(walkthrough)
    title_level = min((level for _start, level, _title in headings), default=0)
    for start, level, _title in headings:
        if level <= title_level:
            continue
        end = _section_end(walkthrough, start, level)
        if any(token in line.lower() for line in walkthrough[start + 1 : end]):
            secondary_start = start
            break
    for index in _fenced_line_numbers(walkthrough, 0, secondary_start):
        if token in walkthrough[index].lower():
            violations.append(
                Violation(
                    "docs/walkthrough.md",
                    index + 1,
                    "corpus name in walkthrough main-path command block",
                )
            )
    return sorted(violations)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else REPO_ROOT
    violations = check(root)
    for violation in violations:
        print(violation.render())
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
