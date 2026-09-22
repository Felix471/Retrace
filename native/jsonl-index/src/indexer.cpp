#ifdef _WIN32
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS
#endif
#endif

#include "index_format.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <exception>
#include <fstream>
#include <limits>
#include <string>
#include <system_error>
#include <vector>

#include <nlohmann/json.hpp>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace retrace::jsonl_index {
namespace {

constexpr std::uint8_t status_value(LineStatus status) {
    return static_cast<std::uint8_t>(status);
}

#ifndef _WIN32
class PosixMappedFile {
public:
    PosixMappedFile() = default;
    PosixMappedFile(const PosixMappedFile&) = delete;
    PosixMappedFile& operator=(const PosixMappedFile&) = delete;

    ~PosixMappedFile() {
        if (data_ != nullptr) {
            static_cast<void>(
                ::munmap(const_cast<std::uint8_t*>(data_), size_));
        }
        if (descriptor_ >= 0) {
            static_cast<void>(::close(descriptor_));
        }
    }

    bool open(const std::filesystem::path& path) {
        descriptor_ = ::open(path.c_str(), O_RDONLY);
        if (descriptor_ < 0) {
            return false;
        }

        struct stat metadata {};
        if (::fstat(descriptor_, &metadata) != 0 || metadata.st_size < 0) {
            static_cast<void>(::close(descriptor_));
            descriptor_ = -1;
            return false;
        }

        const std::uintmax_t file_size =
            static_cast<std::uintmax_t>(metadata.st_size);
        if (file_size > static_cast<std::uintmax_t>(
                            std::numeric_limits<std::size_t>::max())) {
            static_cast<void>(::close(descriptor_));
            descriptor_ = -1;
            return false;
        }
        size_ = static_cast<std::size_t>(file_size);
        if (size_ == 0U) {
            return true;
        }

        void* const mapping =
            ::mmap(nullptr, size_, PROT_READ, MAP_PRIVATE, descriptor_, 0);
        if (mapping == MAP_FAILED) {
            size_ = 0U;
            static_cast<void>(::close(descriptor_));
            descriptor_ = -1;
            return false;
        }
        data_ = static_cast<const std::uint8_t*>(mapping);
        return true;
    }

    const std::uint8_t* data() const {
        return data_;
    }

    std::size_t size() const {
        return size_;
    }

private:
    int descriptor_ = -1;
    const std::uint8_t* data_ = nullptr;
    std::size_t size_ = 0U;
};
#endif

bool is_ascii_whitespace(std::uint8_t byte) {
    return byte == static_cast<std::uint8_t>(' ') ||
           byte == static_cast<std::uint8_t>('\t') ||
           byte == static_cast<std::uint8_t>('\n') ||
           byte == static_cast<std::uint8_t>('\r') ||
           byte == static_cast<std::uint8_t>('\f') ||
           byte == static_cast<std::uint8_t>('\v');
}

bool is_json_whitespace(std::uint8_t byte) {
    return byte == static_cast<std::uint8_t>(' ') ||
           byte == static_cast<std::uint8_t>('\t') ||
           byte == static_cast<std::uint8_t>('\n') ||
           byte == static_cast<std::uint8_t>('\r');
}

bool is_valid_utf8(const std::uint8_t* begin, const std::uint8_t* end) {
    const std::uint8_t* current = begin;
    while (current != end) {
        const std::uint8_t first = *current;
        if (first <= 0x7FU) {
            ++current;
            continue;
        }

        std::size_t continuation_count = 0U;
        std::uint32_t code_point = 0U;
        std::uint32_t minimum = 0U;
        if (first >= 0xC2U && first <= 0xDFU) {
            continuation_count = 1U;
            code_point = static_cast<std::uint32_t>(first & 0x1FU);
            minimum = 0x80U;
        } else if (first >= 0xE0U && first <= 0xEFU) {
            continuation_count = 2U;
            code_point = static_cast<std::uint32_t>(first & 0x0FU);
            minimum = 0x800U;
        } else if (first >= 0xF0U && first <= 0xF4U) {
            continuation_count = 3U;
            code_point = static_cast<std::uint32_t>(first & 0x07U);
            minimum = 0x10000U;
        } else {
            return false;
        }

        if (static_cast<std::size_t>(end - current) <= continuation_count) {
            return false;
        }

        for (std::size_t index = 1U; index <= continuation_count; ++index) {
            const std::uint8_t continuation = current[index];
            if ((continuation & 0xC0U) != 0x80U) {
                return false;
            }
            code_point = (code_point << 6U) |
                         static_cast<std::uint32_t>(continuation & 0x3FU);
        }

        if (code_point < minimum || code_point > 0x10FFFFU ||
            (code_point >= 0xD800U && code_point <= 0xDFFFU)) {
            return false;
        }
        current += static_cast<std::ptrdiff_t>(continuation_count + 1U);
    }
    return true;
}

LineStatus classify_line_fast(
    const std::uint8_t* begin,
    const std::uint8_t* end,
    bool first_line) {
    const bool has_bom = end - begin >= 3 && begin[0] == 0xEFU &&
                         begin[1] == 0xBBU && begin[2] == 0xBFU;
    if (first_line && has_bom) {
        begin += 3;
    }

    if (std::all_of(begin, end, is_ascii_whitespace)) {
        return LineStatus::blank;
    }

    const auto first = std::find_if(
        begin, end, [](std::uint8_t byte) { return !is_json_whitespace(byte); });
    return first != end && *first == static_cast<std::uint8_t>('{')
               ? LineStatus::ok_object
               : LineStatus::not_object;
}

LineStatus classify_line_validated(
    const std::uint8_t* begin,
    const std::uint8_t* end,
    bool first_line) {
    const bool has_bom = end - begin >= 3 && begin[0] == 0xEFU &&
                         begin[1] == 0xBBU && begin[2] == 0xBFU;
    if (first_line && has_bom) {
        begin += 3;
    }

    if (!is_valid_utf8(begin, end)) {
        return LineStatus::invalid_utf8;
    }

    // nlohmann/json accepts a BOM at the start of any parse buffer. Python's
    // utf-8 decoder retains it after line one, and json.loads then rejects it.
    if (!first_line && has_bom) {
        return LineStatus::invalid_json;
    }

    if (std::all_of(begin, end, is_ascii_whitespace)) {
        return LineStatus::blank;
    }

    try {
        if (!nlohmann::json::accept(begin, end)) {
            return LineStatus::invalid_json;
        }
        const auto first = std::find_if(
            begin, end, [](std::uint8_t byte) { return !is_json_whitespace(byte); });
        return first != end && *first == static_cast<std::uint8_t>('{')
                   ? LineStatus::ok_object
                   : LineStatus::not_object;
    } catch (const nlohmann::json::exception&) {
        return LineStatus::invalid_json;
    }
}

void write_u8(
    std::vector<char>& buffer,
    std::size_t& position,
    std::uint8_t value) {
    buffer[position] = static_cast<char>(value);
    ++position;
}

void write_u16(
    std::vector<char>& buffer,
    std::size_t& position,
    std::uint16_t value) {
    for (unsigned int shift = 0U; shift < 16U; shift += 8U) {
        write_u8(
            buffer,
            position,
            static_cast<std::uint8_t>((value >> shift) & 0xFFU));
    }
}

void write_u32(
    std::vector<char>& buffer,
    std::size_t& position,
    std::uint32_t value) {
    for (unsigned int shift = 0U; shift < 32U; shift += 8U) {
        write_u8(
            buffer,
            position,
            static_cast<std::uint8_t>((value >> shift) & 0xFFU));
    }
}

void write_u64(
    std::vector<char>& buffer,
    std::size_t& position,
    std::uint64_t value) {
    for (unsigned int shift = 0U; shift < 64U; shift += 8U) {
        write_u8(
            buffer,
            position,
            static_cast<std::uint8_t>((value >> shift) & 0xFFU));
    }
}

void write_i64(
    std::vector<char>& buffer,
    std::size_t& position,
    std::int64_t value) {
    write_u64(buffer, position, static_cast<std::uint64_t>(value));
}

bool read_exact(std::istream& stream, char* destination, std::size_t size) {
    stream.read(destination, static_cast<std::streamsize>(size));
    return stream.gcount() == static_cast<std::streamsize>(size);
}

bool read_u8(std::istream& stream, std::uint8_t& value) {
    char byte = 0;
    if (!read_exact(stream, &byte, 1U)) {
        return false;
    }
    value = static_cast<std::uint8_t>(static_cast<unsigned char>(byte));
    return true;
}

bool read_u16(std::istream& stream, std::uint16_t& value) {
    value = 0U;
    for (unsigned int shift = 0U; shift < 16U; shift += 8U) {
        std::uint8_t byte = 0U;
        if (!read_u8(stream, byte)) {
            return false;
        }
        value |= static_cast<std::uint16_t>(
            static_cast<std::uint16_t>(byte) << shift);
    }
    return true;
}

bool read_u32(std::istream& stream, std::uint32_t& value) {
    value = 0U;
    for (unsigned int shift = 0U; shift < 32U; shift += 8U) {
        std::uint8_t byte = 0U;
        if (!read_u8(stream, byte)) {
            return false;
        }
        value |= static_cast<std::uint32_t>(byte) << shift;
    }
    return true;
}

bool read_u64(std::istream& stream, std::uint64_t& value) {
    value = 0U;
    for (unsigned int shift = 0U; shift < 64U; shift += 8U) {
        std::uint8_t byte = 0U;
        if (!read_u8(stream, byte)) {
            return false;
        }
        value |= static_cast<std::uint64_t>(byte) << shift;
    }
    return true;
}

bool unsigned_bits_to_i64(std::uint64_t bits, std::int64_t& value) {
    const std::uint64_t max_positive =
        static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max());
    if (bits <= max_positive) {
        value = static_cast<std::int64_t>(bits);
        return true;
    }

    const std::uint64_t magnitude = (~bits) + 1U;
    const std::uint64_t min_magnitude = std::uint64_t{1U} << 63U;
    if (magnitude == min_magnitude) {
        value = std::numeric_limits<std::int64_t>::min();
    } else {
        value = -static_cast<std::int64_t>(magnitude);
    }
    return true;
}

bool read_i64(std::istream& stream, std::int64_t& value) {
    std::uint64_t bits = 0U;
    return read_u64(stream, bits) && unsigned_bits_to_i64(bits, value);
}

std::string path_for_error(const std::filesystem::path& path) {
    try {
        return path.u8string();
    } catch (...) {
        return "<path>";
    }
}

std::optional<IndexResult> build_index_impl(
    const std::filesystem::path& source,
    std::string& error,
    bool validate) {
    std::error_code filesystem_error;
    if (!std::filesystem::is_regular_file(source, filesystem_error)) {
        error = "input is not a readable regular file: " + path_for_error(source);
        return std::nullopt;
    }

    const std::uintmax_t metadata_size =
        std::filesystem::file_size(source, filesystem_error);
    if (filesystem_error) {
        error = "cannot determine input size: " + path_for_error(source);
        return std::nullopt;
    }
    if (metadata_size > static_cast<std::uintmax_t>(
                            std::numeric_limits<std::size_t>::max())) {
        error = "input is too large to read: " + path_for_error(source);
        return std::nullopt;
    }

    std::int64_t modification_time = 0;
    if (!source_mtime_ns(source, modification_time)) {
        error = "cannot determine input modification time: " +
                path_for_error(source);
        return std::nullopt;
    }

    std::vector<std::uint8_t> bytes;
    const std::uint8_t* data = nullptr;
    std::size_t data_size = 0U;

#ifndef _WIN32
    PosixMappedFile mapping;
    const bool mapped = mapping.open(source);
    if (mapped) {
        data = mapping.data();
        data_size = mapping.size();
    } else
#endif
    {
        if (metadata_size > static_cast<std::uintmax_t>(
                                std::numeric_limits<std::streamsize>::max())) {
            error = "input is too large to read: " + path_for_error(source);
            return std::nullopt;
        }

        std::ifstream stream(source, std::ios::binary);
        if (!stream.is_open()) {
            error = "cannot open input: " + path_for_error(source);
            return std::nullopt;
        }

        bytes.resize(static_cast<std::size_t>(metadata_size));
        if (!bytes.empty()) {
            stream.read(reinterpret_cast<char*>(bytes.data()),
                        static_cast<std::streamsize>(bytes.size()));
            if (stream.gcount() != static_cast<std::streamsize>(bytes.size()) ||
                stream.bad()) {
                error = "failed while reading input: " + path_for_error(source);
                return std::nullopt;
            }
        }
        data = bytes.data();
        data_size = bytes.size();
    }

    IndexResult result;
    result.header.source_size = static_cast<std::uint64_t>(data_size);
    result.header.source_mtime_ns = modification_time;
    result.header.flags = validate ? kFlagValidated : 0U;

    std::size_t line_start = 0U;
    while (line_start < data_size) {
        const void* newline_raw = std::memchr(
            data + line_start,
            static_cast<int>('\n'),
            data_size - line_start);
        const auto* newline = static_cast<const std::uint8_t*>(newline_raw);
        const std::size_t line_end =
            newline == nullptr
                ? data_size
                : static_cast<std::size_t>(newline - data) + 1U;
        const std::uint64_t line_length =
            static_cast<std::uint64_t>(line_end - line_start);
        if (line_length > std::numeric_limits<std::uint32_t>::max()) {
            error = "input contains a line longer than UINT32_MAX bytes: " +
                    path_for_error(source);
            return std::nullopt;
        }

        const std::uint8_t* begin = data + line_start;
        const std::uint8_t* end = data + line_end;
        const LineStatus status = validate
                                      ? classify_line_validated(
                                            begin, end, result.records.empty())
                                      : classify_line_fast(
                                            begin, end, result.records.empty());
        result.records.push_back(Record{
            static_cast<std::uint64_t>(line_start),
            static_cast<std::uint32_t>(line_length),
            status_value(status),
        });
        line_start = line_end;
    }

    result.header.record_count =
        static_cast<std::uint64_t>(result.records.size());
    return result;
}

bool write_index_impl(
    const std::filesystem::path& out,
    const IndexResult& index,
    std::string& error) {
    if (index.header.record_count !=
            static_cast<std::uint64_t>(index.records.size())) {
        error = "index record count does not match its records";
        return false;
    }
    if ((index.header.flags & ~kKnownFlags) != 0U) {
        error = "index contains unknown header flags";
        return false;
    }

    constexpr std::size_t header_size =
        static_cast<std::size_t>(kHeaderSize);
    constexpr std::size_t record_size =
        static_cast<std::size_t>(kRecordSize);
    if (index.records.size() >
        (std::numeric_limits<std::size_t>::max() - header_size) /
            record_size) {
        error = "index is too large to serialize";
        return false;
    }

    const std::size_t output_size =
        header_size + record_size * index.records.size();
    std::vector<char> buffer(output_size);
    std::size_t position = 0U;

    for (const char byte : kMagic) {
        write_u8(
            buffer,
            position,
            static_cast<std::uint8_t>(static_cast<unsigned char>(byte)));
    }
    write_u16(buffer, position, kFormatVersion);
    write_u16(buffer, position, kHeaderSize);
    write_u64(buffer, position, index.header.source_size);
    write_i64(buffer, position, index.header.source_mtime_ns);
    write_u64(buffer, position, index.header.record_count);
    write_u32(buffer, position, index.header.flags);
    write_u32(buffer, position, 0U);

    for (const Record& record : index.records) {
        write_u64(buffer, position, record.byte_offset);
        write_u32(buffer, position, record.byte_length);
        write_u8(buffer, position, record.status);
    }

#ifdef _WIN32
    std::FILE* const stream = ::_wfopen(out.c_str(), L"wb");
#else
    std::FILE* const stream = std::fopen(out.c_str(), "wb");
#endif
    if (stream == nullptr) {
        error = "cannot open output: " + path_for_error(out);
        return false;
    }

    const bool write_succeeded =
        std::fwrite(buffer.data(), 1U, buffer.size(), stream) == buffer.size();
    const bool flush_succeeded = std::fflush(stream) == 0;
    const bool close_succeeded = std::fclose(stream) == 0;
    if (!write_succeeded || !flush_succeeded) {
        error = "failed while writing output: " + path_for_error(out);
        return false;
    }
    if (!close_succeeded) {
        error = "failed while closing output: " + path_for_error(out);
        return false;
    }
    return true;
}

std::optional<IndexResult> read_index_impl(
    const std::filesystem::path& in,
    std::string& error) {
    std::error_code filesystem_error;
    const std::uintmax_t actual_size =
        std::filesystem::file_size(in, filesystem_error);
    if (filesystem_error) {
        error = "cannot determine index size: " + path_for_error(in);
        return std::nullopt;
    }

    std::ifstream stream(in, std::ios::binary);
    if (!stream.is_open()) {
        error = "cannot open index: " + path_for_error(in);
        return std::nullopt;
    }

    std::array<char, 4> magic{};
    IndexResult result;
    std::uint32_t reserved = 0U;
    if (!read_exact(stream, magic.data(), magic.size()) ||
        !read_u16(stream, result.header.format_version) ||
        !read_u16(stream, result.header.header_size) ||
        !read_u64(stream, result.header.source_size) ||
        !read_i64(stream, result.header.source_mtime_ns) ||
        !read_u64(stream, result.header.record_count) ||
        !read_u32(stream, result.header.flags) ||
        !read_u32(stream, reserved)) {
        error = "index has a truncated header: " + path_for_error(in);
        return std::nullopt;
    }

    if (magic != kMagic || result.header.format_version != kFormatVersion ||
        result.header.header_size != kHeaderSize) {
        error = "index has an unsupported header: " + path_for_error(in);
        return std::nullopt;
    }
    if ((result.header.flags & ~kKnownFlags) != 0U || reserved != 0U) {
        error = "index has unsupported flags or reserved fields: " +
                path_for_error(in);
        return std::nullopt;
    }

    const std::uint64_t count = result.header.record_count;
    if (count >
        (std::numeric_limits<std::uint64_t>::max() - kHeaderSize) /
            kRecordSize) {
        error = "index record count overflows its file length";
        return std::nullopt;
    }
    const std::uint64_t expected_size = kHeaderSize + count * kRecordSize;
    if (actual_size != static_cast<std::uintmax_t>(expected_size)) {
        error = "index file length does not match its record count: " +
                path_for_error(in);
        return std::nullopt;
    }
    if (count > static_cast<std::uint64_t>(
                    std::numeric_limits<std::size_t>::max())) {
        error = "index has too many records to load";
        return std::nullopt;
    }

    result.records.reserve(static_cast<std::size_t>(count));
    std::uint64_t previous_offset = 0U;
    for (std::uint64_t index = 0U; index < count; ++index) {
        Record record;
        if (!read_u64(stream, record.byte_offset) ||
            !read_u32(stream, record.byte_length) ||
            !read_u8(stream, record.status)) {
            error = "index has a truncated record: " + path_for_error(in);
            return std::nullopt;
        }

        const bool offset_not_monotonic =
            index != 0U && record.byte_offset < previous_offset;
        const bool offset_outside =
            record.byte_offset >= result.header.source_size;
        const bool range_outside =
            static_cast<std::uint64_t>(record.byte_length) >
            result.header.source_size -
                std::min(record.byte_offset, result.header.source_size);
        if (offset_not_monotonic || offset_outside || range_outside ||
            record.status > status_value(LineStatus::not_object)) {
            error = "index contains an invalid record: " + path_for_error(in);
            return std::nullopt;
        }
        previous_offset = record.byte_offset;
        result.records.push_back(record);
    }

    return result;
}

}  // namespace

bool source_mtime_ns(const std::filesystem::path& p, std::int64_t& out) {
    try {
#ifdef _WIN32
        WIN32_FILE_ATTRIBUTE_DATA attributes{};
        if (GetFileAttributesExW(
                p.c_str(), GetFileExInfoStandard, &attributes) == 0) {
            return false;
        }

        const std::uint64_t filetime =
            (static_cast<std::uint64_t>(
                 attributes.ftLastWriteTime.dwHighDateTime)
             << 32U) |
            static_cast<std::uint64_t>(
                attributes.ftLastWriteTime.dwLowDateTime);
        constexpr std::uint64_t epoch_ticks = 116444736000000000ULL;
        constexpr std::uint64_t scale = 100U;
        const std::uint64_t max_positive =
            static_cast<std::uint64_t>(
                std::numeric_limits<std::int64_t>::max());

        if (filetime >= epoch_ticks) {
            const std::uint64_t ticks = filetime - epoch_ticks;
            if (ticks > max_positive / scale) {
                return false;
            }
            out = static_cast<std::int64_t>(ticks * scale);
        } else {
            const std::uint64_t ticks = epoch_ticks - filetime;
            const std::uint64_t min_magnitude = std::uint64_t{1U} << 63U;
            if (ticks > min_magnitude / scale) {
                return false;
            }
            const std::uint64_t nanoseconds = ticks * scale;
            if (nanoseconds == min_magnitude) {
                out = std::numeric_limits<std::int64_t>::min();
            } else {
                out = -static_cast<std::int64_t>(nanoseconds);
            }
        }
        return true;
#else
        struct stat metadata {};
        if (::stat(p.c_str(), &metadata) != 0) {
            return false;
        }

        const std::int64_t seconds =
            static_cast<std::int64_t>(metadata.st_mtim.tv_sec);
        constexpr std::int64_t billion = 1000000000LL;
        if (seconds > std::numeric_limits<std::int64_t>::max() / billion ||
            seconds < std::numeric_limits<std::int64_t>::min() / billion) {
            return false;
        }
        const std::int64_t base = seconds * billion;
        const std::int64_t remainder =
            static_cast<std::int64_t>(metadata.st_mtim.tv_nsec);
        if (remainder > 0 &&
            base > std::numeric_limits<std::int64_t>::max() - remainder) {
            return false;
        }
        out = base + remainder;
        return true;
#endif
    } catch (...) {
        return false;
    }
}

std::optional<IndexResult> build_index(
    const std::filesystem::path& source,
    std::string& error,
    bool validate) {
    error.clear();
    try {
        return build_index_impl(source, error, validate);
    } catch (const std::exception& exception) {
        error = "cannot index input: " + std::string(exception.what());
    } catch (...) {
        error = "cannot index input: unknown error";
    }
    return std::nullopt;
}

bool write_index(
    const std::filesystem::path& out,
    const IndexResult& index,
    std::string& error) {
    error.clear();
    try {
        return write_index_impl(out, index, error);
    } catch (const std::exception& exception) {
        error = "cannot write index: " + std::string(exception.what());
    } catch (...) {
        error = "cannot write index: unknown error";
    }
    return false;
}

std::optional<IndexResult> read_index(
    const std::filesystem::path& in,
    std::string& error) {
    error.clear();
    try {
        return read_index_impl(in, error);
    } catch (const std::exception& exception) {
        error = "cannot read index: " + std::string(exception.what());
    } catch (...) {
        error = "cannot read index: unknown error";
    }
    return std::nullopt;
}

}  // namespace retrace::jsonl_index
