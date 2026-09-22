# Retrace JSONL index format

The index is a binary file. All integer fields are little-endian. Writers must
encode integers byte by byte with explicit shifts. They must not write native
structures or depend on host byte order or structure packing.

## Header

The header is exactly 32 bytes.

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 4 | Magic, ASCII `RIDX` |
| 4 | 2 | Unsigned format version, currently 1 |
| 6 | 2 | Unsigned header size, currently 32 |
| 8 | 8 | Unsigned source file size in bytes |
| 16 | 8 | Signed source modification time in nanoseconds since the Unix epoch |
| 24 | 8 | Unsigned physical line count |

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
| 0 | Valid JSON whose top-level value is an object |
| 1 | Blank after UTF-8 decoding and whitespace stripping |
| 2 | Invalid UTF-8 |
| 3 | Invalid JSON |
| 4 | Valid JSON whose top-level value is not an object |

## Physical lines and classification

Physical lines are split at byte `0A`. The terminating `0A` byte and any `0D`
byte before it belong to the line and are included in its byte length. A final
segment without `0A` is a line if and only if it is non-empty. An empty file has
no records. A file containing only `0A` has one blank record of length 1. A file
ending in `0A` does not gain an additional empty record.

For the first line only, an initial UTF-8 BOM with bytes `EF BB BF` is removed
for classification. It remains part of the physical line, so the first record
has offset 0 and its byte length includes the three BOM bytes.

Classification first validates UTF-8 strictly. Blank detection treats space,
tab, line feed, carriage return, form feed, and vertical tab as whitespace.
Non-blank valid UTF-8 is parsed as JSON and then classified by whether its
top-level value is an object.

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
`32 + 13 * record_count`, if record offsets are not monotonic or lie outside the
source size, if record byte ranges extend outside the source size, or if any
status is outside the range 0 through 4.
