// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#include <cassert>
#include <cstring>
#include <iostream>
#include <string_view>

using esp_err_t = int;
constexpr esp_err_t ESP_OK = 0;
constexpr esp_err_t ESP_ERR_NOT_FOUND = 1;
constexpr esp_err_t ESP_ERR_NVS_NO_FREE_PAGES = 2;
constexpr esp_err_t ESP_ERR_NVS_NEW_VERSION_FOUND = 3;
constexpr int ESP_PARTITION_TYPE_DATA = 1;
constexpr int ESP_PARTITION_SUBTYPE_ANY = 0xff;
constexpr const char* kTag = "voice-db";
constexpr const char* kPart = "storage";

struct esp_partition_t {};
static esp_partition_t partition;
static bool present = true;
static bool initialized = false;
static bool custom_subtype = true;
static unsigned full_scans = 0;

#define ESP_LOGW(...) ((void)0)
#define ESP_LOGD(...) ((void)0)

const esp_partition_t* esp_partition_find_first(int, int, const char* name) {
    assert(std::strcmp(name, kPart) == 0);
    return present ? &partition : nullptr;
}

// Match IDF 5.5.4: the named API reuses registered storage; the pointer API
// calls Storage::init even when another component already registered it.
esp_err_t nvs_flash_init_partition(const char* name) {
    assert(std::strcmp(name, kPart) == 0);
    if (initialized) return ESP_OK;
    if (custom_subtype) return ESP_ERR_NOT_FOUND;
    ++full_scans;
    initialized = true;
    return ESP_OK;
}

esp_err_t nvs_flash_init_partition_ptr(const esp_partition_t* part) {
    assert(part == &partition);
    ++full_scans;
    initialized = true;
    return ESP_OK;
}

#include "voice_partition.inc"

int main(int argc, char** argv) {
    assert(argc == 2);
    const std::string_view mode{argv[1]};
    assert(mode == "loaded" || mode == "custom-first" ||
           mode == "nvs-first" || mode == "missing");
    initialized = mode == "loaded";
    custom_subtype = mode != "nvs-first";
    present = mode != "missing";

    assert(ensure_partition() == present);
    assert(initialized == present);
    const unsigned expected_scans = (mode == "custom-first" || mode == "nvs-first") ? 1 : 0;
    assert(full_scans == expected_scans);
    assert(ensure_partition() == present);
    assert(full_scans == expected_scans);
    std::cout << mode << ": passed\n";
}
