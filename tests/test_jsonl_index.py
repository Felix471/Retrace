from __future__ import annotations

import json
import os
import struct
import subprocess
from pathlib import Path

import pytest

import retrace.adapters.discovery as discovery_module
import retrace.core.ingest as ingest_module
from retrace.adapters.discovery import iter_jsonl_records, iter_jsonl_records_indexed
from retrace.adapters.mapping_schema import validate_mapping_config
from retrace.core.ingest import ingest
from retrace.core.jsonl_index import (
    FLAG_VALIDATED,
    HEADER_STRUCT,
    RECORD_STRUCT,
    STATUS_BLANK,
    STATUS_INVALID_JSON,
    STATUS_INVALID_UTF8,
    STATUS_NOT_OBJECT,
    STATUS_OK,
    find_indexer,
    index_path,
    load_index,
)
from retrace.core.store import SqliteStore


def fixture_bytes() -> bytes:
    """Return neutral JSONL bytes covering line endings and classification outcomes."""
    return b"".join(
        [
            b'\xef\xbb\xbf{"id": 1, "text": "hello"}\r\n',
            b'{"id": 2, "text": "second"}\r\n',
            b'{"id": 3}\n',
            b"\r\n",
            b" \t  \n",
            b'{"a": "\xff"}\n',
            b'{"id": invalid}\n',
            b"[1, 2]\n",
            b"  {}\n",
            '{"id": 4, "data": {"text": "café"}}\n'.encode(),
            b'{"id": 5, "text": "last"}',
        ]
    )


def _reference_status(line_no: int, raw_line: bytes) -> int:
    try:
        line = raw_line.decode("utf-8-sig" if line_no == 1 else "utf-8")
    except UnicodeDecodeError:
        return STATUS_INVALID_UTF8
    if not line.strip():
        return STATUS_BLANK
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return STATUS_INVALID_JSON
    return STATUS_OK if isinstance(value, dict) else STATUS_NOT_OBJECT


def _fast_reference_status(line_no: int, raw_line: bytes) -> int:
    line = raw_line.removeprefix(b"\xef\xbb\xbf") if line_no == 1 else raw_line
    if not line or all(byte in b" \t\n\r\x0b\x0c" for byte in line):
        return STATUS_BLANK
    first = next((byte for byte in line if byte not in b" \t\n\r"), None)
    return STATUS_OK if first == ord("{") else STATUS_NOT_OBJECT


def write_reference_index(
    source: Path,
    output: Path | None = None,
    *,
    validated: bool = True,
) -> Path:
    """Write a deterministic test index from the documented physical-line format."""
    records: list[bytes] = []
    offset = 0
    with source.open("rb") as stream:
        for line_no, raw_line in enumerate(stream, start=1):
            status = (
                _reference_status(line_no, raw_line)
                if validated
                else _fast_reference_status(line_no, raw_line)
            )
            records.append(RECORD_STRUCT.pack(offset, len(raw_line), status))
            offset += len(raw_line)
    stat = source.stat()
    target = index_path(source) if output is None else output
    target.write_bytes(
        HEADER_STRUCT.pack(
            b"RIDX",
            2,
            HEADER_STRUCT.size,
            stat.st_size,
            stat.st_mtime_ns,
            len(records),
            FLAG_VALIDATED if validated else 0,
            0,
        )
        + b"".join(records)
    )
    return target


def test_index_path_appends_suffix() -> None:
    assert index_path(Path("a.jsonl")) == Path("a.jsonl.ridx")


def test_find_indexer_env_var_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = tmp_path / "configured.exe"
    configured.write_bytes(b"")
    monkeypatch.setenv("RETRACE_JSONL_INDEXER", str(configured))
    monkeypatch.setattr("retrace.core.jsonl_index.shutil.which", lambda _: "elsewhere.exe")
    assert find_indexer() == configured


def test_find_indexer_falls_back_to_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETRACE_JSONL_INDEXER", raising=False)
    monkeypatch.setattr("retrace.core.jsonl_index.shutil.which", lambda _: "available.exe")
    assert find_indexer() == Path("available.exe")


def test_find_indexer_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETRACE_JSONL_INDEXER", raising=False)
    monkeypatch.setattr("retrace.core.jsonl_index.shutil.which", lambda _: None)
    assert find_indexer() is None


def test_find_indexer_ignores_missing_env_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRACE_JSONL_INDEXER", "missing.exe")
    monkeypatch.setattr("retrace.core.jsonl_index.shutil.which", lambda _: "available.exe")
    assert find_indexer() == Path("available.exe")


def test_fallback_when_no_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "records.jsonl"
    source.write_bytes(fixture_bytes())
    expected = list(iter_jsonl_records(source))
    assert list(iter_jsonl_records_indexed(source)) == expected
    monkeypatch.setattr(discovery_module, "load_index", lambda _: None)
    assert list(iter_jsonl_records_indexed(source)) == expected


@pytest.mark.parametrize("validated", [False, True])
def test_reference_index_parity(tmp_path: Path, validated: bool) -> None:
    source = tmp_path / "records.jsonl"
    source.write_bytes(fixture_bytes())
    write_reference_index(source, validated=validated)

    assert list(iter_jsonl_records_indexed(source)) == list(iter_jsonl_records(source))
    loaded = load_index(source)
    assert loaded is not None
    assert loaded.validated is validated
    expected_statuses = (
        [
            STATUS_OK,
            STATUS_OK,
            STATUS_OK,
            STATUS_BLANK,
            STATUS_BLANK,
            STATUS_INVALID_UTF8,
            STATUS_INVALID_JSON,
            STATUS_NOT_OBJECT,
            STATUS_OK,
            STATUS_OK,
            STATUS_OK,
        ]
        if validated
        else [
            STATUS_OK,
            STATUS_OK,
            STATUS_OK,
            STATUS_BLANK,
            STATUS_BLANK,
            STATUS_OK,
            STATUS_OK,
            STATUS_NOT_OBJECT,
            STATUS_OK,
            STATUS_OK,
            STATUS_OK,
        ]
    )
    assert [entry.status for entry in loaded.entries] == expected_statuses


def test_record_random_access(tmp_path: Path) -> None:
    source = tmp_path / "records.jsonl"
    source.write_bytes(fixture_bytes())
    write_reference_index(source)
    loaded = load_index(source)
    assert loaded is not None
    expected = [item for _, item in iter_jsonl_records(source) if isinstance(item, dict)]
    assert [loaded.record_object(n) for n in range(len(loaded))] == expected
    assert loaded.record(0).startswith(b"\xef\xbb\xbf")
    with pytest.raises(IndexError):
        loaded.record_object(len(loaded))


@pytest.mark.parametrize("change", ["size", "mtime"])
def test_stale_index_rejected(tmp_path: Path, change: str) -> None:
    source = tmp_path / "records.jsonl"
    source.write_bytes(fixture_bytes())
    write_reference_index(source)
    assert load_index(source) is not None
    if change == "size":
        with source.open("ab") as stream:
            stream.write(b"x")
    else:
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert load_index(source) is None
    assert list(iter_jsonl_records_indexed(source)) == list(iter_jsonl_records(source))


@pytest.mark.parametrize(
    "corruption",
    [
        "truncated",
        "magic",
        "version",
        "count",
        "flags",
        "reserved",
        "offset",
        "status",
        "ending",
    ],
)
def test_malformed_index_rejected(tmp_path: Path, corruption: str) -> None:
    source = tmp_path / "records.jsonl"
    source.write_bytes(fixture_bytes())
    sidecar = write_reference_index(source)
    data = bytearray(sidecar.read_bytes())
    if corruption == "truncated":
        del data[-1:]
    elif corruption == "magic":
        data[0:4] = b"NOPE"
    elif corruption == "version":
        struct.pack_into("<H", data, 4, 1)
    elif corruption == "count":
        struct.pack_into("<Q", data, 24, 12)
    elif corruption == "flags":
        struct.pack_into("<I", data, 32, 2)
    elif corruption == "reserved":
        struct.pack_into("<I", data, 36, 1)
    elif corruption == "offset":
        struct.pack_into("<Q", data, HEADER_STRUCT.size + RECORD_STRUCT.size, 1)
    elif corruption == "status":
        data[HEADER_STRUCT.size + 12] = 9
    else:
        last_record = HEADER_STRUCT.size + 10 * RECORD_STRUCT.size
        original_length = struct.unpack_from("<I", data, last_record + 8)[0]
        struct.pack_into("<I", data, last_record + 8, original_length - 1)
    sidecar.write_bytes(data)

    assert load_index(source) is None
    assert list(iter_jsonl_records_indexed(source)) == list(iter_jsonl_records(source))


def test_ingest_line_unit_uses_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text(
        '\n'.join(
            [
                json.dumps({"id": "run-a", "items": [{"text": "hello"}]}),
                json.dumps({"id": "run-b", "items": [{"text": "goodbye"}]}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_reference_index(source)
    config = validate_mapping_config(
        {
            "retrace_mapping": 1,
            "run_discovery": {"pattern": "*.jsonl", "unit": "line"},
            "run": {"id": "id"},
            "event": {
                "sources": [
                    {"name": "items", "path": "items", "fields": {"content": "text"}}
                ]
            },
        }
    )
    calls: list[Path] = []
    original = ingest_module.iter_jsonl_records_indexed

    def tracking_iterator(path: Path):
        calls.append(path)
        yield from original(path)

    monkeypatch.setattr(ingest_module, "iter_jsonl_records_indexed", tracking_iterator)
    with SqliteStore(tmp_path / "cache.db") as store:
        report = ingest(config, tmp_path, store)
        runs = store.list_runs()
        counts = [store.get_events(run.id)[1] for run in runs]

    assert calls == [source]
    assert report.processed_run_ids == ["run-a", "run-b"]
    assert [(run.id, run.n_events) for run in runs] == [("run-a", 1), ("run-b", 1)]
    assert counts == [1, 1]


def test_real_binary_parity(tmp_path: Path) -> None:
    binary = find_indexer()
    if binary is None:
        pytest.skip("native JSONL indexer is not available")
    source = tmp_path / "records.jsonl"
    source.write_bytes(fixture_bytes())
    before = source.read_bytes()
    before_mtime_ns = source.stat().st_mtime_ns

    references: dict[bool, Path] = {}
    for validated in (False, True):
        command = [str(binary), str(source)]
        if validated:
            command.append("--validate")
        subprocess.run(command, check=True, capture_output=True)
        assert index_path(source).exists()
        loaded = load_index(source)
        assert loaded is not None
        assert loaded.validated is validated
        assert source.read_bytes() == before
        assert source.stat().st_mtime_ns == before_mtime_ns
        assert list(iter_jsonl_records_indexed(source)) == list(iter_jsonl_records(source))
        reference = write_reference_index(
            source,
            tmp_path / f"reference-{validated}.ridx",
            validated=validated,
        )
        references[validated] = reference
        assert index_path(source).read_bytes() == reference.read_bytes()

    custom = tmp_path / "custom.ridx"
    subprocess.run([str(binary), str(source), "-o", str(custom)], check=True, capture_output=True)
    assert custom.read_bytes() == references[False].read_bytes()
    missing = subprocess.run(
        [str(binary), str(tmp_path / "missing.jsonl")],
        check=False,
        capture_output=True,
    )
    assert missing.returncode == 2
