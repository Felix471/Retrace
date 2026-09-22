#ifndef RETRACE_JSONL_INDEX_INDEX_FORMAT_HPP
#define RETRACE_JSONL_INDEX_INDEX_FORMAT_HPP

#include <array>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace retrace::jsonl_index {

inline constexpr std::array<char, 4> kMagic{{'R', 'I', 'D', 'X'}};
inline constexpr std::uint16_t kFormatVersion = 2U;
inline constexpr std::uint16_t kHeaderSize = 40U;
inline constexpr std::uint64_t kRecordSize = 13U;
inline constexpr std::uint32_t kFlagValidated = 1U;
inline constexpr std::uint32_t kKnownFlags = kFlagValidated;

enum class LineStatus : std::uint8_t {
    ok_object = 0U,
    blank = 1U,
    invalid_utf8 = 2U,
    invalid_json = 3U,
    not_object = 4U,
};

struct Record {
    std::uint64_t byte_offset = 0U;
    std::uint32_t byte_length = 0U;
    std::uint8_t status = 0U;
};

struct Header {
    std::uint16_t format_version = kFormatVersion;
    std::uint16_t header_size = kHeaderSize;
    std::uint64_t source_size = 0U;
    std::int64_t source_mtime_ns = 0;
    std::uint64_t record_count = 0U;
    std::uint32_t flags = 0U;
};

struct IndexResult {
    Header header;
    std::vector<Record> records;
};

std::optional<IndexResult> build_index(
    const std::filesystem::path& source,
    std::string& error,
    bool validate = false);

bool write_index(
    const std::filesystem::path& out,
    const IndexResult& index,
    std::string& error);

std::optional<IndexResult> read_index(
    const std::filesystem::path& in,
    std::string& error);

bool source_mtime_ns(const std::filesystem::path& p, std::int64_t& out);

}  // namespace retrace::jsonl_index

#endif
