from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "STATUS.md"
pytestmark = pytest.mark.skipif(
    not STATUS.exists(), reason="local-only process document is absent"
)


def test_status_version_and_table_commands() -> None:
    text = STATUS.read_text(encoding="ascii")
    version = importlib.metadata.version("retrace-logs")

    assert f"Package and version: `retrace-logs {version}`" in text

    table_rows = [line for line in text.splitlines() if line.startswith("|")]
    data_rows = [
        line
        for line in table_rows
        if not line.startswith("| ---") and not line.startswith("| Claim")
    ]
    assert data_rows
    for row in data_rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert len(cells) == 3
        assert cells[1]
