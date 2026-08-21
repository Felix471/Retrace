# Retrace

Retrace is a local-first viewer for inspecting, replaying, tagging, and comparing
structured multi-agent logs. It is an offline inspection tool, not a monitoring
service, SDK, or cloud platform.

See the [changelog](CHANGELOG.md) for release notes.

## Why local

Retrace makes zero outbound network requests. It binds 127.0.0.1, serves only your own browser, reads your logs, and writes only *.retrace.json sidecars next to them plus its own cache in your user cache directory. Any data leaving the machine can only be a user-initiated explicit job - and none exist in v1.

- Inputs are parsed read-only and never executed; scripts shipped with a dataset are never run.
- The cache holds parsed copies of your logs in the user cache directory; delete it to remove them.
- Sidecars hold your tag notes.

## Install

PyPI publication has not happened. From a checkout, install the `retrace-logs`
console script with pipx:

```shell
pipx install .
```

## 60-second quickstart

From the repository checkout, validate the included data and start the viewer:

```shell
retrace-logs check demo/
retrace-logs view demo/
```

The check reports 40 runs, 523 events, and zero warnings. The viewer opens a
local browser page with those 40 runs and a five-tag failure-mode distribution.
Use `retrace-logs view demo/ --no-browser` when a browser must not be opened.

tested against real AG2 and HyperAgent traces from the MAST corpus (config-only); free-text logs are out of scope in v1.
(HyperAgent traces ingest as content-only events - no agent or turn fields exist in the source.)

The batch view lists runs, outcomes, costs, metadata filters and groups, and the
MAST tag distribution. It is the starting point for opening or selecting runs.

The replay view presents one run as an ordered timeline, with agent, phase,
event-type, and text filters. It also lets you add run-level or event-anchored
failure tags and notes.

The compare view aligns two selected runs and identifies structural and content
divergences. Comparison uses the stored event sequence; it is not semantic
similarity analysis.

## Bring your own structured logs

Retrace accepts structured logs (JSON/JSONL, any of the supported layouts: one
file per run, one directory per run, one line per run, one JSON document per
run).

Draft and refine a declarative mapping, validate it, then view the result:

```shell
retrace-logs init path/to/logs --out path/to/logs/retrace.yaml
# Edit path/to/logs/retrace.yaml.
retrace-logs check path/to/logs
retrace-logs view path/to/logs
```

See the [mapping reference](docs/mapping.md), Python adapter protocol (a typed
extension seam; custom Python adapters are not loadable in v1 - see
[docs/adapters.md](docs/adapters.md)), and [tagging guide](docs/tagging.md).

This is the author's real experiment corpus, included because it contains real
logging defects the tool repairs and flags; the game domain is irrelevant.

Shipped builtins are `builtin:ag2` (one JSON document per run, `unit: json`),
`builtin:support_pipeline` (one directory per run), and `builtin:avalon`
(one JSONL line per run).

Screenshots: pending (the project owner will capture them).

## License

Apache License 2.0. See [LICENSE](LICENSE).
