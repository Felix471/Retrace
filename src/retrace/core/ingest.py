"""Compose discovery and extraction into deterministic stored runs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from retrace.adapters.discovery import (
    RunSource,
    discover_runs,
    discover_runs_with_report,
    iter_jsonl_records,
)
from retrace.adapters.extract import ExtractionStats, Extractor, FieldStats
from retrace.adapters.mapping_schema import EventConfig, MappingConfig, MappingConfigError
from retrace.adapters.multisource import MultiSourceEvent, MultiSourceExtractor, MultiSourceStats
from retrace.adapters.roster import RosterJoin
from retrace.core.model import Event, Run
from retrace.core.store import SqliteStore

Progress = Callable[[str, int, int], None]
LineFailure = tuple[Path, int, str]


@dataclass(slots=True)
class IngestReport:
    """Accounting for one ingest invocation."""

    runs_ingested: int = 0
    runs_replaced: int = 0
    runs_skipped_unchanged: int = 0
    line_failures: list[LineFailure] = field(default_factory=list)
    per_file_line_failures: dict[Path, int] = field(default_factory=dict)
    config_hash_warning: bool = False
    processed_run_ids: list[str] = field(default_factory=list)
    field_stats: dict[str, FieldStats] = field(default_factory=dict)
    repair_rule_counts: dict[tuple[str, str, str], tuple[int, int]] = field(
        default_factory=dict
    )

    @property
    def ingested(self) -> int:
        return self.runs_ingested

    @property
    def replaced(self) -> int:
        return self.runs_replaced

    @property
    def skipped_unchanged(self) -> int:
        return self.runs_skipped_unchanged

    @property
    def run_ids(self) -> list[str]:
        return self.processed_run_ids


def _config_hash(config: MappingConfig) -> str:
    canonical = json.dumps(
        config.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def stable_root_hash(root: Path) -> str:
    """Return the stable identifier used for one ingested root."""
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]


def _merge_stats(
    report: IngestReport,
    stats: ExtractionStats,
    config: MappingConfig,
) -> None:
    for path, counter in stats.fields.items():
        aggregate = report.field_stats.setdefault(path, FieldStats())
        aggregate.hits += counter.hits
        aggregate.misses += counter.misses
        aggregate.failures += counter.failures
    if not isinstance(stats, MultiSourceStats):
        return
    sources = config.event.sources or []
    strategies = {
        (source.name, rule.field): rule.strategy
        for source in sources
        for rule in source.repairs
    }
    for (source, repair_field), fired in stats.repair_fire_counts.items():
        strategy = strategies[(source, repair_field)]
        key = (source, repair_field, strategy)
        old_fired, old_considered = report.repair_rule_counts.get(key, (0, 0))
        report.repair_rule_counts[key] = (
            old_fired + fired,
            old_considered + stats.source_record_counts[source],
        )


def _source_path(path: Path) -> str:
    return path.resolve().as_posix()


def _line_files(config: MappingConfig, root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    pattern = config.run_discovery.pattern
    if Path(pattern).is_absolute() or ".." in pattern.replace("\\", "/").split("/"):
        raise MappingConfigError("run_discovery.pattern: invalid line-unit pattern")
    return sorted(path for path in root.glob(pattern) if path.is_file())


def _event(run_id: str, item: MultiSourceEvent, ordinal: int | None = None) -> Event:
    value = item.ordinal if ordinal is None else ordinal
    return Event(
        id=f"{run_id}:{value}", run_id=run_id, ordinal=value,
        turn=item.turn if item.turn is not None else value,
        timestamp=item.timestamp, agent_id=item.agent_id, role=item.role,
        type=item.type, phase=item.phase, content=item.content,
        structured=item.structured, tokens_in=item.tokens_in,
        tokens_out=item.tokens_out, cost=item.cost, refs=[], metadata=item.metadata,
    )


def _flat_event(run_id: str, ordinal: int, fields: object, record: dict[str, object]) -> Event:
    # EventFields is intentionally duck-typed here to keep the two assembly paths compact.
    content = fields.content
    structured = None
    if not isinstance(content, str):
        content = json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True)
        structured = record
    return Event(
        id=f"{run_id}:{ordinal}", run_id=run_id, ordinal=ordinal,
        turn=fields.turn if fields.turn is not None else ordinal,
        timestamp=fields.timestamp, agent_id=fields.agent_id, role=fields.role,
        type=fields.type, phase=fields.phase, content=content,
        structured=structured, tokens_in=fields.tokens_in,
        tokens_out=fields.tokens_out, cost=fields.cost, refs=[], metadata=fields.metadata,
    )


def _run(
    source: RunSource,
    experiment_id: str,
    events: list[Event],
    metadata: dict[str, object],
    outcome: str | None,
    warnings: int,
    repaired: int,
) -> Run:
    timestamps = [event.timestamp for event in events if event.timestamp is not None]
    started = min(timestamps) if timestamps else None
    ended = max(timestamps) if timestamps else None
    agents = list(dict.fromkeys(e.agent_id for e in events if e.agent_id is not None))
    phases = list(dict.fromkeys(e.phase for e in events if e.phase is not None))
    tokens_in = [e.tokens_in for e in events if e.tokens_in is not None]
    tokens_out = [e.tokens_out for e in events if e.tokens_out is not None]
    costs = [e.cost for e in events if e.cost is not None]
    return Run(
        id=source.run_id, experiment_id=experiment_id,
        source_path=_source_path(source.events_path), metadata=metadata, outcome=outcome,
        started_at=started, ended_at=ended,
        duration_s=(ended - started).total_seconds() if started and ended else None,
        n_events=len(events), n_turns=len({e.turn for e in events if e.turn is not None}),
        agent_ids=agents, phases=phases,
        tokens_in=sum(tokens_in) if tokens_in else None,
        tokens_out=sum(tokens_out) if tokens_out else None,
        total_cost=sum(costs) if costs else None,
        ingest_warnings=warnings, n_repaired=repaired,
    )


def _run_extractor(config: MappingConfig) -> Extractor:
    return Extractor(config.model_copy(update={"event": EventConfig()}))


def _assemble_sources(
    config: MappingConfig, source: RunSource, record: dict[str, object], experiment_id: str
) -> tuple[Run, list[Event], ExtractionStats]:
    extractor = MultiSourceExtractor(config)
    events = [_event(source.run_id, item) for item in extractor.extract_events(record)]
    run_extractor = _run_extractor(config)
    basis = source.manifest if source.manifest is not None else record
    fields = run_extractor.extract_run_fields(basis)
    for path, counter in run_extractor.stats.fields.items():
        target = extractor.stats.fields.setdefault(path, FieldStats())
        target.hits += counter.hits
        target.misses += counter.misses
        target.failures += counter.failures
    warnings = (
        extractor.stats.total_warnings
        + run_extractor.stats.total_warnings
        + len(source.warnings)
    )
    return (
        _run(source, experiment_id, events, fields.metadata, fields.outcome,
             warnings, extractor.stats.n_repaired),
        events,
        extractor.stats,
    )


def _assemble_flat(
    config: MappingConfig, source: RunSource, experiment_id: str
) -> tuple[Run, list[Event], ExtractionStats]:
    extractor = Extractor(config)
    parsed: list[dict[str, object]] = []
    malformed = 0
    events: list[Event] = []
    for _, item in iter_jsonl_records(source.events_path):
        if isinstance(item, str):
            malformed += 1
            continue
        parsed.append(item)
        fields = extractor.extract_event_fields(item)
        if fields is None:
            continue
        events.append(_flat_event(source.run_id, len(events), fields, item))

    basis = source.manifest if source.manifest is not None else (parsed[0] if parsed else {})
    run_fields = extractor.extract_run_fields(basis)
    if config.agents is not None:
        table = RosterJoin(config.agents).build(basis)
        events = [
            replace(event, role=result.role, metadata=result.metadata)
            for event in events
            for result in [table.apply(event.agent_id, event.role, event.metadata)]
        ]
        join_warnings = table.warnings.total
    else:
        join_warnings = 0
    warnings = extractor.stats.total_warnings + malformed + join_warnings
    return (_run(source, experiment_id, events, run_fields.metadata, run_fields.outcome,
                 warnings, 0), events, extractor.stats)


def _first_record(path: Path) -> dict[str, object]:
    for _, item in iter_jsonl_records(path):
        if isinstance(item, dict):
            return item
    return {}


def _process(
    config: MappingConfig,
    sources: Iterable[RunSource],
    experiment_id: str,
) -> list[tuple[RunSource, Run, list[Event], ExtractionStats]]:
    result = []
    for source in sources:
        if config.event.sources is not None:
            record = _first_record(source.events_path)
            run, events, stats = _assemble_sources(config, source, record, experiment_id)
        else:
            run, events, stats = _assemble_flat(config, source, experiment_id)
        result.append((source, run, events, stats))
    return result


def ingest(
    config: MappingConfig,
    root: Path,
    store: SqliteStore,
    *,
    adapter_ref: str | None = None,
    reingest: bool = False,
    progress: Progress | None = None,
) -> IngestReport:
    """Discover, extract, summarize, and persist runs below *root*."""
    if config.run_discovery.unit in ("line", "json") and config.event.sources is None:
        raise MappingConfigError(
            f"event.sources: sources are required for {config.run_discovery.unit} units"
        )

    report = IngestReport()
    digest = _config_hash(config)
    old_digest = store.meta_get("config_hash")
    report.config_hash_warning = old_digest is not None and old_digest != digest
    experiment_id = stable_root_hash(root)
    known_fingerprints = store.fingerprints()
    existing = {run.id: run for run in store.list_runs()}
    store.meta_set("discovery_unit", config.run_discovery.unit)

    if config.run_discovery.unit in ("line", "json"):
        candidate_files = _line_files(config, root)
        changed_files = [
            path for path in candidate_files
            if reingest or known_fingerprints.get(_source_path(path))
            != (path.stat().st_mtime, path.stat().st_size)
        ]
        unchanged = {_source_path(path) for path in candidate_files if path not in changed_files}
        report.runs_skipped_unchanged = sum(
            run.source_path in unchanged for run in existing.values()
        )
        unchanged_ids = {
            run.id for run in existing.values() if run.source_path in unchanged
        }
        discovery = discover_runs_with_report(
            config, root, include_paths=changed_files, reserved_ids=unchanged_ids
        )
        report.line_failures.extend(discovery.line_failures)
        report.per_file_line_failures.update(discovery.per_file_failure_counts)
        sources_by_file: dict[Path, list[RunSource]] = defaultdict(list)
        for source in discovery.sources:
            sources_by_file[source.events_path].append(source)
        discoveries = [(path, sources_by_file[path]) for path in changed_files]
        total = sum(len(sources) for _, sources in discoveries)
        index = 0
        for file_path, file_sources in discoveries:
            source_path = _source_path(file_path)
            old_ids = {
                run.id for run in existing.values() if run.source_path == source_path
            }
            by_line = {
                source.line_no: source
                for source in file_sources
                if source.line_no is not None
            }
            store.delete_runs_for_source(source_path)
            if config.run_discovery.unit == "json":
                for source in file_sources:
                    if source.document is None:
                        continue
                    run, events, stats = _assemble_sources(
                        config, source, source.document, experiment_id
                    )
                    _merge_stats(report, stats, config)
                    try:
                        store.insert_run(run, events)
                    except sqlite3.IntegrityError as error:
                        report.line_failures.append((file_path, 1, f"store rejected run {source.run_id!r}: {error}"))
                        report.per_file_line_failures[file_path] = report.per_file_line_failures.get(file_path, 0) + 1
                        continue
                    if source.run_id in old_ids:
                        report.runs_replaced += 1
                    else:
                        report.runs_ingested += 1
                    index += 1
                    report.processed_run_ids.append(source.run_id)
                    if progress is not None:
                        progress(source.run_id, index, total)
                stat = file_path.stat()
                store.set_fingerprint(source_path, stat.st_mtime, stat.st_size)
                continue
            for line_no, item in iter_jsonl_records(file_path):
                source = by_line.get(line_no)
                if source is None or not isinstance(item, dict):
                    continue
                run, events, stats = _assemble_sources(config, source, item, experiment_id)
                _merge_stats(report, stats, config)
                try:
                    store.insert_run(run, events)
                except sqlite3.IntegrityError as error:
                    report.line_failures.append((file_path, line_no, f"store rejected run {source.run_id!r}: {error}"))
                    report.per_file_line_failures[file_path] = report.per_file_line_failures.get(file_path, 0) + 1
                    continue
                if source.run_id in old_ids:
                    report.runs_replaced += 1
                else:
                    report.runs_ingested += 1
                index += 1
                report.processed_run_ids.append(source.run_id)
                if progress is not None:
                    progress(source.run_id, index, total)
            stat = file_path.stat()
            store.set_fingerprint(source_path, stat.st_mtime, stat.st_size)

        if adapter_ref is not None:
            store.meta_set("adapter_ref", adapter_ref)
        store.meta_set("adapter_config_hash", digest)
        store.meta_set("config_hash", digest)
        store.meta_set("experiment_id", experiment_id)
        store.meta_set("root_path", _source_path(root))
        return report
    else:
        all_sources = discover_runs(config, root)
        changed_paths = {
            _source_path(source.events_path) for source in all_sources
            if reingest or known_fingerprints.get(_source_path(source.events_path))
            != (source.events_path.stat().st_mtime, source.events_path.stat().st_size)
        }
        sources = [s for s in all_sources if _source_path(s.events_path) in changed_paths]
        report.runs_skipped_unchanged = len(all_sources) - len(sources)

    total = len(sources)
    grouped: dict[str, list[RunSource]] = defaultdict(list)
    for source in sources:
        grouped[_source_path(source.events_path)].append(source)

    index = 0
    for path, file_sources in grouped.items():
        old_ids = {run.id for run in existing.values() if run.source_path == path}
        store.delete_runs_for_source(path)
        assembled = _process(config, file_sources, experiment_id)
        for source, run, events, stats in assembled:
            _merge_stats(report, stats, config)
            store.insert_run(run, events)
            if source.run_id in old_ids:
                report.runs_replaced += 1
            else:
                report.runs_ingested += 1
            index += 1
            report.processed_run_ids.append(source.run_id)
            if progress is not None:
                progress(source.run_id, index, total)
        stat = Path(path).stat()
        store.set_fingerprint(path, stat.st_mtime, stat.st_size)

    if adapter_ref is not None:
        store.meta_set("adapter_ref", adapter_ref)
    store.meta_set("adapter_config_hash", digest)
    store.meta_set("config_hash", digest)
    store.meta_set("experiment_id", experiment_id)
    store.meta_set("root_path", _source_path(root))
    return report


__all__ = ["IngestReport", "ingest", "stable_root_hash"]
