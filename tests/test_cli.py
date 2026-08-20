from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from retrace.cli.main import _hit_rate, main

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures"


def _cli(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "retrace.cli", *(str(arg) for arg in args)],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_check_avalon_fixture() -> None:
    result = _cli("check", FIXTURES / "avalon_mini")
    assert result.returncode == 0, result.stderr
    for expected in (
        "builtin:avalon",
        "5 runs",
        "652 total events",
        "event.sources.discussions.content: 100.0% (570/570)",
        "event.sources.discussions.agent_id: 100.0% (570/570)",
        "quests.turn (ordinal): fired 3 of 22 records",
        "quests.result (derive): fired 1 of 22 records",
        "Line failures: 0",
    ):
        assert expected in result.stdout


def test_check_support_fixture() -> None:
    result = _cli("check", FIXTURES / "support_pipeline")
    assert result.returncode == 0, result.stderr
    assert "builtin:support_pipeline" in result.stdout
    assert "10 runs" in result.stdout
    assert "100.0%" in result.stdout
    assert "Warnings: 2 total" in result.stdout
    assert "Line failures:" in result.stdout


def test_check_corrupt_line_is_recoverable(tmp_path: Path) -> None:
    target = tmp_path / "copy"
    shutil.copytree(FIXTURES / "avalon_mini", target)
    data = (target / "games.jsonl").read_text(encoding="utf-8").splitlines()
    data[1] = "{broken"
    (target / "games.jsonl").write_text("\n".join(data) + "\n", encoding="utf-8")
    result = _cli("check", target)
    assert result.returncode == 0, result.stderr
    assert "4 runs" in result.stdout
    assert f"{target / 'games.jsonl'}:2:" in result.stdout


def test_unknown_format_mentions_init(tmp_path: Path) -> None:
    result = _cli("check", tmp_path)
    assert result.returncode != 0
    assert "retrace init" in result.stderr


def test_explicit_config_overrides_sniffing() -> None:
    config = REPO_ROOT / "src" / "retrace" / "adapters" / "builtin" / "avalon.yaml"
    result = _cli("check", FIXTURES / "avalon_mini", "--config", config)
    assert result.returncode == 0, result.stderr
    assert f"Adapter: {config}" in result.stdout


def test_view_creates_cache_and_passes_server_options(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    with patch("retrace.cli.main.serve") as mocked:
        code = main(
            [
                "view",
                str(FIXTURES / "avalon_mini"),
                "--cache-dir",
                str(cache),
                "--host",
                "0.0.0.0",
                "--port",
                "9001",
                "--no-browser",
                "--reingest",
            ]
        )
    assert code == 0
    databases = list(cache.glob("*.db"))
    assert len(databases) == 1
    mocked.assert_called_once_with(databases[0], "0.0.0.0", 9001, False)
    assert not list((FIXTURES / "avalon_mini").glob("*.db"))


def test_version() -> None:
    result = _cli("--version")
    assert result.returncode == 0
    assert "retrace-logs 0.1.0" in result.stdout


def test_hit_rate_math() -> None:
    assert _hit_rate(8, 1, 1) == 80.0
    assert _hit_rate(3, 0, 1) == 75.0
    assert _hit_rate(0, 0, 0) is None


@pytest.mark.skipif(
    not (REPO_ROOT / "reference-logs" / "avalon_sample.jsonl").exists(),
    reason="local-only sample is absent",
)
def test_check_local_sample() -> None:
    result = _cli("check", REPO_ROOT / "reference-logs" / "avalon_sample.jsonl")
    assert result.returncode == 0, result.stderr
    assert "5 runs" in result.stdout
    assert "ordinal): fired 3" in result.stdout
    assert "derive): fired 1" in result.stdout
    assert "Line failures: 0" in result.stdout
