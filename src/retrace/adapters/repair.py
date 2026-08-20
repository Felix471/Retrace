"""Reusable declarative repairs for extracted source records."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from jmespath.exceptions import JMESPathError

from retrace.adapters.extract import (
    _coerce_float,
    _coerce_integer,
    _coerce_string,
    _coerce_timestamp,
    _compile_expression,
    _CompiledExpression,
)
from retrace.adapters.mapping_schema import (
    DeriveRepairConfig,
    MappingConfigError,
    OrdinalRepairConfig,
    RepairRule,
)
from retrace.core.model import coerce_event_type

__all__ = [
    "CompiledRepairRules",
    "RepairEngine",
    "RepairPlan",
    "RepairResult",
    "RepairTarget",
]

RepairTargetKind: TypeAlias = Literal["slot", "metadata"]
_SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class RepairTarget:
    """Resolved destination for one configured repair field."""

    field: str
    kind: RepairTargetKind
    record_key: str | None


@dataclass(frozen=True, slots=True)
class _CompiledRule:
    config: RepairRule
    target: RepairTarget
    expression: _CompiledExpression | None


@dataclass(frozen=True, slots=True)
class RepairResult:
    """Pure result of applying one repair plan to one source record."""

    slot_values: dict[str, object]
    metadata: dict[str, object]
    repaired_values: dict[str, object]
    originals: dict[str, object]
    fired: tuple[bool, ...]
    evaluation_failures: tuple[bool, ...]
    fired_slot_fields: frozenset[str]
    record_view: object

    @property
    def record_repaired(self) -> bool:
        """Return whether at least one rule changed its target."""
        return any(self.fired)

    @property
    def fired_fields(self) -> frozenset[str]:
        """Return the configured fields changed by at least one rule."""
        return frozenset(self.repaired_values)

    @property
    def repaired_record(self) -> object:
        """Alias for the shallow repaired view used by fallback rendering."""
        return self.record_view


def _coerce_slot_value(slot: str, value: object) -> tuple[object, bool]:
    if value is None and slot != "type":
        return None, False
    if slot in {"turn", "tokens_in", "tokens_out"}:
        return _coerce_integer(value)
    if slot == "timestamp":
        return _coerce_timestamp(value)
    if slot in {"agent_id", "role", "phase", "content"}:
        return _coerce_string(value)
    if slot == "cost":
        return _coerce_float(value)
    if slot == "type":
        return coerce_event_type(value)
    return None, True


class RepairPlan:
    """Compile and apply ordinal/derive rules without mutating input records."""

    def __init__(
        self,
        rules: Sequence[RepairRule],
        *,
        mapped_slots: Mapping[str, str] | None = None,
        error_prefix: str = "repairs",
    ) -> None:
        self.mapped_slots = dict(mapped_slots or {})
        compiled: list[_CompiledRule] = []
        first_field_indexes: dict[str, int] = {}
        for index, rule in enumerate(rules):
            first_index = first_field_indexes.setdefault(rule.field, index)
            if first_index != index:
                raise MappingConfigError(
                    f"{error_prefix}[{index}].field: duplicate repair field "
                    f"{rule.field!r}; first used at index {first_index}"
                )
            if rule.field == "_retrace":
                raise MappingConfigError(
                    f"{error_prefix}[{index}].field: '_retrace' is reserved for engine metadata"
                )
            source_expression = self.mapped_slots.get(rule.field)
            record_key = None
            if source_expression is not None:
                candidate = source_expression.strip()
                if _SIMPLE_IDENTIFIER.fullmatch(candidate):
                    record_key = candidate
            target = RepairTarget(
                field=rule.field,
                kind="slot" if source_expression is not None else "metadata",
                record_key=record_key,
            )
            expression = (
                _compile_expression(f"{error_prefix}[{index}].expr", rule.expr)
                if isinstance(rule, DeriveRepairConfig)
                else None
            )
            compiled.append(_CompiledRule(rule, target, expression))
        self.rules = tuple(compiled)

    def target_for(self, field: str) -> RepairTarget:
        """Return the first configured target for a field."""
        for rule in self.rules:
            if rule.target.field == field:
                return rule.target
        raise KeyError(field)

    @staticmethod
    def _stored_value(
        target: RepairTarget,
        record: object,
        slot_values: Mapping[str, object],
        metadata: Mapping[str, object],
    ) -> object:
        if target.kind == "slot":
            return slot_values.get(target.field)
        if target.field in metadata:
            return metadata[target.field]
        if isinstance(record, Mapping):
            return record.get(target.field)
        return None

    @staticmethod
    def _computed_value(
        rule: _CompiledRule,
        record: object,
        source_ordinal: int,
    ) -> tuple[object, bool]:
        if isinstance(rule.config, OrdinalRepairConfig):
            return source_ordinal + rule.config.base, False

        assert rule.expression is not None
        try:
            value = rule.expression.search(record)
        except JMESPathError:
            return None, True
        if rule.config.map is None:
            return value, False
        try:
            key = str(value)
        except (OverflowError, ValueError):
            return None, True
        if key not in rule.config.map:
            return None, True
        return rule.config.map[key], False

    def apply(
        self,
        record: object,
        source_ordinal: int,
        *,
        slot_values: Mapping[str, object] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> RepairResult:
        """Apply all rules and return repaired values plus complete audit flags."""
        final_slots = dict(slot_values or {})
        final_metadata = dict(metadata or {})
        record_view: object = dict(record) if isinstance(record, dict) else record
        repaired_values: dict[str, object] = {}
        originals: dict[str, object] = {}
        fired_flags: list[bool] = []
        failure_flags: list[bool] = []
        fired_slot_fields: set[str] = set()

        for rule in self.rules:
            stored = self._stored_value(
                rule.target,
                record,
                final_slots,
                final_metadata,
            )
            raw_computed, failed = self._computed_value(rule, record, source_ordinal)
            computed = raw_computed
            if not failed and rule.target.kind == "slot":
                computed, failed = _coerce_slot_value(rule.target.field, raw_computed)
            if failed:
                fired_flags.append(False)
                failure_flags.append(True)
                continue
            if stored == computed:
                fired_flags.append(False)
                failure_flags.append(False)
                continue

            originals.setdefault(rule.target.field, stored)
            repaired_values[rule.target.field] = computed
            if rule.target.kind == "slot":
                final_slots[rule.target.field] = computed
                fired_slot_fields.add(rule.target.field)
                if isinstance(record_view, dict) and rule.target.record_key is not None:
                    record_view[rule.target.record_key] = raw_computed
            else:
                final_metadata[rule.target.field] = computed
                if isinstance(record_view, dict):
                    record_view[rule.target.field] = raw_computed
            fired_flags.append(True)
            failure_flags.append(False)

        return RepairResult(
            slot_values=final_slots,
            metadata=final_metadata,
            repaired_values=repaired_values,
            originals=originals,
            fired=tuple(fired_flags),
            evaluation_failures=tuple(failure_flags),
            fired_slot_fields=frozenset(fired_slot_fields),
            record_view=record_view,
        )


RepairEngine = RepairPlan
CompiledRepairRules = RepairPlan
