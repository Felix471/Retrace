from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from retrace.adapters.mapping_schema import (
    VALID_DISCOVERY_UNITS,
    VALID_EVENT_TYPES,
    VALID_REPAIR_STRATEGIES,
    DeriveRepairConfig,
    EventFieldsConfig,
    EventSourceConfig,
    EventTypeMapping,
    MappingConfig,
    MappingConfigError,
    OrdinalRepairConfig,
    load_mapping_config,
    validate_mapping_config,
)


def _minimal_config() -> dict[str, object]:
    return {
        "retrace_mapping": 1,
        "run_discovery": {"pattern": "**/*.jsonl"},
        "run": {"id": "{file_stem}"},
        "event": {},
    }


FULL_CONFIG: dict[str, object] = {
    "retrace_mapping": 1,
    "run_discovery": {"pattern": "records/**/*.jsonl"},
    "run": {
        "id": "{dir_name}",
        "manifest": "meta.json",
        "metadata": {
            "condition": "settings.variant",
            "batch": "settings.batch",
        },
        "outcome": "result.status",
    },
    "event": {
        "where": "category != 'debug'",
        "turn": "sequence",
        "timestamp": "emitted_at",
        "agent_id": "actor.identifier",
        "role": "actor.function",
        "type": {
            "from": "category",
            "map": {
                "text": "message",
                "call": "tool_call",
                "reply": "tool_result",
                "notice": "system",
                "misc": "other",
            },
            "default": "other",
        },
        "phase": "stage",
        "content": "payload.value",
        "tokens_in": "metrics.input",
        "tokens_out": "metrics.output",
        "cost": "metrics.charge",
        "metadata": "rest",
    },
}

VALID_CONFIGS = (
    pytest.param(FULL_CONFIG, id="full"),
    pytest.param(_minimal_config(), id="minimal"),
    pytest.param(
        {
            **_minimal_config(),
            "run_discovery": {"pattern": "*/", "events_file": "events.jsonl"},
        },
        id="directory-layout",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"type": "category"}},
        id="plain-type",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "event": {
                "type": {
                    "from": "category",
                    "map": {"text": "message"},
                    "default": "other",
                }
            },
        },
        id="mapped-type",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"metadata": "rest"}},
        id="metadata-present",
    ),
    pytest.param(_minimal_config(), id="metadata-absent"),
    pytest.param(
        {
            **_minimal_config(),
            "run": {"id": "{unfinished[", "outcome": "[not compiled"},
            "event": {"where": "[not compiled", "type": "[not compiled"},
        },
        id="expressions-remain-plain-strings",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"type": {"from": "category"}}},
        id="type-from-only",
    ),
)


@pytest.mark.parametrize("raw", VALID_CONFIGS)
def test_valid_configs_load_from_data_and_yaml(
    raw: dict[str, object],
    tmp_path: Path,
) -> None:
    parsed = validate_mapping_config(raw)
    path = tmp_path / "mapping.yaml"
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )

    loaded = load_mapping_config(path)

    assert isinstance(parsed, MappingConfig)
    assert loaded == parsed


def test_object_type_form_is_typed() -> None:
    config = validate_mapping_config(FULL_CONFIG)

    assert isinstance(config.event.type, EventTypeMapping)
    assert config.event.type.from_ == "category"
    assert set(config.event.type.map.values()) == set(VALID_EVENT_TYPES)
    assert config.event.type.default == "other"


INVALID_CONFIGS = (
    pytest.param(
        {**_minimal_config(), "unexpected": "value"},
        "unexpected",
        ("unknown key",),
        id="unknown-top-level-key",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"unexpected": "value"}},
        "event.unexpected",
        ("unknown key",),
        id="unknown-nested-key",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"bad\nkey": "value"}},
        "event.bad\\nkey",
        ("unknown key",),
        id="line-breaking-unknown-key",
    ),
    pytest.param(
        {key: value for key, value in _minimal_config().items() if key != "retrace_mapping"},
        "retrace_mapping",
        ("field is required", "supported version is 1"),
        id="missing-version",
    ),
    pytest.param(
        {**_minimal_config(), "retrace_mapping": 2},
        "retrace_mapping",
        ("unsupported version 2", "supported version is 1"),
        id="bad-version",
    ),
    pytest.param(
        {**_minimal_config(), "retrace_mapping": True},
        "retrace_mapping",
        ("unsupported version True", "supported version is 1"),
        id="boolean-version",
    ),
    pytest.param(
        {**_minimal_config(), "retrace_mapping": 1.0},
        "retrace_mapping",
        ("unsupported version 1.0", "supported version is 1"),
        id="float-version",
    ),
    pytest.param(
        {**_minimal_config(), "run_discovery": {}},
        "run_discovery.pattern",
        ("field is required",),
        id="missing-pattern",
    ),
    pytest.param(
        {**_minimal_config(), "run": {}},
        "run.id",
        ("field is required",),
        id="missing-run-id",
    ),
    pytest.param(
        {key: value for key, value in _minimal_config().items() if key != "event"},
        "event",
        ("field is required",),
        id="missing-event-block",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "event": {
                "type": {
                    "from": "category",
                    "map": {"entry": "invalid_kind"},
                }
            },
        },
        "event.type.map.entry",
        ("invalid event type", "invalid_kind", *VALID_EVENT_TYPES),
        id="bad-type-map-value",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "event": {"type": {"from": "category", "default": "invalid_kind"}},
        },
        "event.type.default",
        ("invalid event type", "invalid_kind", *VALID_EVENT_TYPES),
        id="bad-type-default",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"type": {"map": {"text": "message"}}}},
        "event.type.from",
        ("field is required",),
        id="missing-type-source",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"metadata": "all"}},
        "event.metadata",
        ("literal string 'rest'", "'all'"),
        id="bad-event-metadata",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"metadata": None}},
        "event.metadata",
        ("literal string 'rest'", "None"),
        id="null-event-metadata",
    ),
)


@pytest.mark.parametrize(("raw", "key_path", "reason_fragments"), INVALID_CONFIGS)
def test_invalid_schema_cases_have_actionable_one_line_errors(
    raw: dict[str, object],
    key_path: str,
    reason_fragments: tuple[str, ...],
    tmp_path: Path,
) -> None:
    with pytest.raises(MappingConfigError) as parsed_error:
        validate_mapping_config(raw)

    parsed_message = str(parsed_error.value)
    assert "\n" not in parsed_message
    assert parsed_message.startswith(f"{key_path}: ")
    assert all(fragment in parsed_message for fragment in reason_fragments)

    path = tmp_path / "invalid.yaml"
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(MappingConfigError) as file_error:
        load_mapping_config(path)

    assert str(file_error.value) == f"{path}: {parsed_message}"


@pytest.mark.parametrize("data", [None, [], "text", 7], ids=["null", "list", "string", "integer"])
def test_parsed_root_must_be_a_mapping(data: object) -> None:
    with pytest.raises(MappingConfigError) as error:
        validate_mapping_config(data)

    assert str(error.value) == "$: configuration must be a mapping"


def test_multiple_schema_errors_repeat_file_context(tmp_path: Path) -> None:
    raw: dict[str, object] = {
        "retrace_mapping": 2,
        "run_discovery": {},
        "run": {},
        "event": {"unexpected": "value"},
    }
    with pytest.raises(MappingConfigError) as parsed_error:
        validate_mapping_config(raw)
    parsed_lines = str(parsed_error.value).splitlines()

    path = tmp_path / "multiple.yaml"
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(MappingConfigError) as file_error:
        load_mapping_config(path)
    file_lines = str(file_error.value).splitlines()

    assert len(parsed_lines) == 4
    assert file_lines == [f"{path}: {line}" for line in parsed_lines]


def test_invalid_yaml_has_one_line_file_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text(
        "retrace_mapping: 1\nrun_discovery: [\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(MappingConfigError) as error:
        load_mapping_config(path)

    message = str(error.value)
    assert "\n" not in message
    assert message.startswith(f"{path}: $: invalid YAML at line ")


def test_nonexistent_file_has_one_line_file_error(tmp_path: Path) -> None:
    path = tmp_path / "absent.yaml"

    with pytest.raises(MappingConfigError) as error:
        load_mapping_config(path)

    assert str(error.value) == f"{path}: $: cannot read file: file does not exist"


def _minimal_source() -> dict[str, object]:
    return {
        "name": "primary",
        "path": "primary_items",
    }


def _config_with_sources(sources: list[dict[str, object]]) -> dict[str, object]:
    return {
        **_minimal_config(),
        "event": {"sources": sources},
    }


def _config_with_repair(repair: dict[str, object]) -> dict[str, object]:
    source = {**_minimal_source(), "repairs": [repair]}
    return _config_with_sources([source])


FULL_EXTENSION_CONFIG: dict[str, object] = {
    "retrace_mapping": 1,
    "run_discovery": {
        "pattern": "data/*.jsonl",
        "unit": "line",
    },
    "run": {
        "id": "record_key",
        "metadata": {"variant": "settings.variant"},
        "outcome": "result.status",
    },
    "event": {
        "sources": [
            {
                "name": "primary",
                "path": "primary_items",
                "type": "message",
                "priority": 0,
                "fields": {
                    "turn": "sequence",
                    "agent_id": "entity_key",
                    "content": "payload",
                    "timestamp": "emitted_at",
                    "metadata": "rest",
                },
            },
            {
                "name": "secondary",
                "path": "secondary_items",
                "type": "other",
                "phase": "evaluation",
                "role": "validator",
                "priority": 1,
                "fields": {
                    "turn": "sequence_no",
                    "agent_id": "selected_by",
                    "metadata": "rest",
                },
                "repairs": [
                    {"field": "turn", "strategy": "ordinal", "base": 1},
                    {
                        "field": "status",
                        "strategy": "derive",
                        "expr": "contains(values(checks), 'invalid')",
                        "map": {True: "negative", False: "positive"},
                    },
                ],
            },
        ],
        "merge": {"sort_by": "turn"},
    },
    "agents": {
        "path": "entities",
        "key": "entity_key",
        "attributes": {
            "role": "function",
            "model": "engine_name",
        },
    },
}


EXTENSION_VALID_CONFIGS = (
    pytest.param(FULL_EXTENSION_CONFIG, id="full-extension"),
    pytest.param(
        {
            **_minimal_config(),
            "run_discovery": {"pattern": "data/*.jsonl", "unit": "line"},
            "run": {"id": "record.identifier"},
        },
        id="line-unit",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "run_discovery": {"pattern": "*/", "events_file": "events.jsonl"},
        },
        id="legacy-directory-unit",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "run_discovery": {
                "pattern": "*/",
                "unit": "dir",
                "events_file": "events.jsonl",
            },
        },
        id="explicit-directory-unit",
    ),
    pytest.param(
        _config_with_sources([_minimal_source()]),
        id="source-without-merge-or-agents",
    ),
    pytest.param(
        _config_with_repair(
            {
                "field": "status",
                "strategy": "derive",
                "expr": "computed.status",
            }
        ),
        id="derive-without-map",
    ),
    pytest.param(
        _config_with_repair({"field": "sequence", "strategy": "ordinal"}),
        id="ordinal-default-base",
    ),
    pytest.param(
        _config_with_sources(
            [
                {
                    **_minimal_source(),
                    "fields": {
                        "type": {
                            "from": "category",
                            "map": {"text": "message"},
                            "default": "other",
                        }
                    },
                }
            ]
        ),
        id="source-fields-mapped-type",
    ),
    pytest.param(
        {
            **_config_with_sources([_minimal_source()]),
            "agents": {
                "path": "entities",
                "key": "entity_key",
                "attributes": {"role": "function", "vendor": "provider_name"},
            },
        },
        id="agents-present",
    ),
)


@pytest.mark.parametrize("raw", EXTENSION_VALID_CONFIGS)
def test_extension_configs_load_from_data_and_yaml(
    raw: dict[str, object],
    tmp_path: Path,
) -> None:
    parsed = validate_mapping_config(raw)
    path = tmp_path / "extension.yaml"
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )

    assert load_mapping_config(path) == parsed


def test_full_extension_config_is_typed_and_uncompiled() -> None:
    config = validate_mapping_config(FULL_EXTENSION_CONFIG)

    assert config.run_discovery.unit == "line"
    assert config.run.id == "record_key"
    assert config.event.sources is not None
    assert len(config.event.sources) == 2
    assert all(isinstance(source, EventSourceConfig) for source in config.event.sources)
    assert isinstance(config.event.sources[0].fields, EventFieldsConfig)
    assert config.event.sources[0].type == "message"
    assert config.event.sources[1].phase == "evaluation"
    assert config.event.sources[1].role == "validator"
    assert config.event.merge is not None
    assert config.event.merge.sort_by == "turn"
    assert config.agents is not None
    assert config.agents.attributes["role"] == "function"

    ordinal, derive = config.event.sources[1].repairs
    assert isinstance(ordinal, OrdinalRepairConfig)
    assert ordinal.base == 1
    assert isinstance(derive, DeriveRepairConfig)
    assert derive.expr == "contains(values(checks), 'invalid')"
    assert derive.map == {"True": "negative", "False": "positive"}


def test_extension_defaults_and_legacy_normalization() -> None:
    default_config = validate_mapping_config(_minimal_config())
    legacy_config = validate_mapping_config(
        {
            **_minimal_config(),
            "run_discovery": {"pattern": "*/", "events_file": "events.jsonl"},
        }
    )
    ordinal_config = validate_mapping_config(
        _config_with_repair({"field": "sequence", "strategy": "ordinal"})
    )

    assert default_config.run_discovery.unit == "file"
    assert default_config.event.sources is None
    assert default_config.event.merge is None
    assert default_config.agents is None
    assert legacy_config.run_discovery.unit == "dir"
    assert ordinal_config.event.sources is not None
    repair = ordinal_config.event.sources[0].repairs[0]
    assert isinstance(repair, OrdinalRepairConfig)
    assert repair.base == 0


EXTENSION_INVALID_CONFIGS = (
    pytest.param(
        {
            **_minimal_config(),
            "run_discovery": {"pattern": "*.jsonl", "unit": "bundle"},
        },
        "run_discovery.unit",
        ("invalid discovery unit", "bundle", *VALID_DISCOVERY_UNITS),
        id="bad-unit",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "run_discovery": {"pattern": "*.jsonl", "unit": []},
        },
        "run_discovery.unit",
        ("invalid discovery unit", "[]", *VALID_DISCOVERY_UNITS),
        id="non-scalar-unit",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "run_discovery": {
                "pattern": "*.jsonl",
                "unit": "line",
                "events_file": "events.jsonl",
            },
        },
        "run_discovery.events_file",
        ("cannot be combined", "unit 'line'", "unit 'dir'"),
        id="events-file-with-line-unit",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "run_discovery": {
                "pattern": "*.jsonl",
                "unit": "file",
                "events_file": "events.jsonl",
            },
        },
        "run_discovery.events_file",
        ("cannot be combined", "unit 'file'", "unit 'dir'"),
        id="events-file-with-file-unit",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"sources": []}},
        "event.sources",
        ("at least one source",),
        id="empty-sources",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"sources": None}},
        "event.sources",
        ("at least one source",),
        id="null-sources",
    ),
    pytest.param(
        _config_with_sources([_minimal_source(), _minimal_source()]),
        "event.sources[1].name",
        ("duplicate source name", "primary"),
        id="duplicate-source-name",
    ),
    pytest.param(
        _config_with_sources([{"name": "primary"}]),
        "event.sources[0].path",
        ("field is required",),
        id="source-missing-path",
    ),
    pytest.param(
        _config_with_sources([{"path": "primary_items"}]),
        "event.sources[0].name",
        ("field is required",),
        id="source-missing-name",
    ),
    pytest.param(
        _config_with_sources([{**_minimal_source(), "type": "invalid_kind"}]),
        "event.sources[0].type",
        ("invalid event type", "invalid_kind", *VALID_EVENT_TYPES),
        id="bad-fixed-source-type",
    ),
    pytest.param(
        _config_with_sources(
            [{**_minimal_source(), "fields": {"where": "category != 'debug'"}}]
        ),
        "event.sources[0].fields.where",
        ("unknown key",),
        id="source-fields-where",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "event": {"sources": [_minimal_source()], "turn": "sequence"},
        },
        "event.sources",
        ("cannot be combined", "flat event fields", "turn"),
        id="sources-with-flat-slot",
    ),
    pytest.param(
        {**_minimal_config(), "event": {"merge": {"sort_by": "turn"}}},
        "event.merge",
        ("merge requires sources",),
        id="merge-without-sources",
    ),
    pytest.param(
        {
            **_config_with_sources([_minimal_source()]),
            "event": {"sources": [_minimal_source()], "merge": None},
        },
        "event.merge",
        ("object", "sort_by"),
        id="null-merge",
    ),
    pytest.param(
        {
            **_config_with_sources([_minimal_source()]),
            "event": {"sources": [_minimal_source()], "merge": {}},
        },
        "event.merge.sort_by",
        ("field is required",),
        id="merge-missing-sort-by",
    ),
    pytest.param(
        _config_with_repair({"field": "status", "strategy": "replace"}),
        "event.sources[0].repairs[0].strategy",
        ("invalid repair strategy", "replace", *VALID_REPAIR_STRATEGIES),
        id="unknown-repair-strategy",
    ),
    pytest.param(
        _config_with_repair({"field": "status"}),
        "event.sources[0].repairs[0].strategy",
        ("field is required",),
        id="repair-missing-strategy",
    ),
    pytest.param(
        _config_with_repair({"strategy": "derive", "expr": "computed.status"}),
        "event.sources[0].repairs[0].field",
        ("field is required",),
        id="repair-missing-field",
    ),
    pytest.param(
        _config_with_repair({"field": "status", "strategy": "derive"}),
        "event.sources[0].repairs[0].expr",
        ("field is required",),
        id="derive-missing-expr",
    ),
    pytest.param(
        _config_with_repair(
            {
                "field": "sequence",
                "strategy": "ordinal",
                "expr": "computed.sequence",
            }
        ),
        "event.sources[0].repairs[0].expr",
        ("not allowed", "strategy 'ordinal'"),
        id="ordinal-with-expr",
    ),
    pytest.param(
        _config_with_repair(
            {
                "field": "sequence",
                "strategy": "ordinal",
                "map": {"1": "first"},
            }
        ),
        "event.sources[0].repairs[0].map",
        ("not allowed", "strategy 'ordinal'"),
        id="ordinal-with-map",
    ),
    pytest.param(
        _config_with_repair(
            {
                "field": "status",
                "strategy": "derive",
                "expr": "computed.status",
                "base": 1,
            }
        ),
        "event.sources[0].repairs[0].base",
        ("not allowed", "strategy 'derive'"),
        id="derive-with-base",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "agents": {"key": "entity_key", "attributes": {"model": "engine"}},
        },
        "agents.path",
        ("field is required",),
        id="agents-missing-path",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "agents": {"path": "entities", "attributes": {"model": "engine"}},
        },
        "agents.key",
        ("field is required",),
        id="agents-missing-key",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "agents": {"path": "entities", "key": "entity_key"},
        },
        "agents.attributes",
        ("field is required",),
        id="agents-missing-attributes",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "agents": {"path": "entities", "key": "entity_key", "attributes": {}},
        },
        "agents.attributes",
        ("at least one attribute",),
        id="agents-empty-attributes",
    ),
    pytest.param(
        {**_minimal_config(), "agents": None},
        "agents",
        ("object", "path", "key", "attributes"),
        id="null-agents",
    ),
    pytest.param(
        _config_with_sources([{**_minimal_source(), "unexpected": "value"}]),
        "event.sources[0].unexpected",
        ("unknown key",),
        id="unknown-source-key",
    ),
    pytest.param(
        {
            **_minimal_config(),
            "agents": {
                "path": "entities",
                "key": "entity_key",
                "attributes": {"model": "engine"},
                "unexpected": "value",
            },
        },
        "agents.unexpected",
        ("unknown key",),
        id="unknown-agents-key",
    ),
    pytest.param(
        {
            **_config_with_sources([_minimal_source()]),
            "event": {
                "sources": [_minimal_source()],
                "merge": {"sort_by": "turn", "unexpected": "value"},
            },
        },
        "event.merge.unexpected",
        ("unknown key",),
        id="unknown-merge-key",
    ),
    pytest.param(
        _config_with_repair(
            {"field": "sequence", "strategy": "ordinal", "unexpected": "value"}
        ),
        "event.sources[0].repairs[0].unexpected",
        ("unknown key",),
        id="unknown-repair-key",
    ),
    pytest.param(
        _config_with_sources(
            [{**_minimal_source(), "fields": {"metadata": "all"}}]
        ),
        "event.sources[0].fields.metadata",
        ("literal string 'rest'", "'all'"),
        id="bad-source-fields-metadata",
    ),
    pytest.param(
        _config_with_sources(
            [
                {
                    **_minimal_source(),
                    "fields": {
                        "type": {
                            "from": "category",
                            "map": {"entry": "invalid_kind"},
                        }
                    },
                }
            ]
        ),
        "event.sources[0].fields.type.map.entry",
        ("invalid event type", "invalid_kind", *VALID_EVENT_TYPES),
        id="bad-source-fields-type-map",
    ),
)


@pytest.mark.parametrize(
    ("raw", "key_path", "reason_fragments"),
    EXTENSION_INVALID_CONFIGS,
)
def test_invalid_extension_cases_have_actionable_one_line_errors(
    raw: dict[str, object],
    key_path: str,
    reason_fragments: tuple[str, ...],
    tmp_path: Path,
) -> None:
    with pytest.raises(MappingConfigError) as parsed_error:
        validate_mapping_config(raw)

    parsed_message = str(parsed_error.value)
    assert "\n" not in parsed_message
    assert parsed_message.startswith(f"{key_path}: ")
    assert all(fragment in parsed_message for fragment in reason_fragments)

    path = tmp_path / "invalid-extension.yaml"
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(MappingConfigError) as file_error:
        load_mapping_config(path)

    assert str(file_error.value) == f"{path}: {parsed_message}"
