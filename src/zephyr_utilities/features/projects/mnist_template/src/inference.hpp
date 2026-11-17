// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "assets.hpp"

namespace mnist {
inline constexpr size_t kArenaSize = 16 * 1024;

struct Result {
	float scores[kClasses];
	uint32_t duration_us;
	uint8_t prediction;
};

// The caller serializes access to the interpreter and arena.
bool Initialize();
bool Ready();
size_t ArenaUsed();
bool Infer(const uint8_t *pixels, Result &result);
} // namespace mnist
