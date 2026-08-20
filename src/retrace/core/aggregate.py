"""Deterministic summary statistics over runs returned by a store."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from retrace.core.model import Run
from retrace.core.store import RunFilters, Store

NONE_LABEL = "(none)"


@dataclass(frozen=True)
class RunAggregate:
    """Summary statistics for one metadata group."""

    group_value: Any | None
    run_count: int
    outcome_distribution: dict[str, int]
    mean_turns: float
    median_turns: float
    mean_cost: float | None
    cost_excluded: int
    mean_duration: float | None
    duration_excluded: int


def _finite_mean(values: list[float | None]) -> tuple[float | None, int]:
    included = [value for value in values if value is not None and math.isfinite(value)]
    return (statistics.fmean(included) if included else None, len(values) - len(included))


def _summarize(group_value: Any | None, runs: list[Run]) -> RunAggregate:
    outcome_counts = Counter(run.outcome for run in runs)
    outcomes = {
        NONE_LABEL if outcome is None else outcome: outcome_counts[outcome]
        for outcome in sorted(outcome_counts, key=lambda value: (value is None, str(value)))
    }
    turns = [run.n_turns for run in runs]
    mean_cost, cost_excluded = _finite_mean([run.total_cost for run in runs])
    mean_duration, duration_excluded = _finite_mean([run.duration_s for run in runs])
    return RunAggregate(
        group_value=group_value,
        run_count=len(runs),
        outcome_distribution=outcomes,
        mean_turns=statistics.fmean(turns),
        median_turns=statistics.median(turns),
        mean_cost=mean_cost,
        cost_excluded=cost_excluded,
        mean_duration=mean_duration,
        duration_excluded=duration_excluded,
    )


def aggregate_runs(
    store: Store,
    filters: RunFilters | None = None,
    group_by: str | None = None,
) -> list[RunAggregate]:
    """Aggregate filtered runs, optionally partitioned by one metadata key."""
    listed = store.list_runs(filters=filters, group_by=group_by)
    if group_by is None:
        runs = [run for run in listed if isinstance(run, Run)]
        return [] if not runs else [_summarize(None, runs)]

    groups: dict[Any | None, list[Run]] = defaultdict(list)
    for item in listed:
        if isinstance(item, tuple):
            value, run = item
            groups[value].append(run)
    ordered_values = sorted(
        groups,
        key=lambda value: (value is None, str(value), type(value).__name__, repr(value)),
    )
    return [_summarize(value, groups[value]) for value in ordered_values]


__all__ = ["NONE_LABEL", "RunAggregate", "aggregate_runs"]
