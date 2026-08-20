"""Typed extension seam for programmatic log adapters."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from retrace.adapters.discovery import RunSource
from retrace.core.model import Event, Run


@runtime_checkable
class Adapter(Protocol):
    """A programmatic adapter that discovers and parses runs.

    Retrace v1 documents this structural interface but does not load custom
    Python adapters from the CLI.
    """

    name: str

    def discover_runs(self, root: Path) -> Iterable[RunSource]:
        """Yield run sources discovered below *root*."""
        ...

    def parse_run(self, src: RunSource) -> tuple[Run, Iterator[Event]]:
        """Parse one source into its run summary and ordered event iterator."""
        ...


__all__ = ["Adapter"]
