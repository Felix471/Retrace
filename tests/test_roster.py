from __future__ import annotations

from collections import Counter
from typing import Any

import jmespath
import pytest

from retrace.adapters.mapping_schema import AgentsConfig, MappingConfigError
from retrace.adapters.roster import RosterJoin, RosterWarningCounts


def _config(
    *,
    path: str = "context.members",
    key: str = "identity.code",
    attributes: dict[str, str] | None = None,
) -> AgentsConfig:
    return AgentsConfig.model_validate(
        {
            "path": path,
            "key": key,
            "attributes": attributes
            or {
                "role": "profile.function",
                "model": "runtime.name",
            },
        }
    )


def test_nested_roster_join_fills_role_and_namespaced_attributes() -> None:
    retained = ["raw", {"value": 3}]
    join = RosterJoin(
        _config(
            attributes={
                "role": "profile.function",
                "model": "runtime.name",
                "score": "metrics.score",
                "active": "flags.active",
                "details": "details",
                "missing": "optional",
            }
        )
    )
    table = join.build(
        {
            "context": {
                "members": [
                    {
                        "identity": {"code": 7},
                        "profile": {"function": "reviewer"},
                        "runtime": {"name": "engine-a"},
                        "metrics": {"score": 0},
                        "flags": {"active": False},
                        "details": retained,
                        "optional": None,
                    }
                ]
            }
        }
    )
    metadata = {
        "model": "event-local",
        "_retrace": {
            "source": "entries",
            "source_ordinal": 2,
            "repaired": {"agent_id": "old"},
        },
    }

    result = table.apply("7", None, metadata)

    assert result.matched
    assert not result.unmatched
    assert result.role == "reviewer"
    assert result.metadata["model"] == "event-local"
    assert result.metadata["_retrace"] == {
        "source": "entries",
        "source_ordinal": 2,
        "repaired": {"agent_id": "old"},
        "agent": {
            "model": "engine-a",
            "score": 0,
            "active": False,
            "details": retained,
        },
    }
    engine = result.metadata["_retrace"]
    assert isinstance(engine, dict)
    agent = engine["agent"]
    assert isinstance(agent, dict)
    assert agent["details"] is retained
    assert "agent" not in metadata["_retrace"]  # type: ignore[operator]
    assert table.warnings == RosterWarningCounts()


@pytest.mark.parametrize("existing_role", ("local", ""), ids=("text", "empty"))
def test_existing_event_role_wins_but_other_attributes_still_join(
    existing_role: str,
) -> None:
    join = RosterJoin(_config())
    table = join.build(
        {
            "context": {
                "members": [
                    {
                        "identity": {"code": "member-a"},
                        "profile": {"function": "joined"},
                        "runtime": {"name": "engine-a"},
                    }
                ]
            }
        }
    )

    result = join.apply(table, "member-a", existing_role, {})

    assert result.role == existing_role
    assert result.metadata == {"_retrace": {"agent": {"model": "engine-a"}}}


@pytest.mark.parametrize(
    ("roster_key", "event_key"),
    ((7, "7"), ("8", 8), (True, "True"), ("", "")),
    ids=("integer-to-string", "string-to-integer", "boolean", "empty-string"),
)
def test_key_comparison_uses_exact_string_coercion(
    roster_key: object,
    event_key: object,
) -> None:
    join = RosterJoin(
        _config(path="members", key="code", attributes={"model": "engine"})
    )
    table = join.build({"members": [{"code": roster_key, "engine": "matched"}]})

    result = table.join(event_key, None, {})

    assert result.matched
    assert result.metadata == {"_retrace": {"agent": {"model": "matched"}}}
    assert table.warnings.total == 0


def test_none_agent_id_is_untouched_without_an_unmatched_warning() -> None:
    join = RosterJoin(
        _config(path="members", key="code", attributes={"model": "engine"})
    )
    table = join.build({"members": [{"code": 1, "engine": "alpha"}]})
    metadata = {"kept": {"nested": True}}

    result = table.apply(None, "local", metadata)

    assert not result.matched
    assert not result.unmatched
    assert result.role == "local"
    assert result.metadata == metadata
    assert table.warnings == RosterWarningCounts()


def test_duplicate_normalized_keys_keep_first_and_warn_once() -> None:
    join = RosterJoin(
        _config(
            path="members",
            key="code",
            attributes={"role": "function", "model": "engine"},
        )
    )
    table = join.build(
        {
            "members": [
                {"code": 7, "function": "first", "engine": "engine-a"},
                {"code": "7", "function": "second", "engine": "engine-b"},
                {"code": 7, "function": "third", "engine": "engine-c"},
            ]
        }
    )

    first = table.apply("7", None, {})
    second = table.apply("7", None, {})

    assert first == second
    assert first.role == "first"
    assert first.metadata == {"_retrace": {"agent": {"model": "engine-a"}}}
    assert table.warning_categories == frozenset({"duplicate"})
    assert table.warnings == RosterWarningCounts(duplicate=1)


@pytest.mark.parametrize(
    "run_record",
    ({}, {"members": None}, {"members": {}}, {"members": "invalid"}),
    ids=("missing", "null", "object", "string"),
)
def test_missing_or_non_array_path_warns_once_without_unmatched_cascade(
    run_record: dict[str, object],
) -> None:
    join = RosterJoin(
        _config(path="members", key="code", attributes={"model": "engine"})
    )
    table = join.build(run_record)

    first = table.apply("one", None, {"kept": True})
    second = table.apply("two", None, {"kept": True})

    assert first.metadata == {"kept": True}
    assert second.metadata == {"kept": True}
    assert table.warnings == RosterWarningCounts(path=1)


@pytest.mark.parametrize(
    "members",
    ([], [{"other": 1}], [{"code": None}], [None, {"other": 2}]),
    ids=("empty", "missing-key", "null-key", "mixed-unusable"),
)
def test_no_usable_roster_key_warns_once_without_unmatched_cascade(
    members: list[object],
) -> None:
    join = RosterJoin(
        _config(path="members", key="code", attributes={"model": "engine"})
    )
    table = join.build({"members": members})

    result = table.apply("one", None, {})

    assert not result.matched
    assert not result.unmatched
    assert table.warnings == RosterWarningCounts(key=1)


def test_many_missing_event_keys_latch_one_unmatched_warning() -> None:
    join = RosterJoin(
        _config(path="members", key="code", attributes={"model": "engine"})
    )
    table = join.build({"members": [{"code": "known", "engine": "alpha"}]})

    results = [
        table.apply(agent_id, None, {})
        for agent_id in ("missing", "missing", "another", "known")
    ]

    assert [result.matched for result in results] == [False, False, False, True]
    assert table.warnings == RosterWarningCounts(unmatched=1)


def test_runtime_expression_errors_follow_nonfatal_categories() -> None:
    path_join = RosterJoin(
        _config(
            path="length(number)",
            key="code",
            attributes={"model": "engine"},
        )
    )
    key_join = RosterJoin(
        _config(
            path="members",
            key="length(code)",
            attributes={"model": "engine"},
        )
    )

    path_table = path_join.build({"number": 4})
    key_table = key_join.build({"members": [{"code": 4, "engine": "alpha"}]})

    assert path_table.warnings == RosterWarningCounts(path=1)
    assert key_table.warnings == RosterWarningCounts(key=1)


def test_expressions_compile_once_per_config_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(
        path="members",
        key="identity.code",
        attributes={
            "role": "profile.function",
            "model": "runtime.name",
            "provider": "runtime.vendor",
        },
    )
    real_compile = jmespath.compile
    calls: list[str] = []

    def compile_spy(expression: str) -> Any:
        calls.append(expression)
        return real_compile(expression)

    monkeypatch.setattr(jmespath, "compile", compile_spy)
    join = RosterJoin(config)
    expected = Counter(
        [
            "members",
            "identity.code",
            "profile.function",
            "runtime.name",
            "runtime.vendor",
        ]
    )
    assert Counter(calls) == expected

    for _ in range(2):
        table = join.build(
            {
                "members": [
                    {
                        "identity": {"code": 1},
                        "profile": {"function": "reviewer"},
                        "runtime": {"name": "engine-a", "vendor": "vendor-a"},
                    }
                ]
            }
        )
        table.apply("1", None, {})

    assert Counter(calls) == expected


@pytest.mark.parametrize(
    ("config", "path"),
    (
        (_config(path="["), "agents.path"),
        (_config(key="["), "agents.key"),
        (_config(attributes={"model": "["}), "agents.attributes.model"),
    ),
    ids=("path", "key", "attribute"),
)
def test_invalid_expressions_have_actionable_one_line_errors(
    config: AgentsConfig,
    path: str,
) -> None:
    with pytest.raises(MappingConfigError) as captured:
        RosterJoin(config)

    message = str(captured.value)
    assert message.startswith(f"{path}: invalid JMESPath expression:")
    assert "\n" not in message
