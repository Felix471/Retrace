# Compatibility

These results are from ingesting the public MAST corpus traces with the shipped configs, measured 2026-08-21 on the author's machine.

## Measured ingest (config-only)

| Source | Documents | Runs | Events | Ingest failures | Configured-field hit rate | Time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| AG2 top-level `AG2/*.json` | 38 | 38 | 210 | 0 | 100% | 0.045 s |
| AG2 one experiment (`trajs_gpt-4_impr_prompt_impr_topology_42`) | 200 | 200 | 1,793 | 0 | 100% | 0.256 s |
| AG2 whole tree `AG2/**/*.json` | 7,184 | 7,184 | 44,102 | 0 | 100% | 10.347 s |
| HyperAgent `HyperAgent/*.json` | 223 | 223 | 873,441 | 0 | 100% | 19.324 s |

## Per-framework outcome

| Framework | Outcome |
| --- | --- |
| AG2 | Supported; native JSON documents; shipped as `builtin:ag2`. |
| HyperAgent | Supported via the included config (`gate/configs/hyperagent.yaml`); events are content-only (the source has no agent or turn fields). |
| MagenticOne | Not supported in v1: its native logs are free text, which is out of scope; see the text-log preprocessing recipe in [mapping.md](mapping.md). |
| OpenManus | Not supported in v1: its native logs are free text, which is out of scope; see the text-log preprocessing recipe in [mapping.md](mapping.md). |
| AppWorld | Not supported in v1: its native logs are free text, which is out of scope; see the text-log preprocessing recipe in [mapping.md](mapping.md). |
| ChatDev | Not supported in v1: its native logs are free text, which is out of scope; see the text-log preprocessing recipe in [mapping.md](mapping.md). |
| MetaGPT | Not supported in v1: its native logs are free text, which is out of scope; see the text-log preprocessing recipe in [mapping.md](mapping.md). |

The MAST corpus itself is not distributed with Retrace.
