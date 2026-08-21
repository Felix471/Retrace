from __future__ import annotations

from pathlib import Path

import pytest

from retrace.adapters.discovery import iter_jsonl_records
from retrace.adapters.mapping_schema import load_mapping_config

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "gate" / "configs"
CORPUS = ROOT / "reference-logs" / "mast" / "MAST" / "traces"


@pytest.mark.parametrize("path", sorted(CONFIGS.glob("*.yaml")), ids=lambda p: p.stem)
def test_gate_config_loads(path: Path) -> None:
    config = load_mapping_config(path)

    assert config.retrace_mapping == 1


@pytest.mark.skipif(not CORPUS.exists(), reason="local survey corpus is absent")
@pytest.mark.parametrize(
    "relative",
    [
        "AG2/02da9c1f-7c36-5739-b723-33a7d4f8e7e7_human.json",
        "HyperAgent/astropy__astropy-14182.json",
    ],
)
def test_g1_layout_is_not_a_jsonl_event_stream(relative: str) -> None:
    records = list(iter_jsonl_records(CORPUS / relative))

    assert records
    assert not any(isinstance(value, dict) for _, value in records)


@pytest.mark.skipif(not CORPUS.exists(), reason="local survey corpus is absent")
@pytest.mark.parametrize(
    "relative",
    [
        (
            "MagenticOne_GAIA/gaia_validation_level_1__MagenticOne/"
            "0383a3ee-47a7-41a4-b493-519bdefe0488/0/console_log.txt"
        ),
        "OpenManus_GAIA/0383a3ee-47a7-41a4-b493-519bdefe0488.log",
        "AppWorld/229360a_1.txt",
        "programdev/chatdev/2048/2048_DefaultOrganization_20250329233429.log",
        "programdev/metagpt/programdev_0.txt",
    ],
)
def test_g2_layout_has_no_json_object_events(relative: str) -> None:
    records = list(iter_jsonl_records(CORPUS / relative))

    assert records
    assert any(isinstance(value, str) for _, value in records)
