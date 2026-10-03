// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

#pragma once
inline void fake_esp_log(const char*, const char*, ...) {}
#define ESP_LOGW(...) fake_esp_log(__VA_ARGS__)
#define ESP_LOGE(...) fake_esp_log(__VA_ARGS__)
