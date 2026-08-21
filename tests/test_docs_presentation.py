from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from scripts.check_docs_presentation import check

REPO_ROOT = Path(__file__).resolve().parents[1]
PRESENTATION = json.loads(
    (REPO_ROOT / "docs_presentation.json").read_text(encoding="utf-8")
)
CORPUS_NAME = PRESENTATION["corpus_name"]
FRAMING_SENTENCE = PRESENTATION["framing_sentence"]
BANNED_PHRASES = PRESENTATION["banned_phrases"]
USER_DOCS = [
    REPO_ROOT / "README.md",
    *sorted((REPO_ROOT / "docs").glob("*.md")),
    REPO_ROOT / "CHANGELOG.md",
    REPO_ROOT / "demo" / "README.md",
]


def _synthetic_tree(tmp_path: Path, readme: str, walkthrough: str) -> Path:
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "walkthrough.md").write_text(walkthrough, encoding="utf-8")
    return tmp_path


def test_first_thirty_physical_lines_rule(tmp_path: Path) -> None:
    lines = ["safe"] * 30 + [CORPUS_NAME]
    root = _synthetic_tree(tmp_path, "\n".join(lines), "# Walkthrough\n")
    assert check(root) == []

    lines[29] = CORPUS_NAME.upper()
    (root / "README.md").write_text("\n".join(lines), encoding="utf-8")
    violations = check(root)
    assert [(item.path, item.line) for item in violations] == [("README.md", 30)]


def test_main_path_fenced_command_rules(tmp_path: Path) -> None:
    readme = f"""# Project

## Quickstart

```shell
tool view {CORPUS_NAME}
```

## Later
```
tool view {CORPUS_NAME}
```
"""
    walkthrough = f"""# Walkthrough

```shell
tool view {CORPUS_NAME}
```

## Second corpus

{FRAMING_SENTENCE}
`tool view {CORPUS_NAME}`
"""
    violations = check(_synthetic_tree(tmp_path, readme, walkthrough))
    assert [(item.path, item.reason) for item in violations] == [
        ("README.md", "corpus name in first 30 lines"),
        ("README.md", "corpus name in quickstart command block"),
        ("README.md", "corpus name in first 30 lines"),
        ("docs/walkthrough.md", "corpus name in walkthrough main-path command block"),
    ]


def test_real_tree_lint_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_docs_presentation.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_banned_phrase_rule_fails_seeded_doc_and_accepts_clean_doc(tmp_path: Path) -> None:
    phrase = BANNED_PHRASES[0]
    root = _synthetic_tree(tmp_path, "# Project\n", "# Walkthrough\n")
    mapping = root / "docs" / "mapping.md"
    mapping.write_text(f"Supports {phrase.upper()}.\n", encoding="utf-8")

    violations = check(root)
    assert [(item.path, item.line, item.reason) for item in violations] == [
        ("docs/mapping.md", 1, f"banned phrase: {phrase}")
    ]

    mapping.write_text("Supports structured JSON records.\n", encoding="utf-8")
    assert check(root) == []


def test_walkthrough_second_step_starts_with_exact_framing_sentence() -> None:
    text = (REPO_ROOT / "docs" / "walkthrough.md").read_text(encoding="utf-8")
    section = re.split(r"^## Second step:.*$", text, maxsplit=1, flags=re.MULTILINE)[1]
    first_sentence = section.strip().splitlines()[0]
    assert first_sentence == FRAMING_SENTENCE


def _paragraphs_with_lines(text: str) -> list[tuple[int, str]]:
    paragraphs = []
    line = 1
    for block in re.split(r"\n\s*\n", text):
        paragraphs.append((line, block))
        line += block.count("\n") + 2
    return paragraphs


def test_all_corpus_mentions_have_nearby_framing() -> None:
    ideas = ("author's real experiment corpus", "repairs and flags", "domain is irrelevant")
    violations = []
    for path in USER_DOCS:
        paragraphs = _paragraphs_with_lines(path.read_text(encoding="utf-8"))
        for paragraph_index, (start_line, paragraph) in enumerate(paragraphs):
            for match in re.finditer(re.escape(CORPUS_NAME), paragraph, re.IGNORECASE):
                line = start_line + paragraph[: match.start()].count("\n")
                prior = paragraphs[paragraph_index - 1][1] if paragraph_index else ""
                prior_sentences = [item for item in re.split(r"(?<=[.!?])\s+", prior) if item]
                preceding_sentence = prior_sentences[-1] if prior_sentences else ""
                context = f"{preceding_sentence}\n{paragraph}".lower()
                missing = [idea for idea in ideas if idea not in context]
                if missing:
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{line}: missing {', '.join(missing)}")
    assert not violations, "\n".join(violations)
