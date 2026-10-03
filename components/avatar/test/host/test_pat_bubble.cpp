// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

#include "pat_bubble.hpp"
#include "test_support.hpp"

int main() {
    CHECK(stackchan::app::pat_bubble_text() == "Pat pat☆");
    CHECK(stackchan::app::pat_bubble_text("zh") == "摸摸♡");
    CHECK(stackchan::app::pat_bubble_text("en") == "Pat pat☆");
    CHECK(stackchan::app::pat_bubble_text("") == "Pat pat☆");
    CHECK(stackchan::app::pat_bubble_text("invalid") == "Pat pat☆");
    return avtest::finish("pat_bubble");
}
