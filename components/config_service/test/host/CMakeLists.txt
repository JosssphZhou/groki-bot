# SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
# SPDX-License-Identifier: BSL-1.0

cmake_minimum_required(VERSION 3.16)
project(config_service_host_test CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CONFIG_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)
set(REPO_ROOT ${CONFIG_ROOT}/../..)
enable_testing()
add_executable(config_test_device_language test_device_language.cpp
    ${CONFIG_ROOT}/settings_registry.cpp ${CONFIG_ROOT}/staged_config.cpp
    ${CONFIG_ROOT}/config_store.cpp)
target_include_directories(config_test_device_language PRIVATE
    idf_stub ${CONFIG_ROOT}/include ${REPO_ROOT}/main
    ${REPO_ROOT}/components/avatar/test/host
    ${REPO_ROOT}/components/tl_expected/expected/include)
target_compile_options(config_test_device_language PRIVATE -Wall -Wextra)
add_test(NAME device_language COMMAND config_test_device_language)
