// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

#pragma once

#include <cstdint>
#include <cstring>
#include <map>
#include <string>
#include <variant>

// In-memory stand-in for the NVS boundary; production store/registry code runs unchanged.
using esp_err_t = int;
using nvs_handle_t = int;
inline const char* esp_err_to_name(esp_err_t) {
    return "fake NVS error";
}
inline constexpr esp_err_t ESP_OK = 0;
inline constexpr esp_err_t ESP_ERR_NVS_NOT_FOUND = 1;
inline constexpr esp_err_t ESP_ERR_NVS_INVALID_LENGTH = 2;
inline constexpr int NVS_READONLY = 0;
inline constexpr int NVS_READWRITE = 1;
namespace fake_nvs {
using Value = std::variant<std::string, std::uint8_t, std::uint16_t, std::uint32_t>;
inline std::map<std::string, Value> values;
inline bool exists = false;
} // namespace fake_nvs
inline esp_err_t nvs_open(const char*, int mode, nvs_handle_t* h) {
    if (!fake_nvs::exists && mode == NVS_READONLY)
        return ESP_ERR_NVS_NOT_FOUND;
    fake_nvs::exists = true;
    *h = 1;
    return ESP_OK;
}
inline void nvs_close(nvs_handle_t) {}
inline esp_err_t nvs_commit(nvs_handle_t) {
    return ESP_OK;
}
inline esp_err_t nvs_get_str(nvs_handle_t, const char* key, char* out, std::size_t* len) {
    const auto it = fake_nvs::values.find(key);
    if (it == fake_nvs::values.end())
        return ESP_ERR_NVS_NOT_FOUND;
    const auto* value = std::get_if<std::string>(&it->second);
    if (!value)
        return ESP_ERR_NVS_NOT_FOUND;
    if (out && *len < value->size() + 1)
        return ESP_ERR_NVS_INVALID_LENGTH;
    *len = value->size() + 1;
    if (out)
        std::memcpy(out, value->c_str(), *len);
    return ESP_OK;
}
inline esp_err_t nvs_set_str(nvs_handle_t, const char* key, const char* value) {
    fake_nvs::values[key] = std::string(value);
    return ESP_OK;
}
template <typename T>
esp_err_t fake_get(const char* key, T* value) {
    const auto it = fake_nvs::values.find(key);
    if (it == fake_nvs::values.end())
        return ESP_ERR_NVS_NOT_FOUND;
    const auto* stored = std::get_if<T>(&it->second);
    if (!stored)
        return ESP_ERR_NVS_NOT_FOUND;
    *value = *stored;
    return ESP_OK;
}
inline esp_err_t nvs_get_u8(nvs_handle_t, const char* k, std::uint8_t* v) {
    return fake_get(k, v);
}
inline esp_err_t nvs_get_u16(nvs_handle_t, const char* k, std::uint16_t* v) {
    return fake_get(k, v);
}
inline esp_err_t nvs_get_u32(nvs_handle_t, const char* k, std::uint32_t* v) {
    return fake_get(k, v);
}
inline esp_err_t nvs_set_u8(nvs_handle_t, const char* k, std::uint8_t v) {
    fake_nvs::values[k] = v;
    return ESP_OK;
}
inline esp_err_t nvs_set_u16(nvs_handle_t, const char* k, std::uint16_t v) {
    fake_nvs::values[k] = v;
    return ESP_OK;
}
inline esp_err_t nvs_set_u32(nvs_handle_t, const char* k, std::uint32_t v) {
    fake_nvs::values[k] = v;
    return ESP_OK;
}
