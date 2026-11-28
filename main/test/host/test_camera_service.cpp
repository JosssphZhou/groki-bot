// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#include "camera_service.hpp"
#include "fake_camera.hpp"

#include <cstdlib>
#include <chrono>
#include <future>
#include <iostream>
#include <string_view>
#include <wifi_config_service/wifi_config_service.hpp>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(EXIT_FAILURE);
    }
}

struct Capture {
    std::vector<std::uint8_t> bytes;
    std::size_t width = 0;
    std::size_t height = 0;
    std::string format;

    bool run(const stackchan::wifi_config::CameraCaptureOptions& options = {})
    {
        require(static_cast<bool>(stackchan::wifi_config::capture_sink), "camera capture sink was not registered");
        return stackchan::wifi_config::capture_sink(options, bytes, width, height, format);
    }
};

void check_released(const stackchan::app::SharedState& state)
{
    require(!state.i2c_quiesce.load(), "camera request left other tasks quiesced");
    require(camera_test::hardware.outstanding_frames == 0, "camera request leaked a framebuffer");
    require(camera_test::hardware.unsafe_operations == 0,
            "camera touched hardware without quiesce or reinitialized with an outstanding frame");
}

} // namespace

int main(int argc, char** argv)
{
    require(argc == 2, "expected one scenario name");
    const std::string_view scenario = argv[1];
    using namespace stackchan;
    auto& hw = camera_test::hardware;
    app::SharedState state;
    board::Board board(scenario == "unsupported" ? board::BoardKind::AtomS3 : board::BoardKind::M5Base);
    hw.fail_rgb_init = scenario == "boot_recovery" || scenario == "init_failure" || scenario == "register_recovery";
    app::camera_service::init_resident(board);
    app::camera_service::register_sinks(state);
    hw.quiesce = &state.i2c_quiesce;

    if (scenario == "disabled" || scenario == "unsupported") {
        require(!wifi_config::capture_sink && !wifi_config::reg_sink, "unsupported camera registered an HTTP sink");
        require(hw.begin_calls == 0, "unsupported camera initialized hardware");
        return EXIT_SUCCESS;
    }

    require(static_cast<bool>(wifi_config::capture_sink), "supported camera did not expose capture capability");
    require(static_cast<bool>(wifi_config::reg_sink), "supported camera did not expose register capability");
    Capture frame;

    if (scenario == "normal" || scenario == "boot_recovery") {
        hw.fail_rgb_init = false;
        require(frame.run(), "RGB capture failed");
        require(frame.format == "rgb565be", "RGB capture returned wrong pixel format");
        require(frame.width == 320 && frame.height == 240, "ordinary capture must use QVGA");
        require(frame.bytes.size() == frame.width * frame.height * 2, "RGB response dimensions do not match bytes");
        require(frame.bytes == hw.pixels, "RGB response corrupted pixel bytes");
        if (scenario == "boot_recovery") {
            require(hw.begin_calls >= 2, "capture did not retry failed boot initialization");
        }
    } else if (scenario == "init_failure") {
        require(!frame.run(), "unavailable hardware reported a successful capture");
    } else if (scenario == "raw") {
        require(frame.run({.raw_bayer = true}), "RAW capture failed");
        require(frame.format == "bayer8-rggb", "RAW capture returned wrong pixel format");
        require(frame.width == 640 && frame.height == 480, "RAW capture must preserve VGA Bayer mosaic");
        require(frame.bytes.size() == frame.width * frame.height, "RAW dimensions do not match bytes");
        for (std::size_t i = 0; i < frame.bytes.size(); ++i) {
            require(frame.bytes[i] == static_cast<std::uint8_t>(i), "RAW response corrupted pixel bytes");
        }
        check_released(state);
        require(frame.run(), "normal capture failed after RAW capture");
        require(frame.width == 320 && frame.height == 240, "RAW capture did not restore QVGA");
    } else if (scenario == "raw_restore_recovery") {
        hw.fail_rgb_init = true;
        (void)frame.run({.raw_bayer = true});
        check_released(state);
        require(!hw.initialized, "test did not exercise failed RGB restoration");
        hw.fail_rgb_init = false;
        require(frame.run(), "capture did not recover after RAW restore failed");
        require(frame.format == "rgb565be", "recovered camera did not return RGB565");
    } else if (scenario == "raw_init_failure") {
        hw.fail_raw_init = true;
        require(!frame.run({.raw_bayer = true}), "failed RAW initialization reported success");
        check_released(state);
        require(frame.run(), "normal capture failed after unsuccessful RAW request");
    } else if (scenario == "colorbar_enable_failure") {
        hw.fail_colorbar_enable = true;
        require(!frame.run({.colorbar = true}), "failed colorbar setup reported success");
    } else if (scenario == "colorbar_disable_failure") {
        hw.fail_colorbar_disable = true;
        require(!frame.run({.colorbar = true}), "failed colorbar restoration reported success");
        check_released(state);
        hw.fail_colorbar_disable = false;
        require(frame.run(), "camera did not recover from failed colorbar restoration");
        require(!hw.colorbar, "normal capture after colorbar failure still uses the test pattern");
    } else if (scenario == "capture_failure") {
        hw.fail_capture = true;
        require(!frame.run({.colorbar = true}), "failed frame acquisition reported success");
        require(!hw.colorbar, "failed frame acquisition left colorbar enabled");
        require(!hw.initialized, "persistent capture failure did not reset the driver");
        check_released(state);
        const auto begins_before_recovery = hw.begin_calls;
        hw.fail_capture = false;
        require(frame.run(), "capture did not recover after a persistent frame failure");
        require(hw.begin_calls > begins_before_recovery, "capture recovery did not reinitialize hardware");
    } else if (scenario == "register_failure") {
        hw.fail_register = true;
        std::uint8_t value = 0;
        require(!wifi_config::reg_sink(false, 0, 0x24, value), "failed register read reported success");
        require(!hw.initialized, "register read failure did not reset the driver");
        check_released(state);
        hw.fail_register = false;
        require(wifi_config::reg_sink(false, 0, 0x24, value), "register read did not recover from a bus failure");
        require(value == 0x42, "recovered register read returned the wrong byte");
        check_released(state);
        hw.fail_register = true;
        require(!wifi_config::reg_sink(true, 0, 0x24, value), "failed register write reported success");
        require(!hw.initialized, "register write failure did not reset the driver");
        check_released(state);
        hw.fail_register = false;
        require(wifi_config::reg_sink(true, 0, 0x24, value), "register write did not recover from a bus failure");
    } else if (scenario == "register_recovery") {
        hw.fail_rgb_init = false;
        std::uint8_t value = 0;
        require(wifi_config::reg_sink(false, 0, 0x24, value), "register read did not recover failed boot init");
        require(value == 0x42, "register read returned the wrong byte");
    } else if (scenario == "serialized_requests") {
        std::promise<void> capture_entered;
        std::promise<void> capture_release;
        const auto release = capture_release.get_future().share();
        bool first_frame = true;
        hw.capture_hook = [&] {
            if (first_frame) {
                first_frame = false;
                capture_entered.set_value();
                release.wait();
            }
        };
        auto capture = std::async(std::launch::async, [&] { return frame.run(); });
        capture_entered.get_future().wait();
        std::promise<void> register_started;
        auto reg = std::async(std::launch::async, [&] {
            register_started.set_value();
            std::uint8_t value = 0;
            return wifi_config::reg_sink(false, 0, 0x24, value);
        });
        register_started.get_future().wait();
        const bool serialized = reg.wait_for(std::chrono::milliseconds(100)) == std::future_status::timeout;
        capture_release.set_value();
        require(capture.get(), "concurrent capture failed");
        require(reg.get(), "concurrent register request failed");
        require(serialized, "register request entered hardware while a capture owned the camera");
    } else {
        require(false, "unknown test scenario");
    }

    check_released(state);
    return EXIT_SUCCESS;
}
