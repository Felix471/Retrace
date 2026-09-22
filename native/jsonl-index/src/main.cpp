#include "index_format.hpp"

#include <cstdint>
#include <filesystem>
#include <iostream>
#include <string>
#include <system_error>

namespace {

void print_usage() {
    std::cerr
        << "Usage: retrace-jsonl-index <input.jsonl> [-o <output>] [--validate]\n";
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc < 2) {
        print_usage();
        return 2;
    }

    const std::filesystem::path input(argv[1]);
    std::filesystem::path output = input;
    output += ".ridx";
    bool output_set = false;
    bool validate = false;

    for (int index = 2; index < argc; ++index) {
        const std::string option(argv[index]);
        if (option == "--validate") {
            if (validate) {
                print_usage();
                return 2;
            }
            validate = true;
        } else if (option == "-o" || option == "--output") {
            if (output_set || index + 1 >= argc || argv[index + 1][0] == '\0') {
                print_usage();
                return 2;
            }
            output = std::filesystem::path(argv[index + 1]);
            output_set = true;
            ++index;
        } else {
            print_usage();
            return 2;
        }
    }

    std::error_code equivalence_error;
    if (std::filesystem::equivalent(input, output, equivalence_error)) {
        std::cerr << "retrace-jsonl-index: output must differ from input\n";
        return 3;
    }

    std::string error;
    const auto result = retrace::jsonl_index::build_index(input, error, validate);
    if (!result.has_value()) {
        std::cerr << "retrace-jsonl-index: " << error << '\n';
        return 2;
    }

    if (!retrace::jsonl_index::write_index(output, *result, error)) {
        std::cerr << "retrace-jsonl-index: " << error << '\n';
        return 3;
    }

    std::uint64_t ok_count = 0U;
    for (const auto& record : result->records) {
        if (record.status ==
            static_cast<std::uint8_t>(retrace::jsonl_index::LineStatus::ok_object)) {
            ++ok_count;
        }
    }

    std::cout << result->header.record_count << " lines, " << ok_count
              << " objects, " << (validate ? "validated" : "fast") << " -> "
              << output.u8string() << '\n';
    return 0;
}
