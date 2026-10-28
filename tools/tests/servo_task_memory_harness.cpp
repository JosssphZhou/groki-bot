// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#include <cstddef>
#include <iostream>
#include <stdexcept>
#include <string>

using UBaseType_t = unsigned;
constexpr unsigned MALLOC_CAP_INTERNAL = 1;
constexpr unsigned MALLOC_CAP_SPIRAM = 2;
constexpr unsigned MALLOC_CAP_8BIT = 4;
constexpr int pdPASS = 1;
constexpr const char* kTag = "servo";
struct ServoTaskArgs { int bus_cfg = 0; };
struct BusFailure {
    explicit operator bool() const { return false; }
    int error() const { return 1; }
};
namespace scs_servo {
struct ScsBus { static BusFailure create(int) { return {}; } };
}
struct Deleted { bool with_caps; };
void vTaskDelete(void*) { throw Deleted{false}; }
void vTaskDeleteWithCaps(void*) { throw Deleted{true}; }
static unsigned logs = 0;
#define ESP_LOGE(...) (++logs)
static bool fail_creation = false;
static unsigned creates = 0;
static void (*callback)(void*) = nullptr;
static void* callback_arg = nullptr;
static unsigned allocated_caps = 0;
static bool created_with_caps = false;
// A bounded headroom model, not an emulator of ESP-IDF's heap layout.
// An internal servo stack leaves 8704 contiguous bytes: below HTTP's 12288.
static std::size_t internal_largest = 16896;
static std::size_t external_stack_bytes = 0;
static void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}
int create(void (*entry)(void*), const char*, unsigned bytes, void* arg,
           unsigned priority, void* handle, int core, unsigned caps, bool with_caps) {
    ++creates;
    require(bytes == 8192 && priority == 4 && core == 0 && handle == nullptr,
            "servo scheduling/stack budget changed");
    if (fail_creation) return 0;
    callback = entry; callback_arg = arg;
    allocated_caps = caps; created_with_caps = with_caps;
    if (caps & MALLOC_CAP_SPIRAM) external_stack_bytes += bytes;
    else internal_largest -= bytes;
    return pdPASS;
}
int xTaskCreatePinnedToCore(void (*entry)(void*), const char* name, unsigned bytes,
                           void* arg, unsigned priority, void* handle, int core) {
    return create(entry, name, bytes, arg, priority, handle, core,
                  MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT, false);
}
int xTaskCreatePinnedToCoreWithCaps(void (*entry)(void*), const char* name, unsigned bytes,
                                   void* arg, unsigned priority, void* handle, int core, unsigned caps) {
    return create(entry, name, bytes, arg, priority, handle, core, caps, true);
}
#include "servo_task_memory.inc"

int main(int argc, char** argv) {
    try {
        require(argc == 2, "case required");
        const std::string mode = argv[1];
        fail_creation = mode == "allocation-failure";
        ServoTaskArgs args;
        start_servo_task(args);
        require(creates == 1, "allocation failure must not retry in scarce internal RAM");
        if (fail_creation) {
            require(callback == nullptr && logs == 1, "task creation failure was silent");
            require(internal_largest == 16896 && external_stack_bytes == 0, "failed creation consumed memory");
        } else if (mode == "budget") {
#if CONFIG_SPIRAM && CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM
            require(internal_largest >= 12288, "servo stack consumed the HTTP task's contiguous internal headroom");
            require(external_stack_bytes == 8192, "PSRAM board did not use an external stack");
#else
            require((allocated_caps & MALLOC_CAP_INTERNAL) != 0 && external_stack_bytes == 0,
                    "configuration without external stacks must use internal memory");
#endif
        } else if (mode == "bus-failure") {
            try {
                callback(callback_arg);
                require(false, "bus failure did not terminate task");
            } catch (const Deleted& deleted) {
                require(created_with_caps && deleted.with_caps, "servo failure exit does not reclaim a WithCaps stack");
            }
        } else {
            require(false, "unknown case");
        }
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
