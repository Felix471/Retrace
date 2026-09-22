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
once per file. The default build classifies lines by their first byte only;
`--validate` additionally checks UTF-8 and JSON syntax. Measured 2026-09-22 on
WSL2 Ubuntu 22.04.3 (kernel 6.18.33.2), AMD Ryzen 9 7950X3D, Python 3.11.16,
inputs and repository on ext4, medians of three runs, 1,000 sampled records
(all 223 for HyperAgent). Corpus inputs were built with one JSON document per
line from `AG2/**/*.json` (7,184 documents), `HyperAgent/*.json` (223
documents), and the `MAD_full_dataset.json` array (1,642 records), tag sidecars
excluded, compact `json.dumps` with `ensure_ascii=False`. The synthetic row is
a generated file of 641,649 objects with no corpus content. None of the inputs
is committed.

| Measurement | AG2 (45.0 MiB) | HyperAgent (42.4 MiB) | MAD (189.6 MiB) | Synthetic (400.0 MiB) |
| --- | ---: | ---: | ---: | ---: |
| (a) Python full sequential pass | 0.134 s | 0.102 s | 0.479 s | 1.404 s |
| (b) Native index build, default | 0.038 s | 0.025 s | 0.157 s | 0.330 s |
| (b2) Native index build, `--validate` | 0.163 s | 0.139 s | 0.685 s | 1.522 s |
| (c) Indexed random access, sampled records | 0.021 s | 0.074 s | 0.217 s | 0.009 s |
| (d) Python fetch of the same records by line number, one pass | 0.124 s | 0.164 s | 0.676 s | 1.427 s |
| (e) Line-unit ingest without index | 17.415 s | 19.555 s | not run | not run |
| (e) Line-unit ingest with index | 16.212 s | 20.231 s | not run | not run |
| Ratio (a)/(b) | 3.54 | 4.01 | 3.06 | 4.25 |
| Ratio (a)/(b2) | 0.82 | 0.74 | 0.70 | 0.92 |
| Ratio (d)/(c) | 5.96 | 2.22 | 3.12 | 154.92 |
| Ratio (e without)/(e with) | 1.07 | 0.97 | not run | not run |

The validated build is slower than one Python pass on every input. Line-unit
ingest needs an array-valued event source, which the MAD and synthetic inputs
do not have, so (e) was not run for them. Reading the index is not the cost in
line-unit ingest: extraction and SQLite writes dominate (e), and the two ingest
ratios are within run-to-run noise. Reproduce with
`python scripts/bench_jsonl_index.py <file.jsonl> [--ingest-config <mapping.yaml>]`.
