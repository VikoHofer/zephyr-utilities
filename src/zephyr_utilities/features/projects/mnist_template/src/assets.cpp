// SPDX-License-Identifier: Apache-2.0
#include "assets.hpp"

namespace mnist {
alignas(16) const unsigned char kModel[] = {
#include "model.inc"
};
const size_t kModelSize = sizeof(kModel);

const uint8_t kImages[kSampleCount * kPixels] = {
#include "images.inc"
};

const Sample kSamples[kSampleCount] = {
#include "test_samples.inc"
};
} // namespace mnist
