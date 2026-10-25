// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0

#include "speaker_dma_reserve.hpp"

#include <stddef.h>
#include <stdint.h>
#include <cstdint>
#include <limits>

#include <sdkconfig.h>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include <M5Unified.h>

namespace {

constexpr const char* kTag = "spk-dma";

// Same pool i2s_alloc_dma_desc uses (INTERNAL|DMA|8BIT).
constexpr uint32_t kDmaCaps = MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA | MALLOC_CAP_8BIT;

// Preferred hold, then the sizes we will accept when the heap will not
// give the full block back after I2S teardown. 1536 B still fits two mono
// 512 B buffers (payload + tlsf slack); below that playRaw is silent again.
constexpr size_t kReserveSizes[] = {4096, 3072, 2048, 1536};
constexpr size_t kMinPark = 1536;

// Leave this much INTERNAL RAM unparked once Wi-Fi has been asked to start.
// esp_wifi_start on CoreS3 (BLE already up, static RX 3 / TX 4) returns
// ESP_ERR_NO_MEM while a 4096 B block is parked. The boot park is still the
// full 4096 B so BLE cannot fragment it; the floor applies only after
// speaker_dma_reserve_repark(), which wifi_start() calls.
constexpr size_t kInternalFloor = 16 * 1024;

// tlsf header + alignment slack on top of the 512 B payload. Conservative
// so the smallest fallback still produces a dma_buf_count that fits.
constexpr size_t kBufOverhead = 64;

void* g_block = nullptr;
size_t g_size = 0;
size_t g_desired_bufs = 0;
bool g_reserve_enabled = false;
bool g_leave_internal_floor = false;

SemaphoreHandle_t reserve_mu()
{
    static SemaphoreHandle_t mu = xSemaphoreCreateMutex();
    return mu;
}

size_t buffers_for_hole(size_t hole, size_t payload)
{
    if (payload == 0) return 2;
    size_t n = hole / (payload + kBufOverhead);
    if (n < 2) n = 2;   // I2S dma_desc_num must be > 1
    if (n > 16) n = 16;
    return n;
}

void hold_largest()
{
    if (!g_reserve_enabled || g_block != nullptr) return;

    const size_t largest = heap_caps_get_largest_free_block(kDmaCaps);
    const size_t free_internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    size_t budget = std::numeric_limits<size_t>::max();
    if (g_leave_internal_floor) {
        // Always willing to park the minimum that keeps playRaw alive, even
        // when that dips under the floor. Anything larger has to leave the
        // floor free for the Wi-Fi driver.
        budget = kMinPark;
        if (free_internal > kInternalFloor) {
            const size_t room = free_internal - kInternalFloor;
            if (room > budget) budget = room;
        }
    }

    for (size_t n : kReserveSizes) {
        if (n > budget || n > largest) continue;
        void* p = heap_caps_malloc(n, kDmaCaps);
        if (p == nullptr) continue;
        g_block = p;
        g_size = n;
        const unsigned left =
            static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
        if (g_leave_internal_floor && free_internal < kInternalFloor + n) {
            ESP_LOGW(kTag,
                     "holding %u B internal DMA under %u B floor (INT free now %u, DMA largest %u)",
                     static_cast<unsigned>(n),
                     static_cast<unsigned>(kInternalFloor),
                     left,
                     static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)));
        } else {
            ESP_LOGI(kTag, "holding %u B internal DMA (largest now %u, INT free %u)",
                     static_cast<unsigned>(n),
                     static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)),
                     left);
        }
        return;
    }
    ESP_LOGW(kTag, "DMA reserve failed (DMA largest %u, INT free %u, budget %u)",
             static_cast<unsigned>(largest),
             static_cast<unsigned>(free_internal),
             static_cast<unsigned>(budget));
}

} // namespace

// Caller holds stackchan_speaker_dma_reserve_enter(). The lock stays held
// through i2s_channel_init_std_mode so a Speaker.end() on the other core
// cannot park the hole again before the driver allocates it.
extern "C" __attribute__((used)) void stackchan_speaker_dma_reserve_enter(void)
{
    if (SemaphoreHandle_t mu = reserve_mu()) xSemaphoreTake(mu, portMAX_DELAY);
}

extern "C" __attribute__((used)) void stackchan_speaker_dma_reserve_leave(void)
{
    if (SemaphoreHandle_t mu = reserve_mu()) xSemaphoreGive(mu);
}

extern "C" __attribute__((used)) void stackchan_speaker_dma_reserve_release(void)
{
    if (!g_reserve_enabled) return;
    const size_t reserved = g_size;
    if (g_block != nullptr) {
        heap_caps_free(g_block);
        g_block = nullptr;
        g_size = 0;
    }
    if (reserved == 0) return;

    // Never ask I2S for more buffers than the largest block actually free,
    // and never for more than the block we parked — a bigger coalesced hole
    // is not ours to spend down to dma_buf_count 16.
    size_t hole = reserved;
    const size_t actual = heap_caps_get_largest_free_block(kDmaCaps);
    if (actual < hole) hole = actual;

    auto cfg = M5.Speaker.config();
    if (cfg.dma_buf_count > g_desired_bufs) g_desired_bufs = cfg.dma_buf_count;
    // Mono 16-bit (CoreS3) → dma_buf_len * 2. Stereo / buzzer doubles it.
    const size_t slots = (cfg.stereo || cfg.buzzer) ? 2u : 1u;
    const size_t payload = cfg.dma_buf_len * sizeof(int16_t) * slots;
    const size_t fit = buffers_for_hole(hole, payload);
    const size_t use = (g_desired_bufs == 0) ? fit
                                              : (fit < g_desired_bufs ? fit : g_desired_bufs);
    if (cfg.dma_buf_count != use) {
        cfg.dma_buf_count = use;
        M5.Speaker.config(cfg);
    }
    ESP_LOGI(kTag, "released %u B for I2S (DMA largest now %u, dma_buf_count %u, buf %u B)",
             static_cast<unsigned>(hole),
             static_cast<unsigned>(actual),
             static_cast<unsigned>(use),
             static_cast<unsigned>(payload));
}

extern "C" __attribute__((used)) void stackchan_speaker_dma_reserve_reacquire(void)
{
    hold_largest();
}

extern "C" __attribute__((used)) StackType_t* stackchan_speaker_psram_stack(size_t bytes)
{
#if CONFIG_SPIRAM && CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM
    static StackType_t* stack = nullptr;
    static size_t cap = 0;
    if (bytes == 0) return nullptr;
    // xTaskCreateStatic requires 16-byte stack alignment, and aligned_alloc
    // requires the size to be a multiple of the alignment.
    constexpr size_t kAlign = 16;
    const size_t bytes_aligned = (bytes + kAlign - 1) & ~(kAlign - 1);
    if (stack != nullptr) return cap >= bytes_aligned ? stack : nullptr;
    stack = static_cast<StackType_t*>(
        heap_caps_aligned_alloc(kAlign, bytes_aligned, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (stack == nullptr) {
        ESP_LOGW(kTag, "PSRAM speaker stack %u B failed; using internal",
                 static_cast<unsigned>(bytes_aligned));
        return nullptr;
    }
    cap = bytes_aligned;
    ESP_LOGI(kTag, "speaker task stack %u B in PSRAM", static_cast<unsigned>(bytes_aligned));
    return stack;
#else
    (void)bytes;
    return nullptr;
#endif
}

namespace stackchan::app {

namespace {

void log_hold(const char* verb, size_t n)
{
    ESP_LOGI(kTag, "%s %u B internal DMA (largest now %u, INT free %u)",
             verb,
             static_cast<unsigned>(n),
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)));
}

// Grow g_block toward the preferred sizes. Never frees it first, so a
// failed realloc leaves the speaker hole in place. Caller holds the mutex.
void grow_inplace(bool leave_floor)
{
    if (g_block == nullptr) return;
    const size_t free_internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    size_t budget = std::numeric_limits<size_t>::max();
    if (leave_floor) {
        budget = g_size;
        if (free_internal > kInternalFloor) {
            const size_t extra = free_internal - kInternalFloor;
            if (g_size <= std::numeric_limits<size_t>::max() - extra) {
                budget = g_size + extra;
            }
        }
    }
    for (size_t n : kReserveSizes) {
        if (n <= g_size || n > budget) continue;
        void* p = heap_caps_realloc(g_block, n, kDmaCaps);
        if (p == nullptr) continue;
        g_block = p;
        g_size = n;
        log_hold("grew to", n);
        return;
    }
    log_hold("keeping", g_size);
}

} // namespace

bool speaker_dma_reserve_shrink_for_radio()
{
    stackchan_speaker_dma_reserve_enter();
    if (!g_reserve_enabled || g_block == nullptr) {
        stackchan_speaker_dma_reserve_leave();
        return false;
    }
    if (g_size <= kMinPark) {
        log_hold("keeping", g_size);
        stackchan_speaker_dma_reserve_leave();
        return true;
    }
    const size_t old = g_size;
    // Split in place. realloc to a smaller size keeps this pointer and frees
    // the tail, so BLE cannot take the speaker hole between free and malloc.
    // A failed realloc leaves the original block allocated.
    void* shrunk = heap_caps_realloc(g_block, kMinPark, kDmaCaps);
    if (shrunk == nullptr) {
        ESP_LOGW(kTag, "DMA park shrink %u -> %u failed; still holding %u",
                 static_cast<unsigned>(old),
                 static_cast<unsigned>(kMinPark),
                 static_cast<unsigned>(g_size));
        stackchan_speaker_dma_reserve_leave();
        return true;
    }
    g_block = shrunk;
    g_size = kMinPark;
    ESP_LOGI(kTag, "shrunk DMA park %u -> %u B for Wi-Fi (INT free=%u largest=%u, DMA largest=%u)",
             static_cast<unsigned>(old),
             static_cast<unsigned>(kMinPark),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)));
    stackchan_speaker_dma_reserve_leave();
    return true;
}

bool speaker_dma_reserve_yield()
{
    stackchan_speaker_dma_reserve_enter();
    if (!g_reserve_enabled) {
        stackchan_speaker_dma_reserve_leave();
        return false;
    }
    if (g_block == nullptr) {
        ESP_LOGI(kTag, "Wi-Fi yield: no parked block (DMA largest %u)",
                 static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)));
        stackchan_speaker_dma_reserve_leave();
        return true;
    }
    const size_t n = g_size;
    heap_caps_free(g_block);
    g_block = nullptr;
    g_size = 0;
    ESP_LOGI(kTag, "yielded %u B for Wi-Fi (INT free=%u largest=%u, DMA largest=%u)",
             static_cast<unsigned>(n),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)));
    stackchan_speaker_dma_reserve_leave();
    return true;
}

void speaker_dma_reserve_repark()
{
    stackchan_speaker_dma_reserve_enter();
    // From here on, including Speaker.end() reacquisitions, do not park so
    // much that Wi-Fi has no internal RAM left for dynamic RX buffers.
    g_leave_internal_floor = true;
    hold_largest();
    stackchan_speaker_dma_reserve_leave();
}

void speaker_dma_reserve_after_radio(bool radio_started)
{
    stackchan_speaker_dma_reserve_enter();
    if (!g_reserve_enabled) {
        stackchan_speaker_dma_reserve_leave();
        return;
    }
    if (radio_started) g_leave_internal_floor = true;
    if (g_block == nullptr) {
        hold_largest();
        stackchan_speaker_dma_reserve_leave();
        return;
    }
    grow_inplace(/*leave_floor=*/radio_started);
    stackchan_speaker_dma_reserve_leave();
}

void speaker_dma_reserve_boot(bool park_dma_hole)
{
    stackchan_speaker_dma_reserve_enter();
    g_reserve_enabled = park_dma_hole;
    hold_largest();
    stackchan_speaker_dma_reserve_leave();
    // Speaker.begin() only references the hooks weakly, and --gc-sections
    // drops weak-only symbols out of libmain.a. These are real relocations
    // from a function app_main calls, so the strong definitions stay linked.
    const uintptr_t enter = reinterpret_cast<uintptr_t>(stackchan_speaker_dma_reserve_enter);
    const uintptr_t leave = reinterpret_cast<uintptr_t>(stackchan_speaker_dma_reserve_leave);
    const uintptr_t release =
        reinterpret_cast<uintptr_t>(stackchan_speaker_dma_reserve_release);
    const uintptr_t reacquire =
        reinterpret_cast<uintptr_t>(stackchan_speaker_dma_reserve_reacquire);
    const uintptr_t stack =
        reinterpret_cast<uintptr_t>(stackchan_speaker_psram_stack);
    asm volatile("" :: "r"(enter), "r"(leave), "r"(release), "r"(reacquire), "r"(stack));
}

} // namespace stackchan::app
