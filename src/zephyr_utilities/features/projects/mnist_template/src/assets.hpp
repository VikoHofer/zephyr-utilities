// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstddef>
#include <cstdint>

namespace mnist {
inline constexpr size_t kWidth = 28;
inline constexpr size_t kPixels = kWidth * kWidth;
inline constexpr size_t kClasses = 10;
inline constexpr size_t kSampleCount = 30;

struct Sample {
	uint32_t source_index;
	uint8_t label;
	uint8_t reference_prediction;
	float reference_scores[kClasses];
};

extern const unsigned char kModel[];
extern const size_t kModelSize;
extern const uint8_t kImages[kSampleCount * kPixels];
extern const Sample kSamples[kSampleCount];

inline const uint8_t *Image(size_t id)
{
	return &kImages[id * kPixels];
}
} // namespace mnist
