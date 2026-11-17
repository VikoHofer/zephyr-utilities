// SPDX-License-Identifier: Apache-2.0
#include "assets.hpp"
#include "inference.hpp"
#include "model_metadata.hpp"

#include <cerrno>
#include <cmath>
#include <cstring>
#include <zephyr/kernel.h>
#include <zephyr/shell/shell.h>
#include <zephyr/sys/crc.h>

namespace {
K_MUTEX_DEFINE(model_mutex);
constexpr float kReferenceTolerance = 0.001f;
uint8_t upload_pixels[mnist::kPixels];
uint32_t upload_rows;
bool upload_active;
bool upload_verified;
constexpr uint32_t kAllRows = (1U << mnist::kWidth) - 1U;

class ModelLock {
public:
	ModelLock() { k_mutex_lock(&model_mutex, K_FOREVER); }
	~ModelLock() { k_mutex_unlock(&model_mutex); }
	ModelLock(const ModelLock &) = delete;
	ModelLock &operator=(const ModelLock &) = delete;
};

bool ParseNumber(const char *text, uint32_t maximum, uint32_t &value)
{
	value = 0;
	if (text == nullptr || *text == '\0') {
		return false;
	}
	for (; *text != '\0'; ++text) {
		if (*text < '0' || *text > '9') {
			return false;
		}
		const uint32_t digit = *text - '0';
		if (value > maximum / 10 ||
		    (value == maximum / 10 && digit > maximum % 10)) {
			return false;
		}
		value = value * 10 + digit;
	}
	return true;
}

bool ReadId(const shell *sh, const char *text, uint32_t &id)
{
	if (!ParseNumber(text, mnist::kSampleCount - 1, id)) {
		shell_error(sh, "ERROR: image ID must be 0..%u", unsigned(mnist::kSampleCount - 1));
		return false;
	}
	return true;
}

bool RequireReady(const shell *sh)
{
	if (!mnist::Ready()) {
		shell_error(sh, "ERROR: model is unavailable; check startup messages");
		return false;
	}
	return true;
}

void PrintPixels(const shell *sh, const uint8_t *pixels)
{
	constexpr char ramp[] = " .:-=+*#%@";
	char border[mnist::kWidth * 2 + 3];
	memset(border, '-', sizeof(border) - 1);
	border[0] = '+';
	border[sizeof(border) - 2] = '+';
	border[sizeof(border) - 1] = '\0';
	shell_print(sh, "%s", border);
	for (size_t y = 0; y < mnist::kWidth; ++y) {
		char row[mnist::kWidth * 2 + 3];
		row[0] = '|';
		for (size_t x = 0; x < mnist::kWidth; ++x) {
			const char shade = ramp[pixels[y * mnist::kWidth + x] * (sizeof(ramp) - 2) / 255];
			row[1 + x * 2] = shade;
			row[2 + x * 2] = shade;
		}
		row[sizeof(row) - 2] = '|';
		row[sizeof(row) - 1] = '\0';
		shell_print(sh, "%s", row);
	}
	shell_print(sh, "%s", border);
}

void PrintImage(const shell *sh, uint32_t id)
{
	const auto &sample = mnist::kSamples[id];
	shell_print(sh, "Image %u: MNIST test index %u, label %u (28x28)", id,
		    unsigned(sample.source_index), unsigned(sample.label));
	PrintPixels(sh, mnist::Image(id));
}

void PrintResult(const shell *sh, uint32_t id, const mnist::Result &result)
{
	const auto &sample = mnist::kSamples[id];
	shell_print(sh, "Image %u: prediction=%u label=%u %s", id, unsigned(result.prediction),
		    unsigned(sample.label), result.prediction == sample.label ? "CORRECT" : "WRONG");
	shell_print(sh, "Inference (Invoke only): %lu us", static_cast<unsigned long>(result.duration_us));
	shell_print(sh, "Digit   Softmax score (not calibrated confidence)");
	for (size_t digit = 0; digit < mnist::kClasses; ++digit) {
		char bar[33];
		memset(bar, ' ', sizeof(bar) - 1);
		const size_t filled = static_cast<size_t>(result.scores[digit] * 32.0f + 0.5f);
		memset(bar, '#', filled);
		bar[sizeof(bar) - 1] = '\0';
		shell_print(sh, "%c %u   %.6f (%6.2f%%) |%s|",
			    digit == result.prediction ? '>' : ' ', unsigned(digit),
			    double(result.scores[digit]), double(result.scores[digit] * 100.0f), bar);
	}
}

int RunAndPrint(const shell *sh, uint32_t id)
{
	mnist::Result result;
	if (!mnist::Infer(mnist::Image(id), result)) {
		shell_error(sh, "ERROR: inference failed");
		return -EIO;
	}
	PrintResult(sh, id, result);
	return 0;
}

int CmdInfo(const shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);
	ModelLock lock;
	shell_print(sh, "MNIST / TensorFlow Lite Micro on %s", CONFIG_BOARD_TARGET);
	shell_print(sh, "State: %s", mnist::Ready() ? "ready" : "FAILED");
	shell_print(sh, "Network: Float32 784 -> 64 ReLU -> 10 Softmax");
	shell_print(sh, "Input: 28x28 grayscale, row-major, pixel / 255.0");
	shell_print(sh, "Model: %u bytes in flash", unsigned(mnist::kModelSize));
	shell_print(sh, "Model SHA256: %s", mnist::kModelSha256);
	shell_print(sh, "Tensor arena: %u / %u bytes used/reserved", unsigned(mnist::ArenaUsed()),
		    unsigned(mnist::kArenaSize));
	shell_print(sh, "Test images: %u (%u bytes in flash), three per digit",
		    unsigned(mnist::kSampleCount), unsigned(sizeof(mnist::kImages)));
	shell_print(sh, "Host accuracy on all 10000 MNIST test images: %.2f%%",
		    double(mnist::kTestAccuracy * 100.0f));
	shell_print(sh, "Timing: Invoke only; no preprocessing or console output");
	shell_print(sh, "Upload protocol: mnist-gray8-v1 (28 rows, CRC32-IEEE)");
#if defined(CONFIG_ARCH_POSIX)
	shell_print(sh, "Simulator clock: timing is not an H7 performance measurement");
#endif
	return mnist::Ready() ? 0 : -EIO;
}

int CmdList(const shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);
	shell_print(sh, "ID  Label  MNIST test index");
	for (size_t id = 0; id < mnist::kSampleCount; ++id) {
		const auto &sample = mnist::kSamples[id];
		shell_print(sh, "%2u    %u       %u", unsigned(id), unsigned(sample.label),
			    unsigned(sample.source_index));
	}
	return 0;
}

int CmdShow(const shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	uint32_t id;
	if (!ReadId(sh, argv[1], id)) {
		return -EINVAL;
	}
	PrintImage(sh, id);
	return 0;
}

int CmdInfer(const shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	uint32_t id;
	if (!ReadId(sh, argv[1], id)) {
		return -EINVAL;
	}
	ModelLock lock;
	return RequireReady(sh) ? RunAndPrint(sh, id) : -EIO;
}

int CmdDemo(const shell *sh, size_t argc, char **argv)
{
	uint32_t id = 0;
	if (argc == 2 && !ReadId(sh, argv[1], id)) {
		return -EINVAL;
	}
	ModelLock lock;
	if (!RequireReady(sh)) {
		return -EIO;
	}
	PrintImage(sh, id);
	return RunAndPrint(sh, id);
}

int CmdTest(const shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);
	ModelLock lock;
	if (!RequireReady(sh)) {
		return -EIO;
	}
	unsigned passed = 0;
	unsigned correct = 0;
	float maximum_delta = 0.0f;
	for (size_t id = 0; id < mnist::kSampleCount; ++id) {
		mnist::Result result;
		if (!mnist::Infer(mnist::Image(id), result)) {
			shell_error(sh, "SELFTEST FAIL: inference on image %u", unsigned(id));
			return -EIO;
		}
		const auto &sample = mnist::kSamples[id];
		float delta = 0.0f;
		for (size_t digit = 0; digit < mnist::kClasses; ++digit) {
			const float difference = std::fabs(result.scores[digit] - sample.reference_scores[digit]);
			if (difference > delta) {
				delta = difference;
			}
		}
		const bool match = delta <= kReferenceTolerance &&
				   result.prediction == sample.reference_prediction;
		passed += match;
		correct += result.prediction == sample.label;
		if (delta > maximum_delta) {
			maximum_delta = delta;
		}
		shell_print(sh, "REF %02u %s: prediction=%u label=%u max_delta=%.8f invoke=%lu us",
			    unsigned(id), match ? "PASS" : "FAIL", unsigned(result.prediction),
			    unsigned(sample.label), double(delta), static_cast<unsigned long>(result.duration_us));
	}
	shell_print(sh, "SELFTEST %s: %u/%u references; labels %u/%u; max_delta=%.8f",
		    passed == mnist::kSampleCount ? "PASS" : "FAIL", passed,
		    unsigned(mnist::kSampleCount), correct, unsigned(mnist::kSampleCount), double(maximum_delta));
	return passed == mnist::kSampleCount ? 0 : -EIO;
}

int CmdBenchmark(const shell *sh, size_t argc, char **argv)
{
	uint32_t runs = 100;
	if (argc == 2 && (!ParseNumber(argv[1], 1000, runs) || runs == 0)) {
		shell_error(sh, "ERROR: runs must be 1..1000");
		return -EINVAL;
	}
	ModelLock lock;
	if (!RequireReady(sh)) {
		return -EIO;
	}
	mnist::Result result;
	if (!mnist::Infer(mnist::Image(0), result)) {
		shell_error(sh, "ERROR: benchmark warmup failed");
		return -EIO;
	}
	uint32_t minimum = UINT32_MAX;
	uint32_t maximum = 0;
	uint64_t total = 0;
	for (uint32_t i = 0; i < runs; ++i) {
		if (!mnist::Infer(mnist::Image(i % mnist::kSampleCount), result)) {
			shell_error(sh, "ERROR: benchmark inference failed");
			return -EIO;
		}
		if (result.duration_us < minimum) {
			minimum = result.duration_us;
		}
		if (result.duration_us > maximum) {
			maximum = result.duration_us;
		}
		total += result.duration_us;
	}
	shell_print(sh, "Benchmark: %u runs, cycling %u images; one warmup excluded", runs,
		    unsigned(mnist::kSampleCount));
	shell_print(sh, "Invoke only: min=%lu us mean=%.2f us max=%lu us",
		    static_cast<unsigned long>(minimum), double(total) / runs,
		    static_cast<unsigned long>(maximum));
#if defined(CONFIG_ARCH_POSIX)
	shell_print(sh, "Simulator clock: timing is not an H7 performance measurement");
#endif
	return 0;
}

int HexDigit(char value)
{
	if (value >= '0' && value <= '9') {
		return value - '0';
	}
	if (value >= 'a' && value <= 'f') {
		return value - 'a' + 10;
	}
	if (value >= 'A' && value <= 'F') {
		return value - 'A' + 10;
	}
	return -1;
}

int CmdUploadBegin(const shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);
	ModelLock lock;
	memset(upload_pixels, 0, sizeof(upload_pixels));
	upload_rows = 0;
	upload_active = true;
	upload_verified = false;
	shell_print(sh, "UPLOAD READY mnist-gray8-v1 28x28");
	return 0;
}

int CmdUploadRow(const shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	uint32_t row;
	if (!ParseNumber(argv[1], mnist::kWidth - 1, row) || strlen(argv[2]) != mnist::kWidth * 2) {
		shell_error(sh, "ERROR: row requires index 0..27 and exactly 56 hex characters");
		return -EINVAL;
	}
	uint8_t decoded[mnist::kWidth];
	for (size_t x = 0; x < mnist::kWidth; ++x) {
		const int high = HexDigit(argv[2][x * 2]);
		const int low = HexDigit(argv[2][x * 2 + 1]);
		if (high < 0 || low < 0) {
			shell_error(sh, "ERROR: invalid hex data");
			return -EINVAL;
		}
		decoded[x] = static_cast<uint8_t>((high << 4) | low);
	}
	ModelLock lock;
	if (!upload_active) {
		shell_error(sh, "ERROR: start with ai upload begin");
		return -EINVAL;
	}
	memcpy(&upload_pixels[row * mnist::kWidth], decoded, sizeof(decoded));
	upload_rows |= 1U << row;
	upload_verified = false;
	shell_print(sh, "UPLOAD ROW %u OK", row);
	return 0;
}

int CmdUploadInfer(const shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	if (strlen(argv[1]) != 8) {
		shell_error(sh, "ERROR: expected eight CRC32 hex characters");
		return -EINVAL;
	}
	uint32_t expected_crc = 0;
	for (size_t i = 0; i < 8; ++i) {
		const int value = HexDigit(argv[1][i]);
		if (value < 0) {
			shell_error(sh, "ERROR: invalid CRC32 hex data");
			return -EINVAL;
		}
		expected_crc = (expected_crc << 4) | static_cast<uint32_t>(value);
	}
	ModelLock lock;
	upload_verified = false;
	if (!upload_active || upload_rows != kAllRows) {
		shell_error(sh, "ERROR: upload incomplete; all 28 rows are required");
		return -EINVAL;
	}
	const uint32_t actual_crc = crc32_ieee(upload_pixels, sizeof(upload_pixels));
	if (actual_crc != expected_crc) {
		shell_error(sh, "ERROR: CRC32 mismatch (received %08x expected %08x)",
			    actual_crc, expected_crc);
		return -EINVAL;
	}
	if (!RequireReady(sh)) {
		return -EIO;
	}
	mnist::Result result;
	if (!mnist::Infer(upload_pixels, result)) {
		shell_error(sh, "ERROR: uploaded image inference failed");
		return -EIO;
	}
	upload_verified = true;
	shell_fprintf(sh, SHELL_NORMAL,
		      "MNIST_RESULT {\"prediction\":%u,\"inference_us\":%u,\"crc32\":\"%08x\",\"scores\":[",
		      unsigned(result.prediction), result.duration_us, actual_crc);
	for (size_t digit = 0; digit < mnist::kClasses; ++digit) {
		shell_fprintf(sh, SHELL_NORMAL, "%s%.9f", digit == 0 ? "" : ",", double(result.scores[digit]));
	}
	shell_print(sh, "]}");
	return 0;
}

int CmdUploadShow(const shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);
	ModelLock lock;
	if (!upload_verified) {
		shell_error(sh, "ERROR: upload and verify an image first");
		return -EINVAL;
	}
	shell_print(sh, "Uploaded image (28x28)");
	PrintPixels(sh, upload_pixels);
	return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(upload_commands,
	SHELL_CMD_ARG(begin, NULL, "Clear upload and start a new 28x28 image", CmdUploadBegin, 1, 0),
	SHELL_CMD_ARG(row, NULL, "row <0..27> <56 hex characters>", CmdUploadRow, 3, 0),
	SHELL_CMD_ARG(infer, NULL, "infer <CRC32 hex>: verify image and run inference", CmdUploadInfer, 2, 0),
	SHELL_CMD_ARG(show, NULL, "ASCII view of the last verified upload", CmdUploadShow, 1, 0),
	SHELL_SUBCMD_SET_END
);

SHELL_STATIC_SUBCMD_SET_CREATE(ai_commands,
	SHELL_CMD_ARG(info, NULL, "Model, memory and dataset information", CmdInfo, 1, 0),
	SHELL_CMD_ARG(list, NULL, "List embedded test images", CmdList, 1, 0),
	SHELL_CMD_ARG(show, NULL, "show <id>: ASCII image, IDs 0..29", CmdShow, 2, 0),
	SHELL_CMD_ARG(infer, NULL, "infer <id>: ten scores and inference time", CmdInfer, 2, 0),
	SHELL_CMD_ARG(demo, NULL, "demo [id]: ASCII image and inference (default 0)", CmdDemo, 1, 1),
	SHELL_CMD_ARG(test, NULL, "Compare all 30 images with host reference scores", CmdTest, 1, 0),
	SHELL_CMD_ARG(benchmark, NULL, "benchmark [runs]: 1..1000, default 100", CmdBenchmark, 1, 1),
	SHELL_CMD(upload, &upload_commands, "Upload your own 28x28 grayscale image", NULL),
	SHELL_SUBCMD_SET_END
);
SHELL_CMD_REGISTER(ai, &ai_commands, "MNIST digit recognition using TensorFlow Lite Micro", NULL);
} // namespace

int main()
{
	ModelLock lock;
	if (!mnist::Initialize()) {
		printk("MNIST startup FAILED.\n");
		return -EIO;
	}
	printk("\nMNIST ready on %s. No sensors required.\n", CONFIG_BOARD_TARGET);
	printk("Try: ai demo 0 | ai list | ai test | ai benchmark 100\n");
	return 0;
}
