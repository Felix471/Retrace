# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to
Semantic Versioning.

## [Unreleased]

### Known issues

- The "Loading..." placeholder stays on the page after the data renders, below the content of the batch, replay, and compare views.

## [0.1.0] - 2026-09-27

### Added

- Ingest of structured logs through declarative mapping files, with several
  sources merged into one run, per-agent details joined from a roster, and
  known logging defects repaired and flagged.
- Four log layouts: one file per run, one directory per run, one line per
  run, and one JSON document per run (`unit: json`).
- `check`, `view`, and `init` commands to validate a log directory, open the
  viewer, and draft a mapping file.
- A local cache, so logs that have not changed are not parsed again.
- A local viewer with a batch table of all runs, a step-by-step replay of one
  run, and a side-by-side comparison of two runs.
- Failure tagging with the MAST failure modes, saved in sidecar files next to
  the logs, and a chart of how the tags are distributed.
- A 40-run demo dataset and user documentation.
- Tests that enforce the promise of no outbound network requests, and
  measured compatibility results in `docs/compatibility.md`.
- A recipe in `docs/mapping.md` for turning plain-text logs into JSONL before
  ingest.
- An optional native JSONL indexer (`native/jsonl-index`, C++17). It speeds up
  random access to records in large JSONL files; it does not speed up
  end-to-end ingest, where extraction and cache writes dominate.

### Changed

- The browser UI and the builtin mappings are part of the installed package,
  so the viewer works outside a checkout.
- The README links its privacy and compatibility claims to the tests and
  measurements behind them.
- The replay tag panel shows each tag's source and optional confidence.
- The replay tag form accepts several failure modes at once and saves one tag
  per mode.
- The documentation describes supported input as structured logs (JSON/JSONL,
  any layout).

### Fixed

- Cached experiments and tag sidecars stay stable across repeated local use.
- Changing a mapping file re-ingests all sources automatically instead of only
  warning.
- The test suite runs with a bare `pytest` from the repository root.
- Runs whose source files are gone are removed from the cache at the next
  ingest.
- Boolean and numeric group labels display as text in the batch table.
- Boolean and numeric metadata values keep their JSON types when runs are
  grouped.
- Run filters match boolean and numeric metadata values correctly.
- Tag sidecar files (`retrace.json`, `*.retrace.json`) are no longer picked up
  as runs.
- Duplicate run IDs in the JSON and line layouts fall back to relative-path
  IDs with a warning instead of stopping ingest.
