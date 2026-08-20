from __future__ import annotations

from pathlib import Path

import pytest

from retrace.adapters.registry import load_builtin
from retrace.core.aggregate import NONE_LABEL, RunAggregate, aggregate_runs
from retrace.core.ingest import ingest
from retrace.core.model import Run
from retrace.core.store import SqliteStore

FIXTURES = Path(__file__).parents[1] / "fixtures"


def make_run(
    run_id: str,
    *,
    cohort: str | None = "alpha",
    outcome: str | None = "ok",
    turns: int = 1,
    cost: float | None = 1.0,
    duration: float | None = 10.0,
    selected: str = "yes",
) -> Run:
    metadata = {"selected": selected}
    if cohort is not None:
        metadata["cohort"] = cohort
    return Run(
        id=run_id,
        experiment_id="experiment",
        source_path=f"input/{run_id}",
        metadata=metadata,
        outcome=outcome,
        started_at=None,
        ended_at=None,
        duration_s=duration,
        n_events=0,
        n_turns=turns,
        agent_ids=[],
        phases=[],
        tokens_in=None,
        tokens_out=None,
        total_cost=cost,
    )


def test_grouped_numeric_null_filter_and_ordering() -> None:
    runs = [
        make_run("a1", cohort="zeta", turns=1, cost=1.0, duration=None),
        make_run("a2", cohort="zeta", outcome=None, turns=3, cost=None, duration=6.0),
        make_run("a3", cohort="zeta", outcome="bad", turns=8, cost=99.0, selected="no"),
        make_run("b1", cohort="beta", outcome="bad", turns=2, cost=2.0, duration=4.0),
        make_run("b2", cohort="beta", turns=4, cost=4.0, duration=8.0),
        make_run("b3", cohort="beta", turns=9, cost=6.0, duration=12.0),
        make_run("n1", cohort=None, turns=5, cost=None, duration=None),
        make_run("n2", cohort=None, outcome="bad", turns=7, cost=None, duration=None),
    ]
    with SqliteStore(":memory:") as store:
        for run in runs:
            store.insert_run(run, [])
        actual = aggregate_runs(store, {"selected": ["yes"]}, "cohort")

    assert [group.group_value for group in actual] == ["beta", "zeta", None]
    assert actual == [
        RunAggregate("beta", 3, {"bad": 1, "ok": 2}, 5.0, 4, 4.0, 0, 8.0, 0),
        RunAggregate("zeta", 2, {"ok": 1, NONE_LABEL: 1}, 2.0, 2.0, 1.0, 1, 6.0, 1),
        RunAggregate(None, 2, {"bad": 1, "ok": 1}, 6.0, 6.0, None, 2, None, 2),
    ]


def test_ungrouped_and_empty_results() -> None:
    with SqliteStore(":memory:") as store:
        store.insert_run(make_run("one", turns=2), [])
        store.insert_run(make_run("two", turns=6), [])
        result = aggregate_runs(store)
        empty = aggregate_runs(store, {"selected": ["missing"]})
    assert len(result) == 1
    assert (result[0].group_value, result[0].mean_turns, result[0].median_turns) == (
        None,
        4.0,
        4.0,
    )
    assert empty == []


def test_non_finite_cost_is_excluded() -> None:
    with SqliteStore(":memory:") as store:
        store.insert_run(make_run("finite", cost=2.0), [])
        store.insert_run(make_run("non-finite", cost=float("inf")), [])
        result = aggregate_runs(store)
    assert result[0].mean_cost == 2.0
    assert result[0].cost_excluded == 1


def test_avalon_fixture_hand_computed_groups() -> None:
    config, _ = load_builtin("avalon")
    with SqliteStore(":memory:") as store:
        ingest(config, FIXTURES / "avalon_mini", store)
        groups = aggregate_runs(store, group_by="winReason")
    assert [(group.group_value, group.run_count, group.outcome_distribution) for group in groups] == [
        ("merlin_assassinated", 1, {"evil": 1}),
        ("three_quests_failed", 2, {"evil": 2}),
        ("three_quests_succeeded", 2, {"good": 2}),
    ]


def test_support_fixture_hand_computed_groups() -> None:
    config, _ = load_builtin("support_pipeline")
    with SqliteStore(":memory:") as store:
        ingest(config, FIXTURES / "support_pipeline", store)
        groups = aggregate_runs(store, group_by="model_name")
        absent = aggregate_runs(store, group_by="absent_key")

    assert [(group.group_value, group.run_count, group.outcome_distribution) for group in groups] == [
        ("support-lite-v1", 5, {"resolved": 5}),
        ("support-plus-v1", 5, {"escalated": 3, "resolved": 2}),
    ]
    assert [group.mean_cost for group in groups] == pytest.approx([0.0025116, 0.0024324])
    assert [group.cost_excluded for group in groups] == [0, 0]
    assert len(absent) == 1
    assert (absent[0].group_value, absent[0].run_count) == (None, 10)
