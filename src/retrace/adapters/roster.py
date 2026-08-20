"""Compiled single-key joins from per-run rosters onto extracted events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from jmespath.exceptions import JMESPathError

from retrace.adapters.extract import (
    _coerce_string,
    _compile_expression,
    _CompiledExpression,
)
from retrace.adapters.mapping_schema import AgentsConfig

__all__ = [
    "AgentRosterJoin",
    "RosterJoin",
    "RosterJoinResult",
    "RosterMatch",
    "RosterTable",
    "RosterWarningCategory",
    "RosterWarningCounts",
    "RunRoster",
]

RosterWarningCategory: TypeAlias = Literal["path", "key", "duplicate", "unmatched"]
_WARNING_CATEGORIES: tuple[RosterWarningCategory, ...] = (
    "path",
    "key",
    "duplicate",
    "unmatched",
)


@dataclass(frozen=True, slots=True)
class RosterWarningCounts:
    """Per-category warning flags for one run roster."""

    path: int = 0
    key: int = 0
    duplicate: int = 0
    unmatched: int = 0

    @property
    def total(self) -> int:
        """Return the number of warning categories present for the run."""
        return self.path + self.key + self.duplicate + self.unmatched

    def as_dict(self) -> dict[str, int]:
        """Return counters keyed by their stable category names."""
        return {category: getattr(self, category) for category in _WARNING_CATEGORIES}


@dataclass(frozen=True, slots=True)
class RosterJoinResult:
    """Role and metadata produced by applying one run roster to one event."""

    role: str | None
    metadata: dict[str, object]
    matched: bool
    unmatched: bool


@dataclass(frozen=True, slots=True)
class _RosterEntry:
    role: str | None
    attributes: dict[str, object]


@dataclass(slots=True)
class RosterTable:
    """One run's normalized lookup table and warning latches."""

    _entries: dict[str, _RosterEntry]
    _available: bool
    _warnings: set[RosterWarningCategory] = field(default_factory=set)

    @property
    def available(self) -> bool:
        """Return whether at least one usable roster key was found."""
        return self._available

    @property
    def warning_categories(self) -> frozenset[RosterWarningCategory]:
        """Return the warning categories observed so far for this run."""
        return frozenset(self._warnings)

    @property
    def warnings(self) -> RosterWarningCounts:
        """Return warning flags as named integer counters."""
        return RosterWarningCounts(
            path=int("path" in self._warnings),
            key=int("key" in self._warnings),
            duplicate=int("duplicate" in self._warnings),
            unmatched=int("unmatched" in self._warnings),
        )

    def apply(
        self,
        agent_id: object,
        role: str | None,
        metadata: Mapping[str, object],
    ) -> RosterJoinResult:
        """Join one event while latching an unmatched warning at most once."""
        if agent_id is None or not self._available:
            return RosterJoinResult(role, dict(metadata), matched=False, unmatched=False)
        try:
            key = str(agent_id)
        except (OverflowError, TypeError, ValueError):
            self._warnings.add("unmatched")
            return RosterJoinResult(role, dict(metadata), matched=False, unmatched=True)

        entry = self._entries.get(key)
        if entry is None:
            self._warnings.add("unmatched")
            return RosterJoinResult(role, dict(metadata), matched=False, unmatched=True)

        joined_role = role if role is not None else entry.role
        joined_metadata = dict(metadata)
        if entry.attributes:
            engine_value = joined_metadata.get("_retrace")
            engine_metadata = (
                dict(engine_value) if isinstance(engine_value, Mapping) else {}
            )
            engine_metadata["agent"] = dict(entry.attributes)
            joined_metadata["_retrace"] = engine_metadata
        return RosterJoinResult(
            joined_role,
            joined_metadata,
            matched=True,
            unmatched=False,
        )

    def join(
        self,
        agent_id: object,
        role: str | None,
        metadata: Mapping[str, object],
    ) -> RosterJoinResult:
        """Alias for :meth:`apply`."""
        return self.apply(agent_id, role, metadata)


RunRoster = RosterTable
RosterMatch = RosterJoinResult


def _attribute_path(name: str) -> str:
    escaped = name.encode("unicode_escape").decode("ascii")
    return f"agents.attributes.{escaped}"


class RosterJoin:
    """Compile one roster config and build independent lookup tables per run."""

    def __init__(self, config: AgentsConfig) -> None:
        self.config = config
        self._path = _compile_expression("agents.path", config.path)
        self._key = _compile_expression("agents.key", config.key)
        self._attributes = {
            name: _compile_expression(_attribute_path(name), expression)
            for name, expression in config.attributes.items()
        }

    @staticmethod
    def _search(expression: _CompiledExpression, value: object) -> object:
        try:
            return expression.search(value)
        except JMESPathError:
            return None

    def _entry(self, value: object) -> _RosterEntry:
        role: str | None = None
        attributes: dict[str, object] = {}
        for name, expression in self._attributes.items():
            extracted = self._search(expression, value)
            if extracted is None:
                continue
            if name == "role":
                joined_role, warned = _coerce_string(extracted)
                if not warned:
                    role = joined_role
            else:
                attributes[name] = extracted
        return _RosterEntry(role=role, attributes=attributes)

    def build(self, run_record: Mapping[str, object]) -> RosterTable:
        """Build one normalized, first-entry-wins table for a run record."""
        warnings: set[RosterWarningCategory] = set()
        roster = self._search(self._path, run_record)
        if not isinstance(roster, list):
            warnings.add("path")
            return RosterTable({}, False, warnings)

        entries: dict[str, _RosterEntry] = {}
        for raw_entry in roster:
            raw_key = self._search(self._key, raw_entry)
            if raw_key is None:
                continue
            try:
                key = str(raw_key)
            except (OverflowError, TypeError, ValueError):
                continue
            if key in entries:
                warnings.add("duplicate")
                continue
            entries[key] = self._entry(raw_entry)

        if not entries:
            warnings.add("key")
            return RosterTable({}, False, warnings)
        return RosterTable(entries, True, warnings)

    def build_table(self, run_record: Mapping[str, object]) -> RosterTable:
        """Alias for :meth:`build`."""
        return self.build(run_record)

    def for_run(self, run_record: Mapping[str, object]) -> RosterTable:
        """Alias for :meth:`build`."""
        return self.build(run_record)

    @staticmethod
    def apply(
        table: RosterTable,
        agent_id: object,
        role: str | None,
        metadata: Mapping[str, object],
    ) -> RosterJoinResult:
        """Apply a previously built table to one event's joinable values."""
        return table.apply(agent_id, role, metadata)


AgentRosterJoin = RosterJoin
