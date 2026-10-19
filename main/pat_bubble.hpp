// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

#pragma once

#include <string_view>

namespace stackchan::app {

// efontCN_12/16 include U+2606 (star), but not U+2661 (heart).
// Keep the existing Chinese text; use the supported symbol for English.
constexpr std::string_view pat_bubble_text(std::string_view language = {}) {
    return language == "zh" ? "摸摸♡" : "Pat pat☆";
}

} // namespace stackchan::app
