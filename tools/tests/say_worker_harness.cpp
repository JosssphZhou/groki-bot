// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

// Run the real worker callback with tracked allocations. Task deletion exits
// without C++ unwinding so a destructor skipped by firmware cannot pass here.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <string_view>
#include <vector>
#include <cstdint>
#include <new>

static long allocations = 0;
void* operator new(std::size_t n) {
    if (auto p = std::malloc(n ? n : 1)) {
        ++allocations;
        return p;
    }
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept {
    if (p) {
        --allocations;
        std::free(p);
    }
}
void operator delete(void* p, std::size_t) noexcept {
    ::operator delete(p);
}

static const char* scenario;
static long baseline;
static bool said_done = false;
using BaseType_t = int;
using UBaseType_t = unsigned;
constexpr int MALLOC_CAP_SPIRAM = 1, MALLOC_CAP_8BIT = 2, tskIDLE_PRIORITY = 0, pdPASS = 1;
constexpr const char* kTag = "test";
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define pdMS_TO_TICKS(ms) (ms)
void vTaskDelay(unsigned) {}
unsigned heap_caps_get_largest_free_block(int) {
    return 65536;
}
static void (*task_entry)(void*);
static void* task_arg;
BaseType_t xTaskCreatePinnedToCoreWithCaps(void (*fn)(void*), const char*, unsigned, void* arg, unsigned, void*, int,
                                           unsigned) {
    if (std::strcmp(scenario, "create-fail") == 0)
        return 0;
    task_entry = fn;
    task_arg = arg;
    return pdPASS;
}
[[noreturn]] void vTaskDeleteWithCaps(void*) {
    const auto leaked = allocations - baseline;
    const bool expected_done = std::strcmp(scenario, "success") == 0;
    std::printf("%s: outstanding allocations=%ld, completion=%d\n", scenario, leaked, said_done);
    std::fflush(stdout);
    std::_Exit(leaked == 0 && said_done == expected_done ? 0 : 1);
}
namespace stackchan::jtts {
struct Options {
    unsigned sample_rate_hz = 16000;
};
struct Result {
    bool ok;
    explicit operator bool() const {
        return ok;
    }
    int error() const {
        return 1;
    }
};
const char* to_string(int) {
    return "error";
}
Result synthesize(const std::u32string&, std::vector<std::int16_t>& pcm, const Options&) {
    if (std::strcmp(scenario, "empty") == 0)
        return {true};
    pcm.resize(4096);
    return {std::strcmp(scenario, "synth-fail") != 0};
}
} // namespace stackchan::jtts
namespace stackchan::app {
struct Speech {
    static constexpr unsigned kSampleRate = 16000;
};
jtts::Options resolve_speech_options(const char*, unsigned) {
    return {};
}
std::u32string decode_utf8(const std::string& s) {
    return std::strcmp(scenario, "invalid") == 0 ? std::u32string{} : std::u32string(s.size(), U'x');
}
} // namespace stackchan::app
namespace stackchan::wifi_config::mcp_events {
void publish_say_done() {
    said_done = true;
}
} // namespace stackchan::wifi_config::mcp_events
struct FakeSpeaker {
    bool isPlaying() {
        return false;
    }
    bool playRaw(const std::int16_t*, std::size_t, unsigned, bool) {
        return std::strcmp(scenario, "play-fail") != 0;
    }
};
struct {
    FakeSpeaker Speaker;
} M5;
static stackchan::jtts::Options g_say_opts;
static bool g_say_opts_ready = true;

#include "worker.inc"

int main(int argc, char** argv) {
    if (argc != 2)
        return 2;
    scenario = argv[1];
    baseline = allocations;
    start_say_worker("a deliberately long speech input exceeding the small string buffer");
    if (std::strcmp(scenario, "create-fail") == 0)
        return allocations == baseline ? 0 : 1;
    task_entry(task_arg);
    return 2;
}
