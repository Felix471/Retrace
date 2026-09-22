# Retrace JSONL index format

The index is a binary file. All integer fields are little-endian. Writers must
encode integers byte by byte with explicit shifts. They must not write native
structures or depend on host byte order or structure packing.

## Header

The header is exactly 40 bytes.

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 4 | Magic, ASCII `RIDX` |
| 4 | 2 | Unsigned format version, currently 2 |
| 6 | 2 | Unsigned header size, currently 40 |
| 8 | 8 | Unsigned source file size in bytes |
| 16 | 8 | Signed source modification time in nanoseconds since the Unix epoch |
| 24 | 8 | Unsigned physical line count |
| 32 | 4 | Unsigned flags; bit 0 indicates validated mode |
| 36 | 4 | Reserved, must be zero |

## Records

Exactly one 13-byte record follows the header for each physical line, in source
file order.

| Record offset | Size | Field |
| ---: | ---: | --- |
| 0 | 8 | Unsigned byte offset of the first byte of the line |
| 8 | 4 | Unsigned byte length of the line |
| 12 | 1 | Status code |

Status codes are:

| Code | Meaning |
| ---: | --- |
| 0 | Object by first non-JSON-whitespace byte |
| 1 | Blank under the ASCII whitespace rule |
| 2 | Invalid UTF-8 |
| 3 | Invalid JSON |
| 4 | Non-object by first non-JSON-whitespace byte |

## Physical lines and classification

Physical lines are split at byte `0A`. The terminating `0A` byte and any `0D`
byte before it belong to the line and are included in its byte length. A final
segment without `0A` is a line if and only if it is non-empty. An empty file has
no records. A file containing only `0A` has one blank record of length 1. A file
ending in `0A` does not gain an additional empty record.

For the first line only, an initial UTF-8 BOM with bytes `EF BB BF` is removed
for classification. It remains part of the physical line, so the first record
has offset 0 and its byte length includes the three BOM bytes.

Blank detection treats space, tab, line feed, carriage return, form feed, and
vertical tab as whitespace. JSON whitespace is space, tab, line feed, and
carriage return.

When flags bit 0 is clear, the index is in fast mode. Fast mode does not
validate UTF-8 or JSON. A non-blank line has status 0 when its first byte after
JSON whitespace is `{`, and status 4 otherwise. Status 0 in a fast index means
only "object by first byte"; the line may still fail to parse. Statuses 2 and 3
never appear in a fast index.

When flags bit 0 is set, the index is validated. Classification validates UTF-8
strictly and then validates non-blank lines as JSON. After validation, the same
first-byte rule distinguishes objects from non-objects. Status 0 in a validated
index means a validated JSON object. Statuses 2 and 3 appear only in validated
indexes. All flag bits other than bit 0 must be zero.

## Source modification time

`source_mtime_ns` is the operating system source modification time converted to
nanoseconds since the Unix epoch. On POSIX it is
`st_mtim.tv_sec * 1000000000 + st_mtim.tv_nsec`. On Windows it is the last-write
FILETIME converted from 100-nanosecond intervals since 1601 by subtracting
116444736000000000 and multiplying by 100. It must equal Python
`os.stat(path).st_mtime_ns` bit for bit.

## Staleness and validation

A reader must treat the index as absent if the stored source size or source
modification time differs from the current source file. It must also treat the
index as absent if its length is not exactly
`40 + 13 * record_count`, if record offsets are not monotonic or lie outside the
source size, if record byte ranges extend outside the source size, or if any
status is outside the range 0 through 4. Unknown flag bits or a nonzero reserved
field also make the index invalid.
