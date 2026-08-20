"""Explode and deterministically merge events from parallel record arrays."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import cast

from jmespath.exceptions import JMESPathError

from retrace.adapters.extract import (
    _EVENT_SLOTS,
    ExtractionStats,
    FieldStats,
    _compile_expression,
    _CompiledExpression,
    _EventFieldPlan,
    _one_line,
)
from retrace.adapters.mapping_schema import (
    EventSourceConfig,
    EventTypeMapping,
    MappingConfig,
    MappingConfigError,
)
from retrace.adapters.repair import RepairPlan, RepairResult

__all__ = [
    "MergedEvent",
    "MultiSourceEvent",
    "MultiSourceExtractor",
    "MultiSourceStats",
]


@dataclass(frozen=True, slots=True)
class MultiSourceEvent:
    """One typed event after source explosion and deterministic merging."""

    ordinal: int
    turn: int | None
    timestamp: datetime | None
    agent_id: str | None
    role: str | None
    type: str
    phase: str | None
    content: str
    structured: dict[str, object] | None
    tokens_in: int | None
    tokens_out: int | None
    cost: float | None
    metadata: dict[str, object]


MergedEvent = MultiSourceEvent


@dataclass(slots=True)
class MultiSourceStats(ExtractionStats):
    """Counters accumulated while exploding and merging source arrays."""

    source_record_counts: dict[str, int] = field(default_factory=dict)
    records_in_source_arrays: int = 0
    none_sort_key_events: int = 0
    sources_without_arrays: int = 0
    provenance_collisions: int = 0
    repair_fire_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    repair_evaluation_failures: dict[tuple[str, str], int] = field(default_factory=dict)
    n_repaired_by_source: dict[str, int] = field(default_factory=dict)
    n_repaired: int = 0

    @property
    def source_records(self) -> dict[str, int]:
        """Alias for per-source exploded record counts."""
        return self.source_record_counts

    @property
    def total_warnings(self) -> int:
        """Return extraction, sorting, repair, and repair-evaluation warnings."""
        field_failures = sum(counter.failures for counter in self.fields.values())
        repair_failures = sum(self.repair_evaluation_failures.values())
        return field_failures + self.none_sort_key_events + repair_failures + self.n_repaired

    @property
    def repaired_record_counts(self) -> dict[str, int]:
        """Alias for per-source repaired-record counts."""
        return self.n_repaired_by_source

    @property
    def rule_evaluation_failures(self) -> dict[tuple[str, str], int]:
        """Alias for failures keyed by source name and repair field."""
        return self.repair_evaluation_failures

    def for_source_slot(self, source: str, slot: str) -> FieldStats:
        """Return the counter for one source path or extracted slot."""
        return self.for_slot(f"event.sources.{source}.{slot}")


@dataclass(frozen=True, slots=True)
class _SourcePlan:
    config: EventSourceConfig
    path: _CompiledExpression
    fields: _EventFieldPlan
    repairs: RepairPlan
    stats_prefix: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    event: MultiSourceEvent
    priority: int
    source_ordinal: int


def _mapped_slots(source: EventSourceConfig) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for slot in _EVENT_SLOTS:
        configured = getattr(source.fields, slot)
        if isinstance(configured, EventTypeMapping):
            mapped[slot] = configured.from_
        elif isinstance(configured, str):
            mapped[slot] = configured
    return mapped


class MultiSourceExtractor:
    """Compile, explode, and merge the source form of a validated config."""

    def __init__(self, config: MappingConfig) -> None:
        sources = config.event.sources
        if sources is None:
            raise MappingConfigError(
                "event.sources: multi-source extractor requires the sources form"
            )

        self.config = config
        self.stats = MultiSourceStats()
        self._sources: list[_SourcePlan] = []
        for index, source in enumerate(sources):
            stats_prefix = f"event.sources.{source.name}"
            self.stats.source_record_counts[source.name] = 0
            self.stats.n_repaired_by_source[source.name] = 0
            self.stats.fields[f"{stats_prefix}.path"] = FieldStats()
            self.stats.fields[f"{stats_prefix}.metadata"] = FieldStats()
            path = _compile_expression(f"event.sources[{index}].path", source.path)
            fields = _EventFieldPlan(
                source.fields,
                self.stats,
                error_prefix=f"event.sources[{index}].fields",
                stats_prefix=stats_prefix,
            )
            repairs = RepairPlan(
                source.repairs,
                mapped_slots=_mapped_slots(source),
                error_prefix=f"event.sources[{index}].repairs",
            )
            for rule in source.repairs:
                key = (source.name, rule.field)
                self.stats.repair_fire_counts.setdefault(key, 0)
                self.stats.repair_evaluation_failures.setdefault(key, 0)
            self._sources.append(
                _SourcePlan(
                    config=source,
                    path=path,
                    fields=fields,
                    repairs=repairs,
                    stats_prefix=stats_prefix,
                )
            )

        self._sort_by = config.event.merge.sort_by if config.event.merge is not None else None
        if self._sort_by is not None and self._sort_by not in _EVENT_SLOTS:
            valid_slots = ", ".join(_EVENT_SLOTS)
            value = _one_line(repr(self._sort_by))
            raise MappingConfigError(
                f"event.merge.sort_by: unknown event slot {value}; expected one of: {valid_slots}"
            )

    def _source_records(
        self,
        source: _SourcePlan,
        run_record: dict[str, object],
    ) -> list[object] | None:
        path_stats = self.stats.fields[f"{source.stats_prefix}.path"]
        try:
            raw = source.path.search(run_record)
        except JMESPathError:
            path_stats.failures += 1
            self.stats.sources_without_arrays += 1
            return None
        if raw is None:
            path_stats.misses += 1
            self.stats.sources_without_arrays += 1
            return None
        if not isinstance(raw, list):
            path_stats.failures += 1
            self.stats.sources_without_arrays += 1
            return None
        path_stats.hits += 1
        count = len(raw)
        self.stats.source_record_counts[source.config.name] += count
        self.stats.records_in_source_arrays += count
        return raw

    def _candidate(
        self,
        source: _SourcePlan,
        record: object,
        source_ordinal: int,
    ) -> _Candidate:
        is_object = isinstance(record, dict)
        extracted = source.fields.extract(record) if is_object else source.fields.extract_missing()
        fields = extracted.fields
        statuses = extracted.statuses

        event_type = (
            fields.type
            if statuses["type"] == "hit"
            else (source.config.type or "other")
        )
        phase = fields.phase if statuses["phase"] == "hit" else source.config.phase
        role = fields.role if statuses["role"] == "hit" else source.config.role
        metadata = dict(fields.metadata)
        collision = is_object and "_retrace" in record
        metadata.pop("_retrace", None)
        if collision:
            self.stats.fields[f"{source.stats_prefix}.metadata"].failures += 1
            self.stats.provenance_collisions += 1

        repair_result = source.repairs.apply(
            record,
            source_ordinal,
            slot_values={
                "turn": fields.turn,
                "timestamp": fields.timestamp,
                "agent_id": fields.agent_id,
                "role": role,
                "type": event_type,
                "phase": phase,
                "content": fields.content,
                "tokens_in": fields.tokens_in,
                "tokens_out": fields.tokens_out,
                "cost": fields.cost,
            },
            metadata=metadata,
        )
        self._record_repairs(source, repair_result)

        repaired_content = repair_result.slot_values["content"]
        has_mapped_content = (is_object and statuses["content"] == "hit") or (
            "content" in repair_result.fired_slot_fields
        )
        if has_mapped_content and isinstance(repaired_content, str):
            content = repaired_content
        else:
            content = json.dumps(repair_result.record_view, indent=2, ensure_ascii=False)
        structured = (
            record
            if is_object and (repair_result.record_repaired or not has_mapped_content)
            else None
        )

        metadata = repair_result.metadata
        engine_metadata: dict[str, object] = {
            "source": source.config.name,
            "source_ordinal": source_ordinal,
        }
        if repair_result.originals:
            engine_metadata["repaired"] = dict(repair_result.originals)
        metadata["_retrace"] = engine_metadata

        slot_values = repair_result.slot_values

        return _Candidate(
            event=MultiSourceEvent(
                ordinal=-1,
                turn=cast(int | None, slot_values["turn"]),
                timestamp=cast(datetime | None, slot_values["timestamp"]),
                agent_id=cast(str | None, slot_values["agent_id"]),
                role=cast(str | None, slot_values["role"]),
                type=cast(str, slot_values["type"]),
                phase=cast(str | None, slot_values["phase"]),
                content=content,
                structured=structured,
                tokens_in=cast(int | None, slot_values["tokens_in"]),
                tokens_out=cast(int | None, slot_values["tokens_out"]),
                cost=cast(float | None, slot_values["cost"]),
                metadata=metadata,
            ),
            priority=source.config.priority,
            source_ordinal=source_ordinal,
        )

    def _record_repairs(self, source: _SourcePlan, result: RepairResult) -> None:
        for rule, fired, failed in zip(
            source.repairs.rules,
            result.fired,
            result.evaluation_failures,
            strict=True,
        ):
            key = (source.config.name, rule.config.field)
            if fired:
                self.stats.repair_fire_counts[key] += 1
            if failed:
                self.stats.repair_evaluation_failures[key] += 1
        if result.record_repaired:
            self.stats.n_repaired_by_source[source.config.name] += 1
            self.stats.n_repaired += 1

    def _ordered_candidates(self, candidates: list[_Candidate]) -> list[_Candidate]:
        if self._sort_by is None:
            return sorted(candidates, key=lambda item: (item.priority, item.source_ordinal))

        sort_by = self._sort_by
        for candidate in candidates:
            if getattr(candidate.event, sort_by) is None:
                self.stats.none_sort_key_events += 1

        def sort_key(candidate: _Candidate) -> tuple[object, ...]:
            value = getattr(candidate.event, sort_by)
            if value is None:
                return (1, 0, candidate.priority, candidate.source_ordinal)
            return (0, value, candidate.priority, candidate.source_ordinal)

        return sorted(candidates, key=sort_key)

    def extract_events(self, run_record: dict[str, object]) -> list[MultiSourceEvent]:
        """Explode every declared array and return one merged, gaplessly ordered list."""
        candidates: list[_Candidate] = []
        for source in self._sources:
            records = self._source_records(source, run_record)
            if records is None:
                continue
            candidates.extend(
                self._candidate(source, record, source_ordinal)
                for source_ordinal, record in enumerate(records)
            )

        ordered = self._ordered_candidates(candidates)
        return [replace(candidate.event, ordinal=ordinal) for ordinal, candidate in enumerate(ordered)]
