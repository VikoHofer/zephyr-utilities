// SPDX-License-Identifier: Apache-2.0
#include "inference.hpp"

#include <cmath>
#include <cstring>
#include <flatbuffers/verifier.h>
#include <tensorflow/lite/micro/micro_interpreter.h>
#include <tensorflow/lite/micro/micro_mutable_op_resolver.h>
#include <tensorflow/lite/schema/schema_generated.h>
#include <zephyr/kernel.h>

namespace mnist {
namespace {
alignas(16) uint8_t arena[kArenaSize];
tflite::MicroInterpreter *interpreter;
TfLiteTensor *input;
TfLiteTensor *output;
bool ready;

bool ValidTensor(const TfLiteTensor *tensor, int width)
{
	return tensor != nullptr && tensor->type == kTfLiteFloat32 &&
	       tensor->data.f != nullptr && tensor->dims != nullptr &&
	       tensor->dims->size == 2 && tensor->dims->data[0] == 1 &&
	       tensor->dims->data[1] == width && tensor->bytes == width * sizeof(float);
}
} // namespace

bool Initialize()
{
	if (ready) {
		return true;
	}
	flatbuffers::Verifier verifier(kModel, kModelSize);
	if (!tflite::VerifyModelBuffer(verifier)) {
		printk("MNIST: invalid model FlatBuffer\n");
		return false;
	}
	const tflite::Model *model = tflite::GetModel(kModel);
	if (model->version() != TFLITE_SCHEMA_VERSION || model->subgraphs() == nullptr ||
	    model->subgraphs()->size() != 1 || model->subgraphs()->Get(0)->inputs() == nullptr ||
	    model->subgraphs()->Get(0)->inputs()->size() != 1 ||
	    model->subgraphs()->Get(0)->outputs() == nullptr ||
	    model->subgraphs()->Get(0)->outputs()->size() != 1) {
		printk("MNIST: unsupported model schema or graph\n");
		return false;
	}
	static tflite::MicroMutableOpResolver<2> resolver;
	if (resolver.AddFullyConnected() != kTfLiteOk || resolver.AddSoftmax() != kTfLiteOk) {
		printk("MNIST: operator registration failed\n");
		return false;
	}
	static tflite::MicroInterpreter instance(model, resolver, arena, sizeof(arena));
	interpreter = &instance;
	if (interpreter->AllocateTensors() != kTfLiteOk) {
		printk("MNIST: tensor allocation failed\n");
		return false;
	}
	input = interpreter->input(0);
	output = interpreter->output(0);
	if (!ValidTensor(input, kPixels) || !ValidTensor(output, kClasses)) {
		printk("MNIST: expected Float32 input [1,784] and output [1,10]\n");
		return false;
	}
	ready = true;
	return true;
}

bool Ready()
{
	return ready;
}

size_t ArenaUsed()
{
	return ready ? interpreter->arena_used_bytes() : 0;
}

bool Infer(const uint8_t *pixels, Result &result)
{
	result = {};
	if (!ready || pixels == nullptr) {
		return false;
	}
	for (size_t i = 0; i < kPixels; ++i) {
		input->data.f[i] = static_cast<float>(pixels[i]) / 255.0f;
	}
	// Measure Invoke only: no ASCII, UART output or input normalization included.
	const uint32_t start = k_cycle_get_32();
	const TfLiteStatus status = interpreter->Invoke();
	const uint32_t cycles = k_cycle_get_32() - start;
	result.duration_us = static_cast<uint32_t>(k_cyc_to_us_floor64(cycles));
	if (status != kTfLiteOk) {
		return false;
	}
	float sum = 0.0f;
	for (size_t digit = 0; digit < kClasses; ++digit) {
		const float score = output->data.f[digit];
		if (!std::isfinite(score) || score < 0.0f || score > 1.0f) {
			return false;
		}
		result.scores[digit] = score;
		sum += score;
		if (score > result.scores[result.prediction]) {
			result.prediction = digit;
		}
	}
	return sum > 0.999f && sum < 1.001f;
}
} // namespace mnist
