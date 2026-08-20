"""Generic two-sequence event alignment.

Inputs are sequences in ordinal order.  Elements may be core ``Event`` objects
or any objects exposing ``turn``, ``agent_id``, ``type``, ``content``, and
``ordinal`` attributes.  Returned indices refer to positions in those input
sequences; ``ordinal`` is part of the duck-typed contract but is not an
alignment anchor.

Alignment uses only the ``(turn, agent_id, type)`` signature and deliberately
disables :class:`difflib.SequenceMatcher`'s autojunk heuristic because these
signatures commonly repeat.  No field values receive special treatment.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal, Protocol

Comparator = Callable[[str, str], bool]
PairStatus = Literal["match", "content-diff", "only-a", "only-b"]
DivergenceKind = Literal["structural", "content"]


class EventLike(Protocol):
    """Structural type accepted by :func:`align`."""

    turn: int | None
    agent_id: str | None
    type: str
    content: str
    ordinal: int


def _normalized_equal(content_a: str, content_b: str) -> bool:
    return " ".join(content_a.split()) == " ".join(content_b.split())


COMPARATORS: dict[str, Comparator] = {
    "exact": lambda content_a, content_b: content_a == content_b,
    "normalized": _normalized_equal,
}


@dataclass(frozen=True, slots=True)
class AlignedPair:
    """One position in the merged alignment."""

    status: PairStatus
    index_a: int | None
    index_b: int | None


@dataclass(frozen=True, slots=True)
class FirstDivergence:
    """The earliest divergence in merged-pair order."""

    index: int
    kind: DivergenceKind


@dataclass(frozen=True, slots=True)
class AlignmentSummary:
    """Counts by alignment status."""

    matches: int
    content_diffs: int
    only_a: int
    only_b: int


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    """Complete immutable result of aligning two event sequences."""

    pairs: tuple[AlignedPair, ...]
    first_structural_divergence: int | None
    first_content_divergence: int | None
    first_divergence: FirstDivergence | None
    summary: AlignmentSummary


def align(
    events_a: Sequence[EventLike],
    events_b: Sequence[EventLike],
    comparator: str = "normalized",
) -> AlignmentResult:
    """Align two full, ordinal-ordered runs by structural event signature."""
    try:
        contents_equal = COMPARATORS[comparator]
    except KeyError:
        valid = ", ".join(sorted(COMPARATORS))
        raise ValueError(f"unknown comparator {comparator!r}; valid names: {valid}") from None

    signatures_a = [(event.turn, event.agent_id, event.type) for event in events_a]
    signatures_b = [(event.turn, event.agent_id, event.type) for event in events_b]
    matcher = SequenceMatcher(None, signatures_a, signatures_b, autojunk=False)

    pairs: list[AlignedPair] = []
    counts = {"match": 0, "content-diff": 0, "only-a": 0, "only-b": 0}
    first_structural: int | None = None
    first_content: int | None = None

    def append(status: PairStatus, index_a: int | None, index_b: int | None) -> None:
        nonlocal first_content, first_structural
        pair_index = len(pairs)
        pairs.append(AlignedPair(status, index_a, index_b))
        counts[status] += 1
        if status in {"only-a", "only-b"} and first_structural is None:
            first_structural = pair_index
        elif status == "content-diff" and first_content is None:
            first_content = pair_index

    position_a = position_b = 0
    for match in matcher.get_matching_blocks():
        for index_a in range(position_a, match.a):
            append("only-a", index_a, None)
        for index_b in range(position_b, match.b):
            append("only-b", None, index_b)
        for offset in range(match.size):
            index_a = match.a + offset
            index_b = match.b + offset
            status: PairStatus = (
                "match"
                if contents_equal(events_a[index_a].content, events_b[index_b].content)
                else "content-diff"
            )
            append(status, index_a, index_b)
        position_a = match.a + match.size
        position_b = match.b + match.size

    first: FirstDivergence | None = None
    candidates = (
        (first_structural, "structural"),
        (first_content, "content"),
    )
    present = [(index, kind) for index, kind in candidates if index is not None]
    if present:
        index, kind = min(present, key=lambda candidate: candidate[0])
        first = FirstDivergence(index=index, kind=kind)

    return AlignmentResult(
        pairs=tuple(pairs),
        first_structural_divergence=first_structural,
        first_content_divergence=first_content,
        first_divergence=first,
        summary=AlignmentSummary(
            matches=counts["match"],
            content_diffs=counts["content-diff"],
            only_a=counts["only-a"],
            only_b=counts["only-b"],
        ),
    )


__all__ = [
    "COMPARATORS",
    "AlignedPair",
    "AlignmentResult",
    "AlignmentSummary",
    "FirstDivergence",
    "align",
]
