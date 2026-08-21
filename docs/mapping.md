# Mapping configuration reference

A mapping is strict YAML: unknown keys and loose type substitutions are rejected.
Expressions are JMESPath unless a field is described as a path, glob, template,
or literal. This complete example shows the hierarchy; the tables below define
every accepted key.

```yaml
retrace_mapping: 1
run_discovery: {pattern: "case-*", unit: dir, events_file: events.jsonl}
run:
  id: "{dir_name}"
  manifest: meta.json
  metadata: {condition: config.condition}
  outcome: result
event:
  where: "kind != 'debug'"
  turn: sequence
  timestamp: occurred_at
  agent_id: actor
  role: actor_role
  type: {from: kind, map: {chat: message}, default: other}
  phase: stage
  content: text
  tokens_in: usage.input
  tokens_out: usage.output
  cost: usage.cost
  metadata: rest
agents: {path: roster, key: id, attributes: {team: team, role: role}}
sniff: {required_fields: [kind, text]}
```

## Top level and run discovery

| Key | Type and presence | Meaning | Short example |
| --- | --- | --- | --- |
| `retrace_mapping` | integer, required; exactly 1 | Mapping schema version. | `retrace_mapping: 1` |
| `run_discovery` | object, required | Filesystem/run layout settings. | `run_discovery: {...}` |
| `pattern` | string, required | Relative glob below the input root. Absolute paths and `..` are rejected. | `pattern: "**/*.jsonl"` |
| `unit` | `file`, `dir`, `line`, or `json`; optional, default `file` | One matched file, directory, JSONL line, or whole JSON document is one run. Supplying `events_file` implies `dir`. | `unit: json` |
| `events_file` | string, optional; directory only | Event JSONL filename inside each matched run directory. | `events_file: events.jsonl` |

The file layout (`unit: file`) maps every matched JSONL file to a run. The
directory layout (`unit: dir`) maps every matched directory to a run and reads
its `events_file`. The line-per-run layout (`unit: line`) maps each JSON object
line in each matched aggregate file to a run; it requires multi-source events.
Malformed lines are reported and skipped without aborting other runs.
Null, non-scalar, or duplicate IDs fall back to `file_stem#Lnumber` (then relative path) for line units and the extensionless relative POSIX path for JSON units.
The document layout (`unit: json`) parses each matched file as one UTF-8 JSON
object (an optional BOM is accepted). It requires multi-source events; malformed
or non-object documents are reported as `path:1` and skipped. For example:

```yaml
run_discovery: {pattern: "runs/*.json", unit: json}
run: {id: run_id, metadata: {condition: condition}}
event:
  sources:
    - {name: messages, path: messages, fields: {content: text, role: role}}
```

## Run

| Key | Type and presence | Meaning | Short example |
| --- | --- | --- | --- |
| `run` | object, required | Run identity and run-level extraction. | `run: {...}` |
| `id` | string, required | For file/dir layouts, a path template; for line/json layouts, a JMESPath expression evaluated against the run record/document. | `id: "{file_stem}"` |
| `manifest` | string, optional | JSON file relative to a run directory; it becomes the run extraction basis. | `manifest: meta.json` |
| `metadata` | map of output name to string expression, optional, default empty | Extracts arbitrary filter/group metadata. | `metadata: {model: config.model}` |
| `outcome` | string expression, optional | Extracts and string-coerces the run outcome. | `outcome: result.status` |

Without a manifest, run fields use the first valid event record (or the complete
line record for multi-source and line layouts, or the complete JSON document
for a json layout). `run.manifest` is ignored for line and json layouts.

## Flat event fields

`event` is required. In flat mode it accepts these slots. Except for the special
forms noted below, every value is an optional string JMESPath expression.

| Key | Type and presence | Meaning | Short example |
| --- | --- | --- | --- |
| `event` | object, required | Flat extraction or multi-source definition. | `event: {content: text}` |
| `where` | string, optional | Drops a record when the expression is JMESPath-false. | `where: "kind != 'debug'"` |
| `turn` | string, optional | Integer event turn; absent values fall back to ordinal at assembly. | `turn: sequence` |
| `timestamp` | string, optional | Timestamp parsed into the canonical model. | `timestamp: created_at` |
| `agent_id` | string, optional | String agent identity. | `agent_id: actor.id` |
| `role` | string, optional | String role; an absent role may be filled by a roster join. | `role: actor.role` |
| `type` | string or mapping object, optional | Closed type: `message`, `tool_call`, `tool_result`, `system`, or `other`. | `type: kind` |
| `phase` | string, optional | String phase label. | `phase: workflow.stage` |
| `content` | string, optional | Display text; if unavailable, the source object is rendered as structured JSON. | `content: body` |
| `tokens_in` | string, optional | Integer input-token count. | `tokens_in: usage.prompt` |
| `tokens_out` | string, optional | Integer output-token count. | `tokens_out: usage.completion` |
| `cost` | string, optional | Finite floating-point cost. | `cost: usage.cost` |
| `metadata` | literal `rest`, optional | Retains unmapped source keys as event metadata. | `metadata: rest` |

The mapped `type` object has required `from` (source expression; represented as
`from_` internally only), optional `map`
(string source values to closed event types, default empty), and optional
`default` (a closed type used when no map entry matches). Example:
`type: {from: kind, map: {assistant: message}, default: other}`. A null default
is invalid.

## Multi-source events and repairs

`sources` cannot be mixed with the flat slots (including `where` or flat
`metadata`). `merge` is valid only with sources.

| Key | Type and presence | Meaning | Short example |
| --- | --- | --- | --- |
| `sources` | non-empty list, optional | Arrays within one run record that contribute events. | `sources: [{name: chat, path: messages}]` |
| `name` | string, required | Unique source name used in reports and tie-breaking. | `name: messages` |
| `path` | string, required | JMESPath selecting the source array. | `path: trace.messages` |
| `type` | closed event-type literal, optional | Constant type for this source. | `type: tool_call` |
| `phase` | string, optional | Constant phase for this source. | `phase: planning` |
| `role` | string, optional | Constant role for this source. | `role: reviewer` |
| `priority` | integer, optional, default 0 | Stable source priority used when merge keys tie. | `priority: 10` |
| `fields` | object, optional, default empty | Per-record extraction using `turn`, `timestamp`, `agent_id`, `role`, `type`, `phase`, `content`, `tokens_in`, `tokens_out`, `cost`, and `metadata`; `where` is not accepted here. | `fields: {content: text}` |
| `repairs` | list, optional, default empty | Fallback repairs applied only when a target field is missing or failed. | `repairs: [{field: turn, strategy: ordinal}]` |
| `field` | string, required | Slot or metadata field repaired; `_retrace` is forbidden. | `field: turn` |
| `strategy` | `ordinal` or `derive`, required | Uses source position or evaluates an expression. | `strategy: derive` |
| `base` | integer, ordinal only, optional, default 0 | Value added to the zero-based source position. | `base: 1` |
| `expr` | string, derive only, required | JMESPath evaluated over the original source record. | `expr: status.code` |
| `map` | scalar-key/scalar-value map, derive only, optional | Remaps the derived result; keys are normalized to strings. | `map: {"True": success}` |
| `merge` | object, optional | Configures the merged event ordering. | `merge: {sort_by: turn}` |
| `sort_by` | string, required inside `merge` | Event slot used as primary merge key. | `sort_by: timestamp` |

A repair fires only for a miss or coercion/evaluation failure; it never replaces
a successful extraction. Fired repairs count as ingest warnings and appear in
the UI. Original values are visible in `metadata._retrace.repaired`; repaired
source data may also be used for fallback structured rendering. Repair-expression
failures are counted separately and leave the target absent.

## Agent roster join

| Key | Type and presence | Meaning | Short example |
| --- | --- | --- | --- |
| `agents` | object, optional | Per-run roster joined to events by agent ID. | `agents: {...}` |
| `path` | string, required | JMESPath selecting a roster array. | `path: participants` |
| `key` | string, required | JMESPath selecting each roster entry's join key. | `key: id` |
| `attributes` | non-empty map of name to string expression, required | Extracted attributes; `role` fills a missing event role and others go under reserved metadata. | `attributes: {role: role, team: team}` |

Keys are compared as strings; the first duplicate roster entry wins. A missing
roster/path or unmatched event agent produces a warning. Joined non-role
attributes are stored at `metadata._retrace.agent`.

## Sniffing

| Key | Type and presence | Meaning | Short example |
| --- | --- | --- | --- |
| `sniff` | object, optional | Data-only signature for built-in selection. | `sniff: {required_fields: [kind]}` |
| `required_fields` | non-empty list of strings, required | Top-level keys that must all occur in the first valid candidate record. | `required_fields: [id, events]` |

This is the author's real experiment corpus, included because it contains real
logging defects the tool repairs and flags; the game domain is irrelevant.
Resolution order is: explicit `--config`; a `retrace.yaml` beside the target;
the first matching shipped builtin (`builtin:avalon` or
`builtin:support_pipeline`); otherwise `retrace-logs init` is suggested to
create a draft. `init` does not bypass validation: edit the draft, then run
`retrace-logs check`.

## Coercion, misses, and reserved metadata

A missing expression result is a miss and normally becomes `None` (with event
type falling back to `other`). An invalid expression or a value that cannot be
coerced is a failure and contributes an ingest warning. Integer slots accept
integers, integral floats, and unsigned decimal strings, but reject booleans.
Floating cost accepts numeric values and numeric strings, rejects booleans, and
rejects non-finite NaN or infinity. String slots accept strings and finite
numeric values, but not booleans or containers. Timestamp inputs must parse as
supported timestamps. Invalid values do not abort the run.

`_retrace` is the reserved engine metadata namespace. Source `metadata: rest`
cannot claim it: collisions are removed and warned. Retrace uses it for repair
originals and roster-joined agent attributes; mappings and repairs must not use
it for user data.
