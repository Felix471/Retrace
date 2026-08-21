"""Validated schema and loader for declarative mapping configuration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import ErrorDetails, InitErrorDetails, PydanticCustomError

SUPPORTED_MAPPING_VERSION = 1
VALID_DISCOVERY_UNITS = ("file", "dir", "line")
VALID_EVENT_TYPES = (
    "message",
    "tool_call",
    "tool_result",
    "system",
    "other",
)
VALID_REPAIR_STRATEGIES = ("ordinal", "derive")

DiscoveryUnit: TypeAlias = Literal["file", "dir", "line", "json"]
EventType: TypeAlias = Literal[
    "message",
    "tool_call",
    "tool_result",
    "system",
    "other",
]
RepairStrategy: TypeAlias = Literal["ordinal", "derive"]
ScalarValue: TypeAlias = str | int | float | bool | None

__all__ = [
    "SUPPORTED_MAPPING_VERSION",
    "VALID_DISCOVERY_UNITS",
    "VALID_EVENT_TYPES",
    "VALID_REPAIR_STRATEGIES",
    "AgentsConfig",
    "DeriveRepairConfig",
    "DiscoveryUnit",
    "EventConfig",
    "EventFieldsConfig",
    "EventSourceConfig",
    "EventType",
    "EventTypeMapping",
    "MappingConfig",
    "MappingConfigError",
    "MergeConfig",
    "OrdinalRepairConfig",
    "RepairRule",
    "RepairStrategy",
    "RunConfig",
    "RunDiscoveryConfig",
    "ScalarValue",
    "SniffConfig",
    "load_mapping_config",
    "validate_mapping_config",
]


class MappingConfigError(ValueError):
    """Raised when mapping configuration cannot be loaded or validated."""


def _located_validation_error(
    title: str,
    code: str,
    message: str,
    location: tuple[int | str, ...],
    value: object,
) -> ValidationError:
    detail: InitErrorDetails = {
        "type": PydanticCustomError(code, message),
        "loc": location,
        "input": value,
    }
    return ValidationError.from_exception_data(title, [detail])


class _StrictConfigModel(BaseModel):
    """Shared strict settings for every mapping schema block."""

    model_config = ConfigDict(extra="forbid", strict=True, loc_by_alias=True)


class EventTypeMapping(_StrictConfigModel):
    """Map source values from one expression into closed event types."""

    from_: str = Field(alias="from")
    map: dict[str, EventType] = Field(default_factory=dict)
    default: EventType | None = None

    @field_validator("default", mode="before")
    @classmethod
    def _reject_null_default(cls, value: object) -> object:
        if value is None:
            raise ValueError("must be one of the valid event types")
        return value


class EventFieldsConfig(_StrictConfigModel):
    """Extraction expressions shared by flat and multi-source events."""

    turn: str | None = None
    timestamp: str | None = None
    agent_id: str | None = None
    role: str | None = None
    type: str | EventTypeMapping | None = None
    phase: str | None = None
    content: str | None = None
    tokens_in: str | None = None
    tokens_out: str | None = None
    cost: str | None = None
    metadata: Literal["rest"] | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _validate_type_form(cls, value: object) -> object:
        if isinstance(value, EventTypeMapping):
            return value
        if isinstance(value, Mapping):
            return EventTypeMapping.model_validate(value)
        if not isinstance(value, str):
            raise PydanticCustomError(
                "event_type_form",
                "must be a string or an object with a required 'from' key",
            )
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _validate_metadata_mode(cls, value: object) -> object:
        if value != "rest":
            raise ValueError("must be the literal string 'rest'")
        return value


class OrdinalRepairConfig(_StrictConfigModel):
    """Repair one field from its source position."""

    field: str
    strategy: Literal["ordinal"]
    base: int = 0


def _stringify_scalar_key(value: object) -> str:
    if value is None:
        return "None"
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    raise PydanticCustomError("repair_map_key", "map keys must be scalar values")


class DeriveRepairConfig(_StrictConfigModel):
    """Repair one field from an expression over its source record."""

    field: str
    strategy: Literal["derive"]
    expr: str
    map: dict[str, ScalarValue] | None = None

    @field_validator("map", mode="before")
    @classmethod
    def _stringify_map_keys(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {_stringify_scalar_key(key): item for key, item in value.items()}


RepairRule: TypeAlias = Annotated[
    OrdinalRepairConfig | DeriveRepairConfig,
    Field(discriminator="strategy"),
]


class EventSourceConfig(_StrictConfigModel):
    """One array source contributing events to a run."""

    name: str
    path: str
    type: EventType | None = None
    phase: str | None = None
    role: str | None = None
    priority: int = 0
    fields: EventFieldsConfig = Field(default_factory=EventFieldsConfig)
    repairs: list[RepairRule] = Field(default_factory=list)


class MergeConfig(_StrictConfigModel):
    """Ordering used to merge events from multiple sources."""

    sort_by: str


class RunDiscoveryConfig(_StrictConfigModel):
    """Locate the records belonging to each run."""

    pattern: str
    unit: DiscoveryUnit = "file"
    events_file: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_directory_layout(cls, data: object) -> object:
        if not isinstance(data, Mapping) or data.get("events_file") is None:
            return data
        if "unit" not in data:
            return {**data, "unit": "dir"}
        unit = data.get("unit")
        if unit in ("file", "line", "json"):
            raise _located_validation_error(
                cls.__name__,
                "events_file_unit_conflict",
                f"events_file cannot be combined with unit {unit!r}; use unit 'dir'",
                ("events_file",),
                data.get("events_file"),
            )
        return data


class RunConfig(_StrictConfigModel):
    """Extract run identity, manifest data, metadata, and outcome."""

    id: str
    manifest: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    outcome: str | None = None


_FLAT_EVENT_FIELDS = (
    "where",
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
    "metadata",
)


class EventConfig(EventFieldsConfig):
    """Optional extraction expressions for individual events."""

    where: str | None = None
    sources: list[EventSourceConfig] | None = None
    merge: MergeConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_event_forms(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        sources_present = "sources" in data and data.get("sources") is not None
        flat_fields = [name for name in _FLAT_EVENT_FIELDS if name in data]
        if sources_present and flat_fields:
            names = ", ".join(flat_fields)
            raise _located_validation_error(
                cls.__name__,
                "sources_with_flat_fields",
                f"sources cannot be combined with flat event fields: {names}",
                ("sources",),
                data.get("sources"),
            )
        if data.get("merge") is not None and not sources_present:
            raise _located_validation_error(
                cls.__name__,
                "merge_without_sources",
                "merge requires sources",
                ("merge",),
                data.get("merge"),
            )
        return data

    @field_validator("sources", mode="before")
    @classmethod
    def _validate_sources_present(cls, value: object) -> object:
        if value is None or (isinstance(value, list) and not value):
            raise PydanticCustomError(
                "empty_event_sources",
                "must contain at least one source",
            )
        return value

    @field_validator("sources")
    @classmethod
    def _validate_unique_source_names(
        cls,
        value: list[EventSourceConfig] | None,
    ) -> list[EventSourceConfig] | None:
        if value is None:
            return value
        first_indexes: dict[str, int] = {}
        for index, source in enumerate(value):
            first_index = first_indexes.setdefault(source.name, index)
            if first_index != index:
                raise _located_validation_error(
                    cls.__name__,
                    "duplicate_source_name",
                    f"duplicate source name {source.name!r}; first used at index {first_index}",
                    (index, "name"),
                    source.name,
                )
        return value

    @field_validator("merge", mode="before")
    @classmethod
    def _validate_merge_present(cls, value: object) -> object:
        if value is None:
            raise PydanticCustomError(
                "null_event_merge",
                "must be an object with a required sort_by key",
            )
        return value


class AgentsConfig(_StrictConfigModel):
    """Join per-run agent attributes onto extracted events."""

    path: str
    key: str
    attributes: dict[str, str]

    @field_validator("attributes", mode="before")
    @classmethod
    def _validate_attributes(cls, value: object) -> object:
        if isinstance(value, Mapping) and not value:
            raise PydanticCustomError(
                "empty_agent_attributes",
                "must contain at least one attribute",
            )
        return value


class SniffConfig(_StrictConfigModel):
    """Data-only signature used to identify a mapping from one record."""

    required_fields: list[str]

    @field_validator("required_fields", mode="before")
    @classmethod
    def _validate_required_fields(cls, value: object) -> object:
        if isinstance(value, list) and not value:
            raise PydanticCustomError(
                "empty_sniff_required_fields",
                "must contain at least one field",
            )
        return value


class MappingConfig(_StrictConfigModel):
    """Top-level declarative mapping configuration."""

    retrace_mapping: Literal[1]
    run_discovery: RunDiscoveryConfig
    run: RunConfig
    event: EventConfig
    agents: AgentsConfig | None = None
    sniff: SniffConfig | None = None

    @field_validator("retrace_mapping", mode="before")
    @classmethod
    def _validate_mapping_version(cls, value: object) -> object:
        if type(value) is not int or value != SUPPORTED_MAPPING_VERSION:
            raise ValueError("unsupported mapping version")
        return value

    @field_validator("agents", mode="before")
    @classmethod
    def _validate_agents_present(cls, value: object) -> object:
        if value is None:
            raise PydanticCustomError(
                "null_agents",
                "must be an object with path, key, and attributes",
            )
        return value


def _one_line(value: object) -> str:
    return " ".join(str(value).split())


def _display_value(value: object) -> str:
    return _one_line(repr(value))


def _repair_branch_position(location: tuple[int | str, ...]) -> int | None:
    for index, part in enumerate(location[:-2]):
        if (
            part == "repairs"
            and isinstance(location[index + 1], int)
            and location[index + 2] in VALID_REPAIR_STRATEGIES
        ):
            return index + 2
    return None


def _normalized_error_location(detail: ErrorDetails) -> tuple[int | str, ...]:
    location = list(detail.get("loc", ()))
    error_type = detail["type"]
    if error_type in {"union_tag_invalid", "union_tag_not_found"} and "repairs" in location:
        location.append("strategy")
    branch_position = _repair_branch_position(tuple(location))
    if branch_position is not None:
        del location[branch_position]
    return tuple(location)


def _format_location(location: tuple[int | str, ...]) -> str:
    path = ""
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            escaped_part = part.encode("unicode_escape").decode("ascii")
            path = f"{path}.{escaped_part}" if path else escaped_part
    return path or "$"


def _event_type_reason(value: object) -> str:
    valid_values = ", ".join(VALID_EVENT_TYPES)
    return f"invalid event type {_display_value(value)}; expected one of: {valid_values}"


def _is_event_type_location(location: tuple[int | str, ...]) -> bool:
    if location[:2] == ("event", "type"):
        return True
    if (
        len(location) >= 4
        and location[:2] == ("event", "sources")
        and isinstance(location[2], int)
    ):
        source_location = location[3:]
        return source_location[:1] == ("type",) or source_location[:2] == (
            "fields",
            "type",
        )
    return False


def _is_event_metadata_location(location: tuple[int | str, ...]) -> bool:
    return location == ("event", "metadata") or (
        len(location) == 5
        and location[:2] == ("event", "sources")
        and isinstance(location[2], int)
        and location[3:] == ("fields", "metadata")
    )


def _repair_strategy_value(detail: ErrorDetails) -> object:
    context = detail.get("ctx")
    if isinstance(context, Mapping) and "tag" in context:
        return context["tag"]
    return detail.get("input")


def _error_reason(detail: ErrorDetails) -> str:
    location = tuple(detail.get("loc", ()))
    error_type = detail["type"]
    value = detail.get("input")

    if location == ("retrace_mapping",):
        if error_type == "missing":
            return "field is required; supported version is 1"
        return (
            f"unsupported version {_display_value(value)}; "
            f"supported version is {SUPPORTED_MAPPING_VERSION}"
        )
    if location == ("run_discovery", "unit") and error_type == "literal_error":
        valid_values = ", ".join(VALID_DISCOVERY_UNITS)
        return (
            f"invalid discovery unit {_display_value(value)}; "
            f"expected one of: {valid_values}"
        )
    if error_type == "union_tag_invalid" and "repairs" in location:
        valid_values = ", ".join(VALID_REPAIR_STRATEGIES)
        strategy = _repair_strategy_value(detail)
        return (
            f"invalid repair strategy {_display_value(strategy)}; "
            f"expected one of: {valid_values}"
        )
    if error_type == "union_tag_not_found" and "repairs" in location:
        return "field is required"
    repair_branch_position = _repair_branch_position(location)
    if error_type == "extra_forbidden" and repair_branch_position is not None:
        strategy = location[repair_branch_position]
        field_name = location[-1]
        forbidden_fields = {
            "ordinal": {"expr", "map"},
            "derive": {"base"},
        }
        if field_name in forbidden_fields[strategy]:
            return f"{field_name} is not allowed with strategy {strategy!r}"
    if _is_event_metadata_location(location):
        return f"must be the literal string 'rest' (got {_display_value(value)})"
    if _is_event_type_location(location) and (
        error_type == "literal_error" or location[-1:] == ("default",)
    ):
        return _event_type_reason(value)
    if error_type == "missing":
        return "field is required"
    if error_type == "extra_forbidden":
        return "unknown key"
    if error_type == "model_type" and not location:
        return "configuration must be a mapping"

    reason = _one_line(detail["msg"])
    value_error_prefix = "Value error, "
    if reason.startswith(value_error_prefix):
        reason = reason.removeprefix(value_error_prefix)
    return reason


def _format_validation_error(error: ValidationError) -> str:
    lines = []
    for detail in error.errors(include_url=False):
        location = _format_location(_normalized_error_location(detail))
        lines.append(f"{location}: {_error_reason(detail)}")
    return "\n".join(lines)


def validate_mapping_config(data: object) -> MappingConfig:
    """Validate already-parsed mapping data."""
    if not isinstance(data, Mapping):
        raise MappingConfigError("$: configuration must be a mapping")
    try:
        return MappingConfig.model_validate(data)
    except ValidationError as error:
        raise MappingConfigError(_format_validation_error(error)) from None


def _yaml_error_reason(error: yaml.YAMLError) -> str:
    problem = getattr(error, "problem", None)
    detail = _one_line(problem if isinstance(problem, str) else error)
    mark = getattr(error, "problem_mark", None)
    line = getattr(mark, "line", None)
    column = getattr(mark, "column", None)
    if isinstance(line, int) and isinstance(column, int):
        return f"invalid YAML at line {line + 1}, column {column + 1}: {detail}"
    return f"invalid YAML: {detail}"


def _prefix_file(path: Path, message: str) -> str:
    return "\n".join(f"{path}: {line}" for line in message.splitlines())


def load_mapping_config(path: Path) -> MappingConfig:
    """Load and validate one UTF-8 YAML mapping file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise MappingConfigError(f"{path}: $: cannot read file: file does not exist") from None
    except UnicodeDecodeError:
        raise MappingConfigError(f"{path}: $: cannot read file: invalid UTF-8") from None
    except OSError as error:
        reason = _one_line(error.strerror or error)
        raise MappingConfigError(f"{path}: $: cannot read file: {reason}") from None

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise MappingConfigError(f"{path}: $: {_yaml_error_reason(error)}") from None

    try:
        return validate_mapping_config(data)
    except MappingConfigError as error:
        raise MappingConfigError(_prefix_file(path, str(error))) from None
