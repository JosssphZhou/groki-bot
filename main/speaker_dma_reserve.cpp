// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0

#include "speaker_dma_reserve.hpp"

#include <stddef.h>
#include <stdint.h>
#include <cstdint>

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
// give the full block back after I2S teardown.
constexpr size_t kReserveSizes[] = {4096, 3072, 2048};

// tlsf header + alignment slack on top of the 512 B payload. Conservative
// so the smallest fallback still produces a dma_buf_count that fits.
constexpr size_t kBufOverhead = 64;

void* g_block = nullptr;
size_t g_size = 0;
size_t g_desired_bufs = 0;
bool g_reserve_enabled = false;

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
    for (size_t n : kReserveSizes) {
        void* p = heap_caps_malloc(n, kDmaCaps);
        if (p == nullptr) continue;
        g_block = p;
        g_size = n;
        ESP_LOGI(kTag, "holding %u B internal DMA (largest now %u)",
                 static_cast<unsigned>(n),
                 static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)));
        return;
    }
    ESP_LOGW(kTag, "DMA reserve failed (largest %u)",
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)));
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
