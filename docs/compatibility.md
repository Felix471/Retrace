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

## MAST trace coverage

The annotated release is `MAD_full_dataset.json` from the MAST repository
(github.com/multi-agent-systems-failure-taxonomy/MAST, commit a70542e, dataset
mcemri/MAD on Hugging Face). Counting rule: one top-level record in that file is
one annotated trace, grouped by its `mas_name` field. Counted 2026-09-22:
1,642 annotated traces in total. AG2 597, MetaGPT 430, ChatDev 330,
MagenticOne 195, OpenManus 30, AppWorld 30, HyperAgent 30. The config-only
subset (AG2 and HyperAgent, the two frameworks ingested with shipped configs)
is 627 of 1,642 annotated traces, or 38.2%.

## Native indexer

The optional native indexer (`native/jsonl-index`) writes a sidecar of byte
offsets so record access seeks instead of re-parsing; the build cost is paid
once per file. Measured 2026-09-22 on the same corpus, WSL2 Ubuntu 22.04.3
(kernel 6.18.33.2), AMD Ryzen 9 7950X3D, Python 3.11.16, inputs and repository
on ext4, medians of three runs, 1,000 sampled records (all 223 for HyperAgent).
Inputs were built with one JSON document per line from `AG2/**/*.json`
(7,184 documents) and `HyperAgent/*.json` (223 documents), tag sidecars
excluded, compact `json.dumps` with `ensure_ascii=False`; neither is committed.
No single file in the corpus approaches 400 MB.

| Measurement | AG2 (7,184 lines, 45.0 MiB) | HyperAgent (223 lines, 42.4 MiB) |
| --- | ---: | ---: |
| (a) Python full sequential pass | 0.111 s | 0.099 s |
| (b) Native index build | 0.139 s | 0.139 s |
| (c) Indexed random access, sampled records | 0.019 s | 0.075 s |
| (d) Python fetch of the same records by line number, one full pass | 0.117 s | 0.153 s |
| (e) Line-unit ingest without index | 16.212 s | 19.524 s |
| (e) Line-unit ingest with index | 16.329 s | 19.542 s |
| Ratio (a)/(b) | 0.80 | 0.71 |
| Ratio (d)/(c) | 6.29 | 2.04 |
| Ratio (e without)/(e with) | 0.99 | 1.00 |

Reading the index file is not the cost in line-unit ingest: extraction and
SQLite writes dominate (e), so the wired call site is unchanged in wall time.
The index build is slower than one Python pass on these files. Reproduce with
`python scripts/bench_jsonl_index.py <file.jsonl> --ingest-config <mapping.yaml>`.
