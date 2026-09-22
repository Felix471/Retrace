"""Read and validate optional native indexes for JSON Lines sources."""

from __future__ import annotations

import json
import os
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path

INDEX_SUFFIX = ".ridx"
ENV_VAR = "RETRACE_JSONL_INDEXER"
EXECUTABLE_NAME = "retrace-jsonl-index"

STATUS_OK = 0
STATUS_BLANK = 1
STATUS_INVALID_UTF8 = 2
STATUS_INVALID_JSON = 3
STATUS_NOT_OBJECT = 4

HEADER_STRUCT = struct.Struct("<4sHHQqQ")
RECORD_STRUCT = struct.Struct("<QIB")

_MAGIC = b"RIDX"
_FORMAT_VERSION = 1
_HEADER_SIZE = 32
_VALID_STATUSES = {
    STATUS_OK,
    STATUS_BLANK,
    STATUS_INVALID_UTF8,
    STATUS_INVALID_JSON,
    STATUS_NOT_OBJECT,
}


def index_path(source: Path) -> Path:
    """Return the sidecar index path for *source*."""
    return source.with_name(source.name + INDEX_SUFFIX)


def find_indexer() -> Path | None:
    """Find an explicitly configured indexer or one available on PATH."""
    configured = os.environ.get(ENV_VAR)
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return candidate
    discovered = shutil.which(EXECUTABLE_NAME)
    return Path(discovered) if discovered is not None else None


@dataclass(frozen=True)
class IndexEntry:
    """The byte range and classification of one physical source line."""

    line_no: int
    byte_offset: int
    byte_length: int
    status: int


@dataclass(frozen=True)
class JsonlIndex:
    """A fully validated, read-only index of a JSON Lines source."""

    source: Path
    source_size: int
    source_mtime_ns: int
    entries: tuple[IndexEntry, ...]
    _ok_entries: tuple[IndexEntry, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_ok_entries",
            tuple(entry for entry in self.entries if entry.status == STATUS_OK),
        )

    @property
    def record_count(self) -> int:
        """Return the number of indexed JSON object records."""
        return len(self._ok_entries)

    def __len__(self) -> int:
        return self.record_count

    def record(self, n: int) -> bytes:
        """Read the raw bytes for the zero-based JSON object record *n*."""
        if n < 0 or n >= len(self._ok_entries):
            raise IndexError(n)
        entry = self._ok_entries[n]
        with self.source.open("rb") as stream:
            stream.seek(entry.byte_offset)
            raw_line = stream.read(entry.byte_length)
        if len(raw_line) != entry.byte_length:
            raise OSError(f"source changed while reading indexed record {n}")
        return raw_line

    def record_object(self, n: int) -> dict:
        """Decode and parse the zero-based JSON object record *n*."""
        if n < 0 or n >= len(self._ok_entries):
            raise IndexError(n)
        entry = self._ok_entries[n]
        encoding = "utf-8-sig" if entry.line_no == 1 else "utf-8"
        value = json.loads(self.record(n).decode(encoding))
        if not isinstance(value, dict):
            raise TypeError(f"indexed record {n} is not a JSON object")
        return value


def _parse_entries(
    data: bytes, record_count: int, source_size: int
) -> tuple[IndexEntry, ...] | None:
    entries: list[IndexEntry] = []
    expected_offset = 0
    for index in range(record_count):
        position = _HEADER_SIZE + index * RECORD_STRUCT.size
        byte_offset, byte_length, status = RECORD_STRUCT.unpack_from(data, position)
        if byte_length == 0 or status not in _VALID_STATUSES:
            return None
        if byte_offset != expected_offset:
            return None
        expected_offset = byte_offset + byte_length
        entries.append(IndexEntry(index + 1, byte_offset, byte_length, status))
    if expected_offset != source_size:
        return None
    return tuple(entries)


def load_index(source: Path) -> JsonlIndex | None:
    """Load a valid, current sidecar index, returning ``None`` for any bad input."""
    try:
        data = index_path(source).read_bytes()
        if len(data) < HEADER_STRUCT.size:
            return None
        magic, version, header_size, source_size, source_mtime_ns, record_count = (
            HEADER_STRUCT.unpack_from(data)
        )
        if magic != _MAGIC or version != _FORMAT_VERSION or header_size != _HEADER_SIZE:
            return None
        if len(data) != _HEADER_SIZE + RECORD_STRUCT.size * record_count:
            return None
        source_stat = source.stat()
        if source_size != source_stat.st_size or source_mtime_ns != source_stat.st_mtime_ns:
            return None
        entries = _parse_entries(data, record_count, source_size)
        if entries is None:
            return None
        return JsonlIndex(source, source_size, source_mtime_ns, entries)
    except (OSError, OverflowError, struct.error, ValueError):
        return None
