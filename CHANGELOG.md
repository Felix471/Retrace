# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to
Semantic Versioning.

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
