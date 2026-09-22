#include "index_format.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <string>
#include <system_error>
#include <vector>

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

namespace {

namespace index = retrace::jsonl_index;

std::uint8_t status(index::LineStatus value) {
    return static_cast<std::uint8_t>(value);
}

class TemporaryDirectory {
public:
    TemporaryDirectory() {
        static std::atomic<std::uint64_t> sequence{0U};
        const auto timestamp = std::chrono::high_resolution_clock::now()
                                   .time_since_epoch()
                                   .count();
        path_ = std::filesystem::temp_directory_path() /
                ("retrace-jsonl-index-test-" + std::to_string(timestamp) +
                 "-" + std::to_string(sequence.fetch_add(1U)));
        std::filesystem::create_directories(path_);
    }

    TemporaryDirectory(const TemporaryDirectory&) = delete;
    TemporaryDirectory& operator=(const TemporaryDirectory&) = delete;

    ~TemporaryDirectory() {
        std::error_code error;
        std::filesystem::remove_all(path_, error);
    }

    const std::filesystem::path& path() const {
        return path_;
    }

private:
    std::filesystem::path path_;
};

void write_bytes(
    const std::filesystem::path& path,
    const std::vector<std::uint8_t>& bytes) {
    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    ASSERT_TRUE(stream.is_open());
    if (!bytes.empty()) {
        stream.write(reinterpret_cast<const char*>(bytes.data()),
                     static_cast<std::streamsize>(bytes.size()));
    }
    stream.close();
    ASSERT_TRUE(stream);
}

void write_text(const std::filesystem::path& path, const std::string& text) {
    write_bytes(path, std::vector<std::uint8_t>(text.begin(), text.end()));
}

std::vector<std::uint8_t> read_bytes(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    EXPECT_TRUE(stream.is_open());
    return std::vector<std::uint8_t>(
        std::istreambuf_iterator<char>(stream),
        std::istreambuf_iterator<char>());
}

index::IndexResult require_build(
    const std::filesystem::path& source,
    bool validate) {
    std::string error;
    const auto result = index::build_index(source, error, validate);
    EXPECT_TRUE(result.has_value()) << error;
    return result.value_or(index::IndexResult{});
}

TEST(JsonlIndexerTest, BomOnFirstLineIsClassifiedButStillCounted) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "bom.jsonl";
    const std::vector<std::uint8_t> bytes{
        0xEFU, 0xBBU, 0xBFU, '{', '"', 'a', '"', ':', '1', '}', '\n'};
    write_bytes(source, bytes);

    const auto result = require_build(source, true);

    ASSERT_EQ(result.records.size(), 1U);
    EXPECT_EQ(result.records[0].byte_offset, 0U);
    EXPECT_EQ(result.records[0].byte_length, bytes.size());
    EXPECT_EQ(result.records[0].status, status(index::LineStatus::ok_object));
}

TEST(JsonlIndexerTest, BomAfterFirstLineIsInvalidJson) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "later-bom.jsonl";
    const std::vector<std::uint8_t> bytes{
        '{', '}', '\n',
        0xEFU, 0xBBU, 0xBFU, '{', '}', '\n'};
    write_bytes(source, bytes);

    const auto result = require_build(source, true);

    ASSERT_EQ(result.records.size(), 2U);
    EXPECT_EQ(result.records[0].status, status(index::LineStatus::ok_object));
    EXPECT_EQ(result.records[1].status, status(index::LineStatus::invalid_json));
}

TEST(JsonlIndexerTest, BlankLinesHaveCorrectLengths) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "blank.jsonl";
    write_text(source, "   \r\n\n");

    const auto result = require_build(source, true);

    ASSERT_EQ(result.records.size(), 2U);
    EXPECT_EQ(result.records[0].byte_offset, 0U);
    EXPECT_EQ(result.records[0].byte_length, 5U);
    EXPECT_EQ(result.records[0].status, status(index::LineStatus::blank));
    EXPECT_EQ(result.records[1].byte_offset, 5U);
    EXPECT_EQ(result.records[1].byte_length, 1U);
    EXPECT_EQ(result.records[1].status, status(index::LineStatus::blank));
}

TEST(JsonlIndexerTest, InvalidUtf8HasDedicatedStatus) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "invalid-utf8.jsonl";
    write_bytes(source, std::vector<std::uint8_t>{'{', 0xFFU, 0xFEU, '}', '\n'});

    const auto result = require_build(source, true);

    ASSERT_EQ(result.records.size(), 1U);
    EXPECT_EQ(result.records[0].status, status(index::LineStatus::invalid_utf8));
}

TEST(JsonlIndexerTest, StrictUtf8RejectsMalformedSequences) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "malformed-utf8.jsonl";
    const std::vector<std::uint8_t> bytes{
        0xC0U, 0xAFU, '\n',
        0xEDU, 0xA0U, 0x80U, '\n',
        0xF4U, 0x90U, 0x80U, 0x80U, '\n',
        0xE2U, 0x82U, '\n'};
    write_bytes(source, bytes);

    const auto result = require_build(source, true);

    ASSERT_EQ(result.records.size(), 4U);
    for (const auto& record : result.records) {
        EXPECT_EQ(record.status, status(index::LineStatus::invalid_utf8));
    }
}

TEST(JsonlIndexerTest, InvalidJsonHasDedicatedStatus) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "invalid-json.jsonl";
    write_text(source, "{missing}\n");

    const auto result = require_build(source, true);

    ASSERT_EQ(result.records.size(), 1U);
    EXPECT_EQ(result.records[0].status, status(index::LineStatus::invalid_json));
}

TEST(JsonlIndexerTest, FirstByteDecidesObjectStatusAfterAccept) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "first-byte.jsonl";
    write_text(source, "  {\"a\":1}\n\t[1]\n\"{\"\n{\n  \r\n{}\r\n");

    const auto result = require_build(source, true);

    const std::array<std::uint8_t, 6> expected{{
        status(index::LineStatus::ok_object),
        status(index::LineStatus::not_object),
        status(index::LineStatus::not_object),
        status(index::LineStatus::invalid_json),
        status(index::LineStatus::blank),
        status(index::LineStatus::ok_object),
    }};
    ASSERT_EQ(result.records.size(), expected.size());
    for (std::size_t record_index = 0U;
         record_index < expected.size();
         ++record_index) {
        EXPECT_EQ(result.records[record_index].status, expected[record_index]);
    }
}

TEST(JsonlIndexerTest, JsonNonObjectsHaveDedicatedStatus) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "values.jsonl";
    write_text(source, "[1,2]\n42\n\"s\"\n");

    const auto result = require_build(source, true);

    ASSERT_EQ(result.records.size(), 3U);
    for (const auto& record : result.records) {
        EXPECT_EQ(record.status, status(index::LineStatus::not_object));
    }
}

TEST(JsonlIndexerTest, CrlfBytesAreIncludedInObjectLines) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "crlf.jsonl";
    write_text(source, "{}\r\n{\"x\":true}\r\n");

    const auto result = require_build(source, true);

    ASSERT_EQ(result.records.size(), 2U);
    EXPECT_EQ(result.records[0].byte_length, 4U);
    EXPECT_EQ(result.records[1].byte_offset, 4U);
    EXPECT_EQ(result.records[1].byte_length, 12U);
    EXPECT_EQ(result.records[0].status, status(index::LineStatus::ok_object));
    EXPECT_EQ(result.records[1].status, status(index::LineStatus::ok_object));
}

TEST(JsonlIndexerTest, EmptyFileProducesHeaderOnlyIndex) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "empty.jsonl";
    const auto output = directory.path() / "empty.ridx";
    write_text(source, "");

    const auto result = require_build(source, true);
    EXPECT_TRUE(result.records.empty());
    EXPECT_EQ(result.header.record_count, 0U);

    std::string error;
    ASSERT_TRUE(index::write_index(output, result, error)) << error;
    EXPECT_EQ(std::filesystem::file_size(output), 40U);
}

TEST(JsonlIndexerTest, UnterminatedFinalLineCountsWithoutExtraTrailingRecord) {
    TemporaryDirectory directory;
    const auto unterminated = directory.path() / "unterminated.jsonl";
    const auto terminated = directory.path() / "terminated.jsonl";
    write_text(unterminated, "{}\n{\"x\":1}");
    write_text(terminated, "{}\n");

    const auto first = require_build(unterminated, true);
    const auto second = require_build(terminated, true);

    ASSERT_EQ(first.records.size(), 2U);
    EXPECT_EQ(first.records[0].byte_length, 3U);
    EXPECT_EQ(first.records[1].byte_offset, 3U);
    EXPECT_EQ(first.records[1].byte_length, 7U);
    EXPECT_EQ(first.records[1].status, status(index::LineStatus::ok_object));
    ASSERT_EQ(second.records.size(), 1U);
    EXPECT_EQ(second.records[0].byte_length, 3U);
}

TEST(JsonlIndexerTest, RoundTripPreservesEveryStatusAndObjectOffsets) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "all-statuses.jsonl";
    const auto output = directory.path() / "all-statuses.ridx";
    const std::vector<std::uint8_t> bytes{
        0xEFU, 0xBBU, 0xBFU, '{', '"', 'a', '"', ':', '1', '}', '\n',
        '\n',
        0xFFU, '\n',
        '{', 'b', 'a', 'd', '}', '\n',
        '[', '1', ']', '\n'};
    write_bytes(source, bytes);

    const auto built = require_build(source, true);
    std::string error;
    ASSERT_TRUE(index::write_index(output, built, error)) << error;
    const auto loaded_optional = index::read_index(output, error);
    ASSERT_TRUE(loaded_optional.has_value()) << error;
    const auto& loaded = *loaded_optional;

    EXPECT_EQ(loaded.header.format_version, built.header.format_version);
    EXPECT_EQ(loaded.header.header_size, built.header.header_size);
    EXPECT_EQ(loaded.header.source_size, built.header.source_size);
    EXPECT_EQ(loaded.header.source_mtime_ns, built.header.source_mtime_ns);
    EXPECT_EQ(loaded.header.record_count, built.header.record_count);
    EXPECT_EQ(loaded.header.flags, built.header.flags);
    ASSERT_EQ(loaded.records.size(), built.records.size());
    for (std::size_t record_index = 0U;
         record_index < loaded.records.size();
         ++record_index) {
        EXPECT_EQ(loaded.records[record_index].byte_offset,
                  built.records[record_index].byte_offset);
        EXPECT_EQ(loaded.records[record_index].byte_length,
                  built.records[record_index].byte_length);
        EXPECT_EQ(loaded.records[record_index].status,
                  built.records[record_index].status);
    }

    const std::array<std::uint8_t, 5> expected_statuses{{
        status(index::LineStatus::ok_object),
        status(index::LineStatus::blank),
        status(index::LineStatus::invalid_utf8),
        status(index::LineStatus::invalid_json),
        status(index::LineStatus::not_object),
    }};
    ASSERT_EQ(loaded.records.size(), expected_statuses.size());
    for (std::size_t record_index = 0U;
         record_index < expected_statuses.size();
         ++record_index) {
        EXPECT_EQ(loaded.records[record_index].status,
                  expected_statuses[record_index]);
    }

    std::ifstream stream(source, std::ios::binary);
    ASSERT_TRUE(stream.is_open());
    for (std::size_t record_index = 0U;
         record_index < loaded.records.size();
         ++record_index) {
        const auto& record = loaded.records[record_index];
        if (record.status != status(index::LineStatus::ok_object)) {
            continue;
        }
        stream.clear();
        stream.seekg(static_cast<std::streamoff>(record.byte_offset));
        ASSERT_TRUE(stream);
        std::vector<std::uint8_t> line(record.byte_length);
        stream.read(reinterpret_cast<char*>(line.data()),
                    static_cast<std::streamsize>(line.size()));
        ASSERT_EQ(stream.gcount(), static_cast<std::streamsize>(line.size()));

        const auto begin = line.begin() +
                           static_cast<std::vector<std::uint8_t>::difference_type>(
                               record_index == 0U ? 3U : 0U);
        const nlohmann::json from_record = nlohmann::json::parse(begin, line.end());
        const nlohmann::json from_original =
            nlohmann::json::parse("{\"a\":1}");
        EXPECT_EQ(from_record, from_original);
    }
}

TEST(JsonlIndexerTest, BuildingAndWritingDoNotTouchSource) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "source.jsonl";
    const auto output = directory.path() / "source.jsonl.ridx";
    write_text(source, "{\"stable\":true}\n");
    const auto bytes_before = read_bytes(source);
    std::int64_t mtime_before = 0;
    ASSERT_TRUE(index::source_mtime_ns(source, mtime_before));

    const auto result = require_build(source, true);
    std::string error;
    ASSERT_TRUE(index::write_index(output, result, error)) << error;

    std::int64_t mtime_after = 0;
    ASSERT_TRUE(index::source_mtime_ns(source, mtime_after));
    EXPECT_EQ(read_bytes(source), bytes_before);
    EXPECT_EQ(mtime_after, mtime_before);

    std::vector<std::filesystem::path> entries;
    for (const auto& entry : std::filesystem::directory_iterator(directory.path())) {
        entries.push_back(entry.path().filename());
    }
    std::sort(entries.begin(), entries.end());
    ASSERT_EQ(entries.size(), 2U);
    EXPECT_EQ(entries[0], source.filename());
    EXPECT_EQ(entries[1], output.filename());
}

TEST(JsonlIndexerTest, SerializationIsLittleEndian) {
    TemporaryDirectory directory;
    const auto output = directory.path() / "manual.ridx";
    index::IndexResult value;
    value.header.source_size = std::numeric_limits<std::uint64_t>::max();
    value.header.source_mtime_ns = 0;
    value.header.record_count = 1U;
    value.header.flags = index::kFlagValidated;
    value.records.push_back(index::Record{
        0x0102030405060708ULL,
        1U,
        status(index::LineStatus::ok_object),
    });

    std::string error;
    ASSERT_TRUE(index::write_index(output, value, error)) << error;
    const auto bytes = read_bytes(output);

    ASSERT_EQ(bytes.size(), 53U);
    EXPECT_EQ(std::string(bytes.begin(), bytes.begin() + 4), "RIDX");
    EXPECT_EQ(bytes[4], 0x02U);
    EXPECT_EQ(bytes[5], 0x00U);
    EXPECT_EQ(bytes[6], 0x28U);
    EXPECT_EQ(bytes[7], 0x00U);
    EXPECT_EQ(bytes[32], 0x01U);
    EXPECT_EQ(bytes[33], 0x00U);
    EXPECT_EQ(bytes[34], 0x00U);
    EXPECT_EQ(bytes[35], 0x00U);
    EXPECT_EQ(bytes[36], 0x00U);
    EXPECT_EQ(bytes[37], 0x00U);
    EXPECT_EQ(bytes[38], 0x00U);
    EXPECT_EQ(bytes[39], 0x00U);
    const std::array<std::uint8_t, 8> expected_offset{{
        0x08U, 0x07U, 0x06U, 0x05U, 0x04U, 0x03U, 0x02U, 0x01U,
    }};
    EXPECT_TRUE(std::equal(expected_offset.begin(),
                           expected_offset.end(),
                           bytes.begin() + 40));
}

TEST(JsonlIndexerTest, FastModeBlankLinesHaveCorrectLengths) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "fast-blank.jsonl";
    write_text(source, "   \r\n\n");

    const auto result = require_build(source, false);

    ASSERT_EQ(result.records.size(), 2U);
    EXPECT_EQ(result.header.flags, 0U);
    EXPECT_EQ(result.records[0].byte_offset, 0U);
    EXPECT_EQ(result.records[0].byte_length, 5U);
    EXPECT_EQ(result.records[0].status, status(index::LineStatus::blank));
    EXPECT_EQ(result.records[1].byte_offset, 5U);
    EXPECT_EQ(result.records[1].byte_length, 1U);
    EXPECT_EQ(result.records[1].status, status(index::LineStatus::blank));
}

TEST(JsonlIndexerTest, FastModeCrlfObjectsIncludeLineEndings) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "fast-crlf.jsonl";
    write_text(source, "{}\r\n{\"x\":true}\r\n");

    const auto result = require_build(source, false);

    ASSERT_EQ(result.records.size(), 2U);
    EXPECT_EQ(result.records[0].byte_length, 4U);
    EXPECT_EQ(result.records[1].byte_offset, 4U);
    EXPECT_EQ(result.records[1].byte_length, 12U);
    EXPECT_EQ(result.records[0].status, status(index::LineStatus::ok_object));
    EXPECT_EQ(result.records[1].status, status(index::LineStatus::ok_object));
}

TEST(JsonlIndexerTest, FastModeBomIsSkippedButCounted) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "fast-bom.jsonl";
    const std::vector<std::uint8_t> bytes{
        0xEFU, 0xBBU, 0xBFU, '{', '"', 'a', '"', ':', '1', '}', '\n'};
    write_bytes(source, bytes);

    const auto result = require_build(source, false);

    ASSERT_EQ(result.records.size(), 1U);
    EXPECT_EQ(result.records[0].byte_offset, 0U);
    EXPECT_EQ(result.records[0].byte_length, bytes.size());
    EXPECT_EQ(result.records[0].status, status(index::LineStatus::ok_object));
}

TEST(JsonlIndexerTest, FastModeNonObjectFirstBytesAreNotObjects) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "fast-non-objects.jsonl";
    write_text(source, "[1]\n\"{\"\n42\n");

    const auto result = require_build(source, false);

    ASSERT_EQ(result.records.size(), 3U);
    for (const auto& record : result.records) {
        EXPECT_EQ(record.status, status(index::LineStatus::not_object));
    }
}

TEST(JsonlIndexerTest, InvalidJsonDiffersBetweenFastAndValidatedModes) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "mode-invalid-json.jsonl";
    write_text(source, "{bad}\n");

    const auto fast = require_build(source, false);
    const auto validated = require_build(source, true);

    ASSERT_EQ(fast.records.size(), 1U);
    ASSERT_EQ(validated.records.size(), 1U);
    EXPECT_EQ(fast.records[0].status, status(index::LineStatus::ok_object));
    EXPECT_EQ(validated.records[0].status,
              status(index::LineStatus::invalid_json));
}

TEST(JsonlIndexerTest, InvalidUtf8DiffersBetweenFastAndValidatedModes) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "mode-invalid-utf8.jsonl";
    write_bytes(source, std::vector<std::uint8_t>{'{', 0xFFU, '}', '\n'});

    const auto fast = require_build(source, false);
    const auto validated = require_build(source, true);

    ASSERT_EQ(fast.records.size(), 1U);
    ASSERT_EQ(validated.records.size(), 1U);
    EXPECT_EQ(fast.records[0].status, status(index::LineStatus::ok_object));
    EXPECT_EQ(validated.records[0].status,
              status(index::LineStatus::invalid_utf8));
}

TEST(JsonlIndexerTest, FastModeCountsUnterminatedLastLine) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "fast-unterminated.jsonl";
    write_text(source, "{}\n[1]");

    const auto result = require_build(source, false);

    ASSERT_EQ(result.records.size(), 2U);
    EXPECT_EQ(result.records[0].byte_offset, 0U);
    EXPECT_EQ(result.records[0].byte_length, 3U);
    EXPECT_EQ(result.records[0].status, status(index::LineStatus::ok_object));
    EXPECT_EQ(result.records[1].byte_offset, 3U);
    EXPECT_EQ(result.records[1].byte_length, 3U);
    EXPECT_EQ(result.records[1].status, status(index::LineStatus::not_object));
}

TEST(JsonlIndexerTest, FastModeEmptyFileProducesFortyByteIndex) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "fast-empty.jsonl";
    const auto output = directory.path() / "fast-empty.ridx";
    write_text(source, "");

    const auto result = require_build(source, false);
    EXPECT_TRUE(result.records.empty());
    EXPECT_EQ(result.header.record_count, 0U);
    EXPECT_EQ(result.header.flags, 0U);

    std::string error;
    ASSERT_TRUE(index::write_index(output, result, error)) << error;
    EXPECT_EQ(std::filesystem::file_size(output), 40U);
}

TEST(JsonlIndexerTest, RoundTripPreservesModeFlag) {
    TemporaryDirectory directory;
    const auto source = directory.path() / "mode-flags.jsonl";
    write_text(source, "{}\n");

    for (const bool validate : std::array<bool, 2>{{false, true}}) {
        const auto output = directory.path() /
                            (validate ? "validated.ridx" : "fast.ridx");
        const auto built = require_build(source, validate);
        std::string error;
        ASSERT_TRUE(index::write_index(output, built, error)) << error;
        const auto loaded = index::read_index(output, error);
        ASSERT_TRUE(loaded.has_value()) << error;
        const std::uint32_t expected_flags =
            validate ? index::kFlagValidated : 0U;
        EXPECT_EQ(built.header.flags, expected_flags);
        EXPECT_EQ(loaded->header.flags, expected_flags);
    }
}

}  // namespace
