# Retrace

Retrace is a local-first viewer for inspecting, replaying, tagging, and comparing
structured multi-agent logs. It is an offline inspection tool, not a monitoring
service, SDK, or cloud platform.

See the [changelog](CHANGELOG.md) for release notes.

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

The batch view lists runs, outcomes, costs, metadata filters and groups, and the
MAST tag distribution. It is the starting point for opening or selecting runs.

The replay view presents one run as an ordered timeline, with agent, phase,
event-type, and text filters. It also lets you add run-level or event-anchored
failure tags and notes.

The compare view aligns two selected runs and identifies structural and content
divergences. Comparison uses the stored event sequence; it is not semantic
similarity analysis.

## Bring your own logs

Draft and refine a declarative mapping, validate it, then view the result:

```shell
retrace-logs init path/to/logs --out path/to/logs/retrace.yaml
# Edit path/to/logs/retrace.yaml.
retrace-logs check path/to/logs
retrace-logs view path/to/logs
```

See the [mapping reference](docs/mapping.md), [Python adapter protocol](docs/adapters.md),
and [tagging guide](docs/tagging.md).

Screenshots: pending (the project owner will capture them).

## License

Apache License 2.0. See [LICENSE](LICENSE).
