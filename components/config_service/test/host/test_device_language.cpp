// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

#include <config_service/config_store.hpp>
#include <config_service/settings_registry.hpp>
#include <config_service/staged_config.hpp>
#include <nvs.h>
#include "pat_bubble.hpp"
#include "test_support.hpp"

int main() {
    const auto* language = stackchan::config::registry::find("device-language");
    CHECK(language != nullptr);
    if (!language)
        return avtest::finish("device_language");
    CHECK(stackchan::app::pat_bubble_text(stackchan::config::store::load().*(language->str_member)) == "Pat pat☆");
    CHECK(stackchan::config::store::load().device_language == "en");
    CHECK(stackchan::config::registry::needs_reboot(*language));

    // Upgrade from a device with existing settings but no language key.
    nvs_handle_t handle;
    CHECK(nvs_open("stackchan_cfg", NVS_READWRITE, &handle) == ESP_OK);
    CHECK(nvs_set_str(handle, "wifi_ssid", "test-network") == ESP_OK);
    CHECK(nvs_set_str(handle, "wifi_pass", "test-password") == ESP_OK);
    CHECK(nvs_commit(handle) == ESP_OK);
    nvs_close(handle);
    auto active = stackchan::config::store::load();
    CHECK(active.device_language == "en");
    CHECK(active.wifi_ssid == "test-network");

    // The same staging/save/load path as BLE Apply must survive a reboot.
    stackchan::config::StagedConfig staging;
    staging.set_str("device-language", "zh");
    CHECK(active.device_language == "en");
    staging.merge_into(active);
    CHECK(active.device_language == "zh");
    CHECK(stackchan::config::store::save(active).has_value());
    auto rebooted = stackchan::config::store::load();
    CHECK(stackchan::app::pat_bubble_text(rebooted.device_language) == "摸摸♡");
    CHECK(rebooted.wifi_ssid == "test-network");
    CHECK(rebooted.wifi_password == "test-password");

    staging.clear();
    staging.set_str("device-language", "en");
    staging.merge_into(rebooted);
    CHECK(stackchan::config::store::save(rebooted).has_value());
    CHECK(stackchan::app::pat_bubble_text(stackchan::config::store::load().device_language) == "Pat pat☆");

    // A corrupt or unsupported stored language falls back without touching other settings.
    for (const auto* invalid : {"xx", "", "zh-CN"}) {
        rebooted.device_language = invalid;
        CHECK(stackchan::config::store::save_one(*language, rebooted).has_value());
        const auto loaded = stackchan::config::store::load();
        CHECK(loaded.device_language == "en");
        CHECK(stackchan::app::pat_bubble_text(loaded.device_language) == "Pat pat☆");
        CHECK(loaded.wifi_password == "test-password");
    }
    return avtest::finish("device_language");
}
