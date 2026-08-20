import os
import time
from dataclasses import dataclass, replace

import pytest

from retrace.core.align import AlignedPair, align


@dataclass(frozen=True)
class SyntheticEvent:
    ordinal: int
    turn: int
    agent_id: str
    type: str
    content: str


def event(index: int, *, content: str | None = None, prefix: str = "a") -> SyntheticEvent:
    return SyntheticEvent(index, index, f"{prefix}-{index}", "message", content or f"text {index}")


def test_identical_runs_are_all_matches() -> None:
    events = [event(index) for index in range(4)]
    result = align(events, events)

    assert [pair.status for pair in result.pairs] == ["match"] * 4
    assert result.first_structural_divergence is None
    assert result.first_content_divergence is None
    assert result.first_divergence is None
    assert (result.summary.matches, result.summary.content_diffs) == (4, 0)
    assert (result.summary.only_a, result.summary.only_b) == (0, 0)


def test_early_insertion_resumes_matching() -> None:
    events_a = [event(index) for index in range(4)]
    inserted = SyntheticEvent(99, 99, "inserted", "system", "new")
    result = align(events_a, [events_a[0], inserted, *events_a[1:]])

    assert result.pairs[1] == AlignedPair("only-b", None, 1)
    assert [pair.status for pair in result.pairs] == ["match", "only-b", "match", "match", "match"]
    assert result.first_structural_divergence == 1
    assert result.summary.only_b == 1


def test_content_only_drift() -> None:
    events_a = [event(index) for index in range(3)]
    events_b = [*events_a[:1], replace(events_a[1], content="changed"), events_a[2]]
    result = align(events_a, events_b)

    assert [pair.status for pair in result.pairs] == ["match", "content-diff", "match"]
    assert result.first_structural_divergence is None
    assert result.first_content_divergence == 1
    assert result.first_divergence is not None
    assert (result.first_divergence.index, result.first_divergence.kind) == (1, "content")


def test_totally_disjoint_runs_emit_deletions_then_insertions() -> None:
    result = align([event(0), event(1)], [event(0, prefix="b"), event(1, prefix="b")])

    assert [pair.status for pair in result.pairs] == ["only-a", "only-a", "only-b", "only-b"]
    assert result.summary.matches == 0
    assert result.first_structural_divergence == 0
    assert result.first_divergence is not None and result.first_divergence.kind == "structural"


@pytest.mark.parametrize("content_first", [True, False])
def test_first_divergence_selects_earlier_kind(content_first: bool) -> None:
    base = [event(index) for index in range(4)]
    inserted = SyntheticEvent(99, 99, "inserted", "system", "new")
    if content_first:
        other = [replace(base[0], content="drift"), base[1], inserted, *base[2:]]
    else:
        other = [base[0], inserted, replace(base[1], content="drift"), *base[2:]]

    result = align(base, other)

    assert result.first_divergence is not None
    assert result.first_divergence.kind == ("content" if content_first else "structural")


def test_comparators_and_unknown_name() -> None:
    left = [event(0, content="a  b\n")]
    right = [event(0, content="a b")]

    assert align(left, right).summary.matches == 1
    assert align(left, right, comparator="exact").summary.content_diffs == 1
    with pytest.raises(ValueError, match=r"unknown comparator.*exact.*normalized"):
        align(left, right, comparator="semantic")


@pytest.mark.parametrize("size", [0, 1, 2, 9, 31])
def test_self_alignment_property_and_coverage(size: int) -> None:
    events = [event(index) for index in range(size)]
    result = align(events, events)

    assert all(pair.status == "match" for pair in result.pairs)
    assert result.first_divergence is None
    assert [pair.index_a for pair in result.pairs] == list(range(size))
    assert [pair.index_b for pair in result.pairs] == list(range(size))


def test_asymmetric_coverage_and_status_invariants() -> None:
    events_a = [event(index) for index in range(5)]
    events_b = [events_a[0], replace(events_a[2], content="drift"), event(10), events_a[4]]
    result = align(events_a, events_b)

    assert sorted(pair.index_a for pair in result.pairs if pair.index_a is not None) == list(range(5))
    assert sorted(pair.index_b for pair in result.pairs if pair.index_b is not None) == list(range(4))
    for pair in result.pairs:
        assert (pair.index_a is None) == (pair.status == "only-b")
        assert (pair.index_b is None) == (pair.status == "only-a")


def test_empty_and_single_event_edges() -> None:
    one = [event(0)]

    assert align([], []).pairs == ()
    assert align([], one).pairs == (AlignedPair("only-b", None, 0),)
    assert align(one, []).pairs == (AlignedPair("only-a", 0, None),)
    assert align(one, [replace(one[0], content="different")]).summary.content_diffs == 1


@pytest.mark.skipif(bool(os.environ.get("RETRACE_SKIP_BUDGET")), reason="budget tests disabled")
def test_alignment_budget() -> None:
    events_a = [event(index) for index in range(5_000)]
    events_b = [item for index, item in enumerate(events_a) if index % 100 != 0]
    events_b = [
        replace(item, content=f"drift {item.ordinal}") if item.ordinal % 100 == 25 else item
        for item in events_b
    ]
    for index in range(75, 5_000, 100):
        events_b.insert(index, SyntheticEvent(10_000 + index, 10_000 + index, "new", "system", "new"))

    started = time.perf_counter()
    align(events_a, events_b)
    realistic_elapsed = time.perf_counter() - started

    disjoint = [event(index, prefix="b") for index in range(5_000)]
    started = time.perf_counter()
    align(events_a, disjoint)
    hard_elapsed = time.perf_counter() - started

    assert realistic_elapsed < 2.0, f"realistic alignment took {realistic_elapsed:.3f}s"
    assert hard_elapsed < 6.0, f"low-similarity alignment took {hard_elapsed:.3f}s"
