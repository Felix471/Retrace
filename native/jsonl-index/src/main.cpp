#include "index_format.hpp"

#include <cstdint>
#include <filesystem>
#include <iostream>
#include <string>
#include <system_error>

namespace {

void print_usage() {
    std::cerr << "Usage: retrace-jsonl-index <input.jsonl> [-o <output>]\n";
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc != 2 && argc != 4) {
        print_usage();
        return 2;
    }

    const std::filesystem::path input(argv[1]);
    std::filesystem::path output;

    if (argc == 4) {
        const std::string option(argv[2]);
        if ((option != "-o" && option != "--output") || argv[3][0] == '\0') {
            print_usage();
            return 2;
        }
        output = std::filesystem::path(argv[3]);
    } else {
        output = input;
        output += ".ridx";
    }

    std::error_code equivalence_error;
    if (std::filesystem::equivalent(input, output, equivalence_error)) {
        std::cerr << "retrace-jsonl-index: output must differ from input\n";
        return 3;
    }

    std::string error;
    const auto index = retrace::jsonl_index::build_index(input, error);
    if (!index.has_value()) {
        std::cerr << "retrace-jsonl-index: " << error << '\n';
        return 2;
    }

    if (!retrace::jsonl_index::write_index(output, *index, error)) {
        std::cerr << "retrace-jsonl-index: " << error << '\n';
        return 3;
    }

    std::uint64_t ok_count = 0U;
    for (const auto& record : index->records) {
        if (record.status ==
            static_cast<std::uint8_t>(retrace::jsonl_index::LineStatus::ok_object)) {
            ++ok_count;
        }
    }

    std::cout << index->header.record_count << " lines, " << ok_count
              << " objects -> " << output.u8string() << '\n';
    return 0;
}
