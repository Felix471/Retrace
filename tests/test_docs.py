from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from pydantic import BaseModel

from retrace.adapters import mapping_schema
from retrace.adapters.discovery import RunSource
from retrace.adapters.protocol import Adapter
from retrace.core.model import Event, Run

ROOT = Path(__file__).resolve().parents[1]
README_VERDICT = (
    "tested against real AG2 and HyperAgent traces from the MAST corpus (config-only); "
    "free-text logs are out of scope in v1."
)
README_LOCAL_PROMISE = (
    "Retrace makes zero outbound network requests. It binds 127.0.0.1, serves only your "
    "own browser, reads your logs, and writes only *.retrace.json sidecars next to them "
    "plus its own cache in your user cache directory. Any data leaving the machine can "
    "only be a user-initiated explicit job - and none exist in v1."
)


def _schema_keys() -> set[str]:
    keys: set[str] = set()
    for value in vars(mapping_schema).values():
        if isinstance(value, type) and issubclass(value, BaseModel):
            for name, field in value.model_fields.items():
                keys.add(name)
                if isinstance(field.alias, str):
                    keys.add(field.alias)
    return keys


def test_mapping_reference_covers_every_schema_key() -> None:
    text = (ROOT / "docs" / "mapping.md").read_text(encoding="ascii")
    documented = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text))
    assert _schema_keys() <= documented


def test_readme_has_owner_verdict_verbatim() -> None:
    text = (ROOT / "README.md").read_text(encoding="ascii")
    assert README_VERDICT in text


def test_readme_has_local_privacy_promise_verbatim() -> None:
    text = (ROOT / "README.md").read_text(encoding="ascii")
    assert README_LOCAL_PROMISE in text


def test_public_compatibility_page_has_measured_results() -> None:
    compatibility = ROOT / "docs" / "compatibility.md"
    assert compatibility.is_file()
    text = compatibility.read_text(encoding="ascii")
    for value in ("7,184", "44,102", "873,441", "100%"):
        assert value in text
    readme = (ROOT / "README.md").read_text(encoding="ascii")
    assert "docs/compatibility.md" in readme


def test_readme_quickstart_check_command_executes() -> None:
    text = (ROOT / "README.md").read_text(encoding="ascii")
    section = text.split("## 60-second quickstart", 1)[1].split("\n## ", 1)[0]
    blocks = re.findall(r"```shell\n(.*?)```", section, flags=re.DOTALL)
    commands = [line for block in blocks for line in block.splitlines() if line.strip()]
    check = next(command for command in commands if command.startswith("retrace-logs check "))
    result = subprocess.run(
        [sys.executable, "-m", "retrace.cli", *check.split()[1:]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Runs: 40 runs" in result.stdout


def test_adapter_protocol_is_runtime_checkable() -> None:
    class MinimalAdapter:
        name = "minimal"

        def discover_runs(self, root: Path) -> Iterable[RunSource]:
            return []

        def parse_run(self, src: RunSource) -> tuple[Run, Iterator[Event]]:
            raise NotImplementedError

    assert isinstance(MinimalAdapter(), Adapter)
