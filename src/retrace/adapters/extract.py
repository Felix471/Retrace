"""Format-neutral extraction from parsed records using a mapping config."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, TypeAlias, TypeVar

import jmespath
from jmespath.exceptions import JMESPathError

from retrace.adapters.mapping_schema import (
    EventFieldsConfig,
    EventTypeMapping,
    MappingConfig,
    MappingConfigError,
)
from retrace.core.model import coerce_event_type, parse_timestamp

__all__ = [
    "EventFields",
    "ExtractedEventFields",
    "ExtractedRunFields",
    "ExtractionStats",
    "Extractor",
    "FieldStats",
    "RunFields",
    "SlotStats",
]


class _CompiledExpression(Protocol):
    def search(self, value: object) -> object:
        """Evaluate this expression against one value."""


@dataclass(frozen=True, slots=True)
class EventFields:
    """Typed values extracted from one event record."""

    turn: int | None
    timestamp: datetime | None
    agent_id: str | None
    role: str | None
    type: str
    phase: str | None
    content: str | None
    tokens_in: int | None
    tokens_out: int | None
    cost: float | None
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class RunFields:
    """Values extracted from one run-level record."""

    metadata: dict[str, object]
    outcome: str | None


# Descriptive aliases make the return types easy to discover without constraining callers
# to one naming convention.
ExtractedEventFields = EventFields
ExtractedRunFields = RunFields


@dataclass(slots=True)
class FieldStats:
    """Extraction counters for one configured field."""

    hits: int = 0
    misses: int = 0
    failures: int = 0


SlotStats = FieldStats


_EVENT_SLOTS = (
    "turn",
    "timestamp",
    "agent_id",
    "role",
    "type",
    "phase",
    "content",
    "tokens_in",
    "tokens_out",
    "cost",
)


@dataclass(slots=True)
class ExtractionStats:
    """Counters accumulated by an :class:`Extractor`."""

    filtered_records: int = 0
    fields: dict[str, FieldStats] = field(default_factory=dict)

    @property
    def slots(self) -> dict[str, FieldStats]:
        """Alias for the path-keyed field counters."""
        return self.fields

    @property
    def filtered(self) -> int:
        """Short alias for the number of records rejected by ``where``."""
        return self.filtered_records

    @property
    def total_warnings(self) -> int:
        """Return the total number of extraction failures."""
        return sum(counter.failures for counter in self.fields.values())

    def for_slot(self, path: str) -> FieldStats:
        """Return a counter by full path or by a bare event-slot name."""
        resolved = path if "." in path else f"event.{path}"
        return self.fields.setdefault(resolved, FieldStats())

    def _record_hit(self, path: str) -> None:
        self.for_slot(path).hits += 1

    def _record_miss(self, path: str) -> None:
        self.for_slot(path).misses += 1

    def _record_failure(self, path: str) -> None:
        self.for_slot(path).failures += 1


_T = TypeVar("_T")
_Coercer: TypeAlias = Callable[[object], tuple[_T | None, bool]]
_EVALUATION_FAILED = object()
_LEADING_IDENTIFIER = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?=$|[.\[])")


def _one_line(value: object) -> str:
    return " ".join(str(value).split())


def _metadata_path(name: str) -> str:
    escaped = name.encode("unicode_escape").decode("ascii")
    return f"run.metadata.{escaped}"


def _coerce_integer(value: object) -> tuple[int | None, bool]:
    if isinstance(value, bool):
        return None, True
    if isinstance(value, int):
        return value, False
    if isinstance(value, float):
        if value.is_integer():
            return int(value), False
        return None, True
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.isdigit():
            try:
                return int(candidate), False
            except ValueError:
                return None, True
    return None, True


def _coerce_float(value: object) -> tuple[float | None, bool]:
    if isinstance(value, bool):
        return None, True
    if isinstance(value, (int, float)):
        try:
            return float(value), False
        except OverflowError:
            return None, True
    if isinstance(value, str):
        try:
            return float(value.strip()), False
        except (OverflowError, ValueError):
            pass
    return None, True


def _coerce_string(value: object) -> tuple[str | None, bool]:
    if isinstance(value, str):
        return value, False
    if isinstance(value, bool):
        return None, True
    if isinstance(value, (int, float)):
        try:
            return str(value), False
        except (OverflowError, ValueError):
            return None, True
    return None, True


def _coerce_timestamp(value: object) -> tuple[datetime | None, bool]:
    return parse_timestamp(value)  # type: ignore[arg-type]


def _is_jmespath_truthy(value: object) -> bool:
    if value is None or value is False:
        return False
    return not (isinstance(value, (str, list, dict)) and not value)


def _leading_identifier(expression: str) -> str | None:
    match = _LEADING_IDENTIFIER.match(expression.strip())
    return None if match is None else match.group(1)


def _compile_expression(path: str, expression: str) -> _CompiledExpression:
    try:
        return jmespath.compile(expression)
    except JMESPathError as error:
        reason = _one_line(error)
        raise MappingConfigError(f"{path}: invalid JMESPath expression: {reason}") from None


_FieldStatus: TypeAlias = Literal["unmapped", "hit", "miss", "failure"]


@dataclass(frozen=True, slots=True)
class _EventFieldExtraction:
    fields: EventFields
    statuses: dict[str, _FieldStatus]


class _EventFieldPlan:
    """Compiled event-field mappings shared by flat and multi-source extraction."""

    def __init__(
        self,
        config: EventFieldsConfig,
        stats: ExtractionStats,
        *,
        error_prefix: str,
        stats_prefix: str,
    ) -> None:
        self.config = config
        self.stats = stats
        self.stats_prefix = stats_prefix
        self._expressions: dict[str, _CompiledExpression] = {}
        self._expression_text: dict[str, str] = {}
        self._type_map: dict[str, str] | None = None
        self._type_default: str | None = None

        for slot in _EVENT_SLOTS:
            stats.fields.setdefault(f"{stats_prefix}.{slot}", FieldStats())
            configured = getattr(config, slot)
            if slot == "type" and isinstance(configured, EventTypeMapping):
                expression = configured.from_
                error_path = f"{error_prefix}.type.from"
                self._type_map = {str(key): value for key, value in configured.map.items()}
                self._type_default = configured.default
            elif isinstance(configured, str):
                expression = configured
                error_path = f"{error_prefix}.{slot}"
            else:
                continue
            self._expressions[slot] = _compile_expression(error_path, expression)
            self._expression_text[slot] = expression

        self._rest_metadata = config.metadata == "rest"
        self._consumed_top_level = {
            identifier
            for expression in self._expression_text.values()
            if (identifier := _leading_identifier(expression)) is not None
        }

    def _stats_path(self, slot: str) -> str:
        return f"{self.stats_prefix}.{slot}"

    def _search(self, slot: str, data: object) -> object:
        try:
            return self._expressions[slot].search(data)
        except JMESPathError:
            self.stats._record_failure(self._stats_path(slot))
            return _EVALUATION_FAILED

    def _extract_value(
        self,
        slot: str,
        data: object,
        coercer: _Coercer[_T],
    ) -> tuple[_T | None, _FieldStatus]:
        if slot not in self._expressions:
            return None, "unmapped"
        raw = self._search(slot, data)
        if raw is _EVALUATION_FAILED:
            return None, "failure"
        if raw is None:
            self.stats._record_miss(self._stats_path(slot))
            return None, "miss"
        value, warned = coercer(raw)
        if warned:
            self.stats._record_failure(self._stats_path(slot))
            return None, "failure"
        self.stats._record_hit(self._stats_path(slot))
        return value, "hit"

    def _extract_type(self, data: object) -> tuple[str, _FieldStatus]:
        configured = self.config.type
        if configured is None or "type" not in self._expressions:
            return "other", "unmapped"

        raw = self._search("type", data)
        if raw is _EVALUATION_FAILED:
            return "other", "failure"
        if raw is None:
            self.stats._record_miss(self._stats_path("type"))
            return "other", "miss"

        if isinstance(configured, EventTypeMapping):
            try:
                key = str(raw)
            except (OverflowError, ValueError):
                self.stats._record_failure(self._stats_path("type"))
                return "other", "failure"
            candidate = (self._type_map or {}).get(key, self._type_default)
            if candidate is None:
                self.stats._record_failure(self._stats_path("type"))
                return "other", "failure"
        else:
            candidate = raw

        event_type, warned = coerce_event_type(candidate)
        if warned:
            self.stats._record_failure(self._stats_path("type"))
            return event_type, "failure"
        self.stats._record_hit(self._stats_path("type"))
        return event_type, "hit"

    def extract_missing(self) -> _EventFieldExtraction:
        """Record mapped fields as missing without evaluating a non-object value."""
        statuses: dict[str, _FieldStatus] = {slot: "unmapped" for slot in _EVENT_SLOTS}
        for slot in self._expressions:
            self.stats._record_miss(self._stats_path(slot))
            statuses[slot] = "miss"
        return _EventFieldExtraction(
            fields=EventFields(
                turn=None,
                timestamp=None,
                agent_id=None,
                role=None,
                type="other",
                phase=None,
                content=None,
                tokens_in=None,
                tokens_out=None,
                cost=None,
                metadata={},
            ),
            statuses=statuses,
        )

    def extract(self, data: Mapping[str, object]) -> _EventFieldExtraction:
        """Extract all configured slots and retain each slot's extraction status."""
        statuses: dict[str, _FieldStatus] = {}
        turn, statuses["turn"] = self._extract_value("turn", data, _coerce_integer)
        timestamp, statuses["timestamp"] = self._extract_value(
            "timestamp", data, _coerce_timestamp
        )
        agent_id, statuses["agent_id"] = self._extract_value(
            "agent_id", data, _coerce_string
        )
        role, statuses["role"] = self._extract_value("role", data, _coerce_string)
        event_type, statuses["type"] = self._extract_type(data)
        phase, statuses["phase"] = self._extract_value("phase", data, _coerce_string)
        content, statuses["content"] = self._extract_value(
            "content", data, _coerce_string
        )
        tokens_in, statuses["tokens_in"] = self._extract_value(
            "tokens_in", data, _coerce_integer
        )
        tokens_out, statuses["tokens_out"] = self._extract_value(
            "tokens_out", data, _coerce_integer
        )
        cost, statuses["cost"] = self._extract_value("cost", data, _coerce_float)
        metadata = (
            {
                key: value
                for key, value in data.items()
                if key not in self._consumed_top_level
            }
            if self._rest_metadata
            else {}
        )
        return _EventFieldExtraction(
            fields=EventFields(
                turn=turn,
                timestamp=timestamp,
                agent_id=agent_id,
                role=role,
                type=event_type,
                phase=phase,
                content=content,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=cost,
                metadata=metadata,
            ),
            statuses=statuses,
        )


class Extractor:
    """Compile and apply the flat form of one validated mapping config."""

    def __init__(self, config: MappingConfig) -> None:
        if config.event.sources is not None:
            raise MappingConfigError(
                "event.sources: multi-source extraction is not supported by the flat extractor"
            )

        self.config = config
        self.stats = ExtractionStats()
        self._where = self._compile_optional("event.where", config.event.where)
        self._event_plan = _EventFieldPlan(
            config.event,
            self.stats,
            error_prefix="event",
            stats_prefix="event",
        )

        self._run_metadata: dict[str, _CompiledExpression] = {}
        for name, expression in config.run.metadata.items():
            path = _metadata_path(name)
            self.stats.fields[path] = FieldStats()
            self._run_metadata[name] = self._compile(path, expression)

        self.stats.fields["run.outcome"] = FieldStats()
        self._run_outcome = self._compile_optional("run.outcome", config.run.outcome)

    @staticmethod
    def _compile(path: str, expression: str) -> _CompiledExpression:
        return _compile_expression(path, expression)

    @classmethod
    def _compile_optional(
        cls,
        path: str,
        expression: str | None,
    ) -> _CompiledExpression | None:
        return None if expression is None else cls._compile(path, expression)

    def _search(self, path: str, expression: _CompiledExpression, data: object) -> object:
        try:
            return expression.search(data)
        except JMESPathError:
            self.stats._record_failure(path)
            return _EVALUATION_FAILED

    def _extract_value(
        self,
        path: str,
        expression: _CompiledExpression | None,
        data: object,
        coercer: _Coercer[_T],
    ) -> _T | None:
        if expression is None:
            return None
        raw = self._search(path, expression, data)
        if raw is _EVALUATION_FAILED:
            return None
        if raw is None:
            self.stats._record_miss(path)
            return None
        value, warned = coercer(raw)
        if warned:
            self.stats._record_failure(path)
            return None
        self.stats._record_hit(path)
        return value

    def _keeps_record(self, record: Mapping[str, object]) -> bool:
        if self._where is None:
            return True
        raw = self._search("event.where", self._where, record)
        return raw is not _EVALUATION_FAILED and _is_jmespath_truthy(raw)

    def extract_event_fields(self, record: dict[str, object]) -> EventFields | None:
        """Extract flat event fields, or return ``None`` when ``where`` rejects the record."""
        if not self._keeps_record(record):
            self.stats.filtered_records += 1
            return None
        return self._event_plan.extract(record).fields

    def extract_run_fields(self, data: dict[str, object]) -> RunFields:
        """Extract configured run metadata and outcome from one parsed record."""
        metadata: dict[str, object] = {}
        for name, expression in self._run_metadata.items():
            path = _metadata_path(name)
            raw = self._search(path, expression, data)
            if raw is _EVALUATION_FAILED:
                metadata[name] = None
            elif raw is None:
                self.stats._record_miss(path)
                metadata[name] = None
            else:
                self.stats._record_hit(path)
                metadata[name] = raw

        outcome = self._extract_value(
            "run.outcome", self._run_outcome, data, _coerce_string
        )
        return RunFields(metadata=metadata, outcome=outcome)
