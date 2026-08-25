# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to
Semantic Versioning.

## [Unreleased]

### Added

- Added `unit: json` discovery (D28), where each JSON document file is one run, as found by the MAST release gate.
- Added the D27 presentation rule and doc lint in `scripts/check_docs_presentation.py`, enforced in CI.
- Added the text-log preprocessing recipe in `docs/mapping.md` and a lint rule rejecting arbitrary-log claims.
- Added a local-only, gitignored bilingual analysis playbook as development tooling; it is not shipped.

### Changed

- The replay tag form accepts several failure modes at once and saves one tag per mode.
- Corrected D29 claims to "structured logs (JSON/JSONL, any layout)" and updated the README verdict line.

### Fixed

- Rendered boolean and numeric group labels as text in the batch UI.
- Preserved boolean and numeric JSON types in grouped run metadata values.
- Matched boolean and numeric metadata values correctly in run filters.
- Excluded `retrace.json` and `*.retrace.json` tag sidecars from run discovery.
- Made duplicate run IDs robust for JSON and line units (T33b) with warned relative-path fallback IDs so ingest never crashes, as found during the gate re-run.

## [0.1.0] - 2026-08-20

### Added

- Ingest engine with mapping configurations, multi-source merging, repairs,
  roster joins, and line, directory, and file discovery.
- SQLite cache and `check`, `view`, and `init` CLI commands.
- Local server with replay, batch, and compare views, MAST tagging with
  sidecars, and a tag distribution chart.
- Demo dataset, user documentation, and core/UI purity checks in CI.

### Changed

- Packaged the browser UI and builtin adapter mappings for installed use.

### Fixed

- Kept cached experiments and tag sidecars stable across repeated local use.
