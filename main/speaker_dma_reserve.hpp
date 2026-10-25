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
// else 2048). Other boards pass false so a later Speaker.end() does not
// pin 4 KiB they never needed.
void speaker_dma_reserve_boot(bool park_dma_hole);

} // namespace stackchan::app
