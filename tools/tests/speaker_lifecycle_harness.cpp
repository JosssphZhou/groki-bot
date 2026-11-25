// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

// Deterministic scheduler: idle cleanup may be delayed arbitrarily. The real
// patched begin/end and worker shutdown code run against these task primitives.
#include <atomic>
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <cstring>

#ifndef TEST_SPIRAM
#define TEST_SPIRAM 1
#endif
#define CONFIG_SPIRAM TEST_SPIRAM
#define CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM 1
#define CONFIG_IDF_TARGET 1
#define CONFIG_IDF_TARGET_ESP32S3 1
#define portNUM_PROCESSORS 2
#define pdMS_TO_TICKS(n) (n)
using TickType_t = unsigned;
using BaseType_t = int;
using StackType_t = unsigned char;
constexpr int tskNO_AFFINITY = -1, pdPASS = 1, pdFAIL = 0, ESP_OK = 0;
constexpr int MALLOC_CAP_SPIRAM = 1, MALLOC_CAP_INTERNAL = 2, MALLOC_CAP_8BIT = 4;
enum State { eRunning, eSuspended, eDeleted };
struct StaticTask_t {
    State state = eRunning;
    bool pending = false;
    bool dynamic = false;
};
using TaskHandle_t = StaticTask_t*;
static TaskHandle_t current = nullptr;
static TaskHandle_t deferred = nullptr;
static int allocated = 0, held = 0, setups = 0, external_requests = 0;
static TickType_t ticks = 10;
static bool fail_i2s = false, fail_external = false, fail_tasks = false;
struct Suspended {};
void check(bool ok, const char* why) {
    if (!ok) {
        std::fprintf(stderr, "%s\n", why);
        std::exit(1);
    }
}
TickType_t xTaskGetTickCount() {
    return ticks;
}
void vTaskDelay(unsigned n) {
    ticks += n;
#if !CONFIG_SPIRAM
    if (deferred) {
        --allocated;
        delete deferred;
        deferred = nullptr;
    }
#endif
    // With external stacks, deliberately starve idle cleanup.
}
void* xSemaphoreCreateBinary() {
    return reinterpret_cast<void*>(1);
}
void vTaskSuspend(TaskHandle_t) {
    current->state = eSuspended;
    throw Suspended{};
}
State eTaskGetState(TaskHandle_t t) {
    return t->state;
}
void vTaskDelete(TaskHandle_t) {
    current->state = eDeleted;
    current->pending = true;
    deferred = current;
}
void vTaskDeleteWithCaps(TaskHandle_t t) {
    check(t->state == eSuspended, "speaker must finish I2S before deletion");
    check(t->dynamic, "WithCaps deletion requires a WithCaps task");
    --allocated;
    delete t;
}
TaskHandle_t xTaskCreateStaticPinnedToCore(void (*)(void*), const char*, std::size_t, void*, int, StackType_t*,
                                           StaticTask_t* t, int) {
    check(!t->pending, "static speaker TCB reused before idle cleanup");
    t->state = eRunning;
    return t;
}
int xTaskCreatePinnedToCoreWithCaps(void (*)(void*), const char*, std::size_t, void*, int, TaskHandle_t* out, int,
                                    unsigned caps) {
    if (caps & MALLOC_CAP_SPIRAM)
        ++external_requests;
    if (fail_tasks || (fail_external && (caps & MALLOC_CAP_SPIRAM)))
        return pdFAIL;
    *out = new StaticTask_t;
    (*out)->dynamic = true;
    ++allocated;
    return pdPASS;
}
int xTaskCreatePinnedToCore(void (*)(void*), const char*, std::size_t, void*, int, TaskHandle_t* out, int) {
    check(!CONFIG_SPIRAM, "external-stack configuration used default task allocation");
    if (fail_tasks)
        return pdFAIL;
    *out = new StaticTask_t;
    (*out)->dynamic = true;
    ++allocated;
    return pdPASS;
}
int xTaskCreate(void (*)(void*), const char*, std::size_t, void*, int, TaskHandle_t*) {
    check(false, "unexpected default task allocation");
    return pdFAIL;
}
void _i2s_driver_uninstall(int) {}
void _i2s_stop(int) {}

class Speaker_Class {
public:
    bool _task_running = false;
    TaskHandle_t _task_handle = nullptr;
    void* _task_semaphore = nullptr;
    bool (*_cb_set_enabled)(void*, bool) = nullptr;
    void* _cb_set_enabled_args = nullptr;
    struct {
        std::size_t dma_buf_len = 256;
        int task_pinned_core = 1, task_priority = 6, i2s_port = 1;
    } _cfg;
    struct Wave {
        void clear() {}
    };
    struct Channel {
        Wave wavinfo[2];
        int index = 0, diff = 0;
        bool flip = false;
        int liner_buf[2][2]{};
    };
    static constexpr unsigned sound_channel_max = 8;
    Channel _ch_info[sound_channel_max];
    std::atomic<int> _play_channel_bits{0};
    bool begin();
    void end();
    void stop() {}
    int _setup_i2s() {
        ++setups;
        return fail_i2s ? 1 : ESP_OK;
    }
    static void spk_task(void*);
};
static Speaker_Class* active;
void xTaskNotifyGive(TaskHandle_t t) {
    current = t;
    try {
        Speaker_Class::spk_task(active);
    } catch (const Suspended&) {
    }
    current = nullptr;
}

#include "speaker.inc"
extern "C" void stackchan_speaker_dma_reserve_enter() {
    ++held;
}
extern "C" void stackchan_speaker_dma_reserve_leave() {
    --held;
}
extern "C" void stackchan_speaker_dma_reserve_release() {}
extern "C" void stackchan_speaker_dma_reserve_reacquire() {}
// Kept to make the same test fail on PR #26's shared-static-stack version.
extern "C" StackType_t* stackchan_speaker_psram_stack(std::size_t) {
#if CONFIG_SPIRAM
    static StackType_t stack[4096];
    return stack;
#else
    return nullptr;
#endif
}

int main(int argc, char** argv) {
    if (argc != 2)
        return 2;
    Speaker_Class speaker;
    active = &speaker;
    fail_i2s = std::strcmp(argv[1], "i2s-fail") == 0;
    fail_external = std::strcmp(argv[1], "fallback") == 0;
    fail_tasks = std::strcmp(argv[1], "task-fail") == 0;
    if (fail_i2s || fail_tasks) {
        check(!speaker.begin(), "failed allocation must not start speaker");
        check(!speaker._task_running && !speaker._task_handle, "failed task left running state");
        int previous = setups;
        check(!speaker.begin() && setups == previous, "failed begin retried without backoff");
        check(held == 0 && allocated == 0, "failed begin leaked lock or task");
        return 0;
    }
    for (int turn = 0; turn < 100; ++turn) {
        check(speaker.begin(), "speaker begin failed");
        speaker.end();
        check(!speaker._task_running && !speaker._task_handle, "end did not stop task");
        check(allocated == 0 && held == 0, "end leaked task or lock");
    }
    check(external_requests == (CONFIG_SPIRAM ? 100 : 0), "unexpected stack allocation policy");
    std::puts("100 speaker cycles completed without task leaks");
}
