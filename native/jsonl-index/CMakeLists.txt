cmake_minimum_required(VERSION 3.20)

if(NOT DEFINED CMAKE_BUILD_TYPE OR CMAKE_BUILD_TYPE STREQUAL "")
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

project(retrace_jsonl_index LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(FetchContent)

set(JSON_BuildTests OFF CACHE BOOL "" FORCE)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)

FetchContent_Declare(
    nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.11.3
)

FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG v1.14.0
)

FetchContent_MakeAvailable(nlohmann_json googletest)

function(retrace_target_warnings target)
    if(MSVC)
        target_compile_options(${target} PRIVATE /W4)
    else()
        target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
    endif()
endfunction()

add_library(jsonl_index STATIC src/indexer.cpp)
target_include_directories(jsonl_index PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(jsonl_index PUBLIC nlohmann_json::nlohmann_json)
retrace_target_warnings(jsonl_index)

add_executable(retrace-jsonl-index src/main.cpp)
target_link_libraries(retrace-jsonl-index PRIVATE jsonl_index)
set_target_properties(retrace-jsonl-index PROPERTIES OUTPUT_NAME "retrace-jsonl-index")
retrace_target_warnings(retrace-jsonl-index)

enable_testing()
file(GLOB JSONL_INDEX_TEST_SOURCES CONFIGURE_DEPENDS tests/*.cpp)
add_executable(jsonl_index_tests ${JSONL_INDEX_TEST_SOURCES})
target_link_libraries(jsonl_index_tests PRIVATE jsonl_index GTest::gtest_main)
retrace_target_warnings(jsonl_index_tests)

include(GoogleTest)
gtest_discover_tests(jsonl_index_tests)

install(TARGETS retrace-jsonl-index)
