"""Format-neutral data model for Retrace."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

VALID_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"message", "tool_call", "tool_result", "system", "other"}
)

TimestampInput = str | int | float | None
TimestampResult = tuple[datetime | None, bool]
EventTypeResult = tuple[str, bool]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetime_to_string(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    if normalized is None:
        return None
    return normalized.isoformat().removesuffix("+00:00") + "Z"


def parse_timestamp(value: TimestampInput) -> TimestampResult:
    """Return a UTC datetime and whether parsing produced a warning."""
    if value is None or isinstance(value, bool):
        return None, True

    if isinstance(value, (int, float)):
        try:
            seconds = value / 1000 if value >= 100_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=UTC), False
        except (OSError, OverflowError, TypeError, ValueError):
            return None, True

    if not isinstance(value, str):
        return None, True

    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except (OverflowError, ValueError):
        return None, True
    return _as_utc(parsed), False


def coerce_event_type(value: object) -> EventTypeResult:
    """Return a closed event type and whether coercion produced a warning."""
    if isinstance(value, str) and value in VALID_EVENT_TYPES:
        return value, False
    return "other", True


@dataclass
class Event:
    """One ordered event within a run."""

    id: str
    run_id: str
    ordinal: int
    turn: int | None
    timestamp: datetime | None
    agent_id: str | None
    role: str | None
    type: str
    phase: str | None
    content: str
    structured: dict | None
    tokens_in: int | None
    tokens_out: int | None
    cost: float | None
    refs: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.timestamp = _as_utc(self.timestamp)
        self.type, _ = coerce_event_type(self.type)

    @staticmethod
    def parse_timestamp(value: TimestampInput) -> TimestampResult:
        """Parse a timestamp using the model's UTC rules."""
        return parse_timestamp(value)

    @staticmethod
    def coerce_event_type(value: object) -> EventTypeResult:
        """Coerce an event type into the closed vocabulary."""
        return coerce_event_type(value)

    def to_dict(self) -> dict:
        """Return a plain dictionary representation."""
        return {
            "id": self.id,
            "run_id": self.run_id,
            "ordinal": self.ordinal,
            "turn": self.turn,
            "timestamp": _datetime_to_string(self.timestamp),
            "agent_id": self.agent_id,
            "role": self.role,
            "type": self.type,
            "phase": self.phase,
            "content": self.content,
            "structured": None if self.structured is None else dict(self.structured),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost": self.cost,
            "refs": list(self.refs),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Event:
        """Build an event from its plain dictionary representation."""
        timestamp, _ = parse_timestamp(data["timestamp"])
        event_type, _ = coerce_event_type(data["type"])
        structured = data["structured"]
        return cls(
            id=data["id"],
            run_id=data["run_id"],
            ordinal=data["ordinal"],
            turn=data["turn"],
            timestamp=timestamp,
            agent_id=data["agent_id"],
            role=data["role"],
            type=event_type,
            phase=data["phase"],
            content=data["content"],
            structured=None if structured is None else dict(structured),
            tokens_in=data["tokens_in"],
            tokens_out=data["tokens_out"],
            cost=data["cost"],
            refs=list(data.get("refs", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class Run:
    """One source run and its derived summaries."""

    id: str
    experiment_id: str
    source_path: str
    metadata: dict
    outcome: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_s: float | None
    n_events: int
    n_turns: int
    agent_ids: list[str]
    phases: list[str]
    tokens_in: int | None
    tokens_out: int | None
    total_cost: float | None
    ingest_warnings: int = 0
    n_repaired: int = 0

    def __post_init__(self) -> None:
        self.started_at = _as_utc(self.started_at)
        self.ended_at = _as_utc(self.ended_at)

    def to_dict(self) -> dict:
        """Return a plain dictionary representation."""
        return {
            "id": self.id,
            "experiment_id": self.experiment_id,
            "source_path": self.source_path,
            "metadata": dict(self.metadata),
            "outcome": self.outcome,
            "started_at": _datetime_to_string(self.started_at),
            "ended_at": _datetime_to_string(self.ended_at),
            "duration_s": self.duration_s,
            "n_events": self.n_events,
            "n_turns": self.n_turns,
            "agent_ids": list(self.agent_ids),
            "phases": list(self.phases),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "total_cost": self.total_cost,
            "ingest_warnings": self.ingest_warnings,
            "n_repaired": self.n_repaired,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Run:
        """Build a run from its plain dictionary representation."""
        started_at, _ = parse_timestamp(data["started_at"])
        ended_at, _ = parse_timestamp(data["ended_at"])
        return cls(
            id=data["id"],
            experiment_id=data["experiment_id"],
            source_path=data["source_path"],
            metadata=dict(data["metadata"]),
            outcome=data["outcome"],
            started_at=started_at,
            ended_at=ended_at,
            duration_s=data["duration_s"],
            n_events=data["n_events"],
            n_turns=data["n_turns"],
            agent_ids=list(data["agent_ids"]),
            phases=list(data["phases"]),
            tokens_in=data["tokens_in"],
            tokens_out=data["tokens_out"],
            total_cost=data["total_cost"],
            ingest_warnings=data.get("ingest_warnings", 0),
            n_repaired=data.get("n_repaired", 0),
        )


@dataclass
class Experiment:
    """A collection of runs resolved from one root path."""

    id: str
    root_path: str
    adapter_ref: str
    runs: list[Run] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a plain dictionary representation."""
        return {
            "id": self.id,
            "root_path": self.root_path,
            "adapter_ref": self.adapter_ref,
            "runs": [run.to_dict() for run in self.runs],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Experiment:
        """Build an experiment from its plain dictionary representation."""
        return cls(
            id=data["id"],
            root_path=data["root_path"],
            adapter_ref=data["adapter_ref"],
            runs=[Run.from_dict(run) for run in data.get("runs", [])],
        )


__all__ = [
    "VALID_EVENT_TYPES",
    "Event",
    "Experiment",
    "Run",
    "coerce_event_type",
    "parse_timestamp",
]
