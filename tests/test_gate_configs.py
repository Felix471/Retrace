from __future__ import annotations

from pathlib import Path

import pytest

from retrace.adapters.mapping_schema import load_mapping_config
from retrace.adapters.registry import load_builtin, sniff_config
from retrace.core.ingest import ingest
from retrace.core.store import SqliteStore

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "gate" / "configs"
CORPUS = ROOT / "reference-logs" / "mast" / "MAST" / "traces"


def _with_pattern(config_name: str, pattern: str):
    config = load_mapping_config(CONFIGS / config_name)
    return config.model_copy(
        update={
            "run_discovery": config.run_discovery.model_copy(
                update={"pattern": pattern}
            )
        }
    )


@pytest.mark.parametrize("path", sorted(CONFIGS.glob("*.yaml")), ids=lambda p: p.stem)
def test_gate_config_loads(path: Path) -> None:
    config = load_mapping_config(path)

    assert config.retrace_mapping == 1


@pytest.mark.skipif(not CORPUS.exists(), reason="local survey corpus is absent")
def test_ag2_config_ingests_native_top_level_tree() -> None:
    config, _ = load_builtin("ag2")

    assert sniff_config(config, CORPUS / "AG2")
    with SqliteStore(":memory:") as store:
        report = ingest(config, CORPUS / "AG2", store)
        runs = store.list_runs()
        event_count = store.experiment_summary()[1]
        roles = {
            event.role
            for run in runs
            for event in store.get_events(run.id, limit=10_000)[0]
        }

    assert len(runs) == 38
    assert event_count > 0
    assert roles - {None}
    assert report.line_failures == []
    assert not any(report.per_file_line_failures.values())


@pytest.mark.skipif(not CORPUS.exists(), reason="local survey corpus is absent")
def test_ag2_config_ingests_bounded_experiment_sample() -> None:
    experiment = min((CORPUS / "AG2" / "experiments").iterdir())
    paths = sorted(experiment.glob("*.json"))[:50]
    config = _with_pattern("ag2.yaml", "*.json")

    assert len(paths) == 50
    assert sniff_config(config, experiment)
    with SqliteStore(":memory:") as store:
        reports = [ingest(config, path, store) for path in paths]
        runs = store.list_runs()
        event_count = store.experiment_summary()[1]
        roles = {
            event.role
            for run in runs
            for event in store.get_events(run.id, limit=10_000)[0]
        }

    assert len(runs) == 50
    assert event_count > 0
    assert roles - {None}
    assert all(report.line_failures == [] for report in reports)
    assert all(not any(report.per_file_line_failures.values()) for report in reports)


@pytest.mark.skipif(not CORPUS.exists(), reason="local survey corpus is absent")
def test_ag2_duplicate_ids_across_experiments_fall_back_without_failure() -> None:
    experiments = sorted((CORPUS / "AG2" / "experiments").iterdir())[:2]
    prefix = experiments[0].name[:-1]
    suffixes = "".join(experiment.name[-1] for experiment in experiments)
    pattern = f"experiments/{prefix}[{suffixes}]/*.json"
    config = _with_pattern("ag2.yaml", pattern)

    with SqliteStore(":memory:") as store:
        report = ingest(config, CORPUS / "AG2", store)
        runs = store.list_runs()

    assert len(runs) == 400
    assert sum(run.ingest_warnings - run.n_repaired for run in runs) == 200
    assert report.line_failures == []


@pytest.mark.skipif(not CORPUS.exists(), reason="local survey corpus is absent")
def test_hyperagent_config_ingests_bounded_native_sample() -> None:
    config = load_mapping_config(CONFIGS / "hyperagent.yaml")
    paths = sorted((CORPUS / "HyperAgent").glob("*.json"))[:10]

    assert len(paths) == 10
    assert sniff_config(config, CORPUS)
    with SqliteStore(":memory:") as store:
        reports = [ingest(config, path, store) for path in paths]
        run_count, event_count, _ = store.experiment_summary()

    assert run_count == 10
    assert event_count > 0
    assert all(report.line_failures == [] for report in reports)
    assert all(not any(report.per_file_line_failures.values()) for report in reports)


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
    from retrace.adapters.discovery import iter_jsonl_records

    records = list(iter_jsonl_records(CORPUS / relative))

    assert records
    assert any(isinstance(value, str) for _, value in records)


def test_gate_report_preserves_initial_and_post_g1_verdicts() -> None:
    report = (ROOT / "GATE_REPORT.md").read_text(encoding="ascii")

    assert "Result: 0/7 native framework layouts ingest faithfully" in report
    assert "## 6. After G1" in report
    assert (
        "Owner verdict: tested against real AG2 and HyperAgent traces from the MAST "
        "corpus (config-only); free-text logs are out of scope in v1."
    ) in report
