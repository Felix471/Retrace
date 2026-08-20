# Adapters

Declarative YAML mappings are Retrace's primary format-integration mechanism.
They are validated, inspectable, and cover filesystem discovery, JMESPath field
extraction, multi-source merging, repairs, and roster joins. See [mapping.md](mapping.md).

For formats YAML cannot express, such as a repair that depends on several
records, the typed Python escape hatch is `retrace.adapters.protocol.Adapter`:

```python
class Adapter(Protocol):
    name: str
    def discover_runs(self, root: Path) -> Iterable[RunSource]: ...
    def parse_run(self, src: RunSource) -> tuple[Run, Iterator[Event]]: ...
```

`discover_runs` identifies sources beneath a root. `parse_run` returns the run
summary and a deterministic iterator of events. The protocol is structural and
runtime-checkable, so an implementation need not inherit from `Adapter`.

Retrace v1 has no loader, plugin registration, import-string option, or CLI flag
for custom Python adapters. The protocol is a typed extension seam only.

Not implemented: future wiring could accept an explicitly configured import
path, load an object in a controlled adapter registry, validate it against the
protocol, and pass its output through the same store and UI. No such behavior
should be inferred from the v1 interface.
