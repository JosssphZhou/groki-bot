// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <string_view>
#include <vector>

constexpr std::size_t kUplinkFrameSamples = 960;
constexpr int pdTRUE = 1;
constexpr unsigned pdMS_TO_TICKS(unsigned ms) { return ms; }
struct AudioChunk {
    std::size_t len_samples = 640;
    std::int16_t data[640];
};

static unsigned remaining_chunks = 192;
static unsigned pending_allocations = 0;
static std::size_t input_samples = 0;
static unsigned busy_ms = 0;
static unsigned max_busy_ms = 0;

// Model a producer that refills the bounded queue: receives need not block
// while a backlog exists. A queue timeout alone cannot guarantee idle time.
int xQueueReceive(void*, AudioChunk** out, unsigned) {
    if (remaining_chunks == 0) {
        *out = nullptr;
        return pdTRUE;
    }
    --remaining_chunks;
    *out = new AudioChunk;
    ++pending_allocations;
    for (auto& sample : (*out)->data) sample = static_cast<std::int16_t>(input_samples++ % 32767);
    return pdTRUE;
}

void heap_caps_free(AudioChunk* chunk) {
    --pending_allocations;
    delete chunk;
}

void vTaskDelay(unsigned ticks) {
    if (ticks != 0) busy_ms = 0;
}

class Sender {
public:
    std::atomic<bool> sender_should_exit_{false};
    void* audio_tx_queue_ = this;
    void* encoder_ = this;
    std::size_t enc_in_bytes_ = kUplinkFrameSamples * sizeof(std::int16_t);
    std::vector<std::int16_t> enc_accum_;
    bool connected = true;
    unsigned frames = 0;
    std::size_t output_samples = 0;

    bool encode_and_send(std::int16_t* samples) {
        if (!connected) return false;
        // The live trace includes 60-90 ms encode calls during backlog.
        // Fast transport does not provide a scheduling gap in this case.
        busy_ms += 75;
        max_busy_ms = std::max(max_busy_ms, busy_ms);
        for (std::size_t i = 0; i < kUplinkFrameSamples; ++i) {
            assert(samples[i] == static_cast<std::int16_t>(output_samples++ % 32767));
        }
        ++frames;
        return true;
    }

#include "xiaozhi_sender.inc"
};

int main(int argc, char** argv) {
    assert(argc == 2);
    const std::string_view mode{argv[1]};
    assert(mode == "backlog" || mode == "partial" || mode == "disconnected");
    Sender sender;
    if (mode == "partial") remaining_chunks = 1;
    if (mode == "disconnected") sender.connected = false;
    sender.sender_loop();
    assert(pending_allocations == 0);
    if (mode == "backlog") {
        assert(sender.frames == 128);
        assert(sender.output_samples == input_samples);
        assert(sender.enc_accum_.empty());
        // This checks the sender's own bounded runnable interval. Real-device
        // tests still need to cover competing tasks on the same core.
        assert(max_busy_ms < 5000);
    } else if (mode == "partial") {
        assert(sender.frames == 0);
        assert(sender.enc_accum_.size() == 640);
    } else {
        assert(sender.frames == 0);
        assert(sender.enc_accum_.empty());
    }
    std::cout << mode << ": passed\n";
}
