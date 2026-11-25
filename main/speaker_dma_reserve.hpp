// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0

#pragma once

namespace stackchan::app {

// Park a contiguous INTERNAL|DMA block while the speaker is idle.
//
// CoreS3 playRaw configures I2S only when speaking starts. By then Wi-Fi,
// TLS and BLE have fragmented DMA-capable internal RAM so the largest hole
// is ~400 B, and i2s_alloc_dma_desc cannot get its ~512 B buffers
// (ALLOC_FAIL storm, silent speaker). Call once at boot, after the
// conversation segment ring and before that fragmentation. Pass park_dma_hole
// only for CoreS3-class boards: M5.Speaker.begin() releases the block
// immediately before I2S init and end() parks it again (4096, else 3072,
// else 2048, else 1536 — two mono 512 B buffers). Other boards pass false
// so a later Speaker.end() does not pin 4 KiB they never needed.
//
// Do not keep the full block across esp_wifi_start. On CoreS3 that call
// returns ESP_ERR_NO_MEM while 4096 B is parked (BLE is already up) and an
// ESP_ERROR_CHECK there reboot-loops. wifi_start() shrinks the park to
// 1536 B first (enough for two 512 B I2S buffers) and releases the rest
// only if start still returns ESP_ERR_NO_MEM.
void speaker_dma_reserve_boot(bool park_dma_hole);

// Shrink a larger park to 1536 B in place (realloc splits the block, the
// speaker hole stays allocated, the tail goes back to the heap). False when
// the reserve is disabled or nothing is parked.
bool speaker_dma_reserve_shrink_for_radio();

// Free the parked block so the Wi-Fi driver can allocate from it. Returns
// false when the reserve is disabled (nothing to put back). Returns true
// when the reserve is armed, even if no block was currently parked. Does
// not change Speaker dma_buf_count (that happens only on the I2S release hook).
bool speaker_dma_reserve_yield();

// Park a hole again. After the first call, the park will not spend the last
// 16 KiB of internal RAM (Wi-Fi dynamic buffers); it still takes the
// smallest useful hole when that is all that fits.
void speaker_dma_reserve_repark();

// Settle the park after esp_wifi_start. Grows an in-place shrink back up
// when the driver did not need the tail. If the block was fully released,
// parks whatever hole remains. A successful start also enables the 16 KiB
// internal floor for later Speaker.end() reacquisitions.
void speaker_dma_reserve_after_radio(bool radio_started);

} // namespace stackchan::app
