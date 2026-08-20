from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_benchmark_machinery_smoke(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/bench.py",
            str(tmp_path / "dataset"),
            "--runs",
            "5",
            "--events-min",
            "50",
            "--events-max",
            "50",
            "--compare-events",
            "50",
            "--smoke",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "| Cold ingest" in result.stdout
    assert "| Warm start" in result.stdout
    assert "| /api/runs" in result.stdout
    assert "| Single run load" in result.stdout
    assert "| 5,000-event compare" in result.stdout
    assert result.stdout.count("SMOKE") == 5
