"""Explicit, versioned sample adaptations for verified local board inputs."""

from copy import deepcopy
import hashlib
from pathlib import Path

from .probing import check_cancel


TARGETS = frozenset({"esp32c3_devkitc/esp32c3", "esp32c3_devkitm/esp32c3"})
_STRIP = "samples/drivers/led/led_strip/"
_OVERLAY = _STRIP + "boards/esp32c3_devkitm.overlay"
# These exact upstream inputs were reviewed together. A new input needs review,
# rather than inferring an addressable LED's wiring from a board-name substring.
_KNOWN_INPUTS = {
    "boards/espressif/esp32c3_devkitc/board.yml": "ee554086365368419e174d48411b94196124828334a2d3bb264739062da3c11c",
    "boards/espressif/esp32c3_devkitc/esp32c3_devkitc.dts": "2818c80baddc6056aa403d6d571dca63475e15df561e21a93701a8717aea8155",
    "boards/espressif/esp32c3_devkitc/esp32c3_devkitc-pinctrl.dtsi": "4082369437b0916b3bc7f0dc9b4124c13bb992af485d8641400244eacf062dbc",
    "boards/espressif/esp32c3_devkitm/board.yml": "0563ae6e69f777d24e76577f0cd03fa57fd10d5c39da41e0e961cd2d25760eda",
    "boards/espressif/esp32c3_devkitm/esp32c3_devkitm.dts": "b71e1156397a9c8a6dd21bdf631ddc525475c585f67856137713e92a0c267fc4",
    "boards/espressif/esp32c3_devkitm/esp32c3_devkitm-pinctrl.dtsi": "f57dc68255fe0b92e3543cab92b2586e8440be3bb85f6d64275f2d09b53039de",
    _STRIP + "src/main.c": "1a0018fe945e1c1065961613471338b8e44d4aabfc733487a74198c3fb46756b",
    _OVERLAY: "bb67d26ded180c9c03c80c65d47d64ceae857139cd0d2e9b314db7f7971bf397",
    "include/zephyr/drivers/led_strip.h": "9ad15d6564d84fdec760e9bbd44044fbdc55f1a26a94c2c4cb2ed2427cb206b2",
    "drivers/led_strip/Kconfig.ws2812": "2e972dc238d8fbd4f68525bb9200d61f1a1c4ea9be2255214d7f9825f370909b",
    "drivers/led_strip/ws2812_spi.c": "19b7de28d93109f92a91128e68c5fb00ba0fb636692492aa9bd43737e995271f",
    "dts/bindings/led_strip/worldsemi,ws2812-spi.yaml": "9c24083ef7cbbd1e8f3dbfcd1af06011ed9ca83d9531dedca9822a48c829f050",
}
_MAIN = '''/*
 * Copyright (c) 2017 Linaro Limited
 * Copyright (c) 2018 Intel Corporation
 * Copyright (c) 2024 TOKITA Hiroshi
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Zephyr Utilities adaptation: blink one GPIO8 addressable RGB LED.
 * Adapted from the local Zephyr LED strip sample; original evidence is in upstream/.
 */

#include <errno.h>
#include <stdbool.h>
#include <zephyr/device.h>
#include <zephyr/drivers/led_strip.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#define STRIP_NODE DT_ALIAS(led_strip)
BUILD_ASSERT(DT_PROP(STRIP_NODE, chain_length) == 1, "This adaptation needs one RGB LED");

static const struct device *const strip = DEVICE_DT_GET(STRIP_NODE);

int main(void)
{
    bool on = true;

    if (!device_is_ready(strip)) {
        printk("RGB LED device is not ready\\n");
        return -ENODEV;
    }

    while (1) {
        /* The driver may overwrite pixel data; initialize it for every update. */
        struct led_rgb pixel = { .r = on ? 16 : 0, .g = 0, .b = 0 };
        int error = led_strip_update_rgb(strip, &pixel, 1);

        if (error < 0) {
            printk("RGB LED update failed: %d\\n", error);
            return error;
        }
        on = !on;
        k_sleep(K_MSEC(500));
    }
}
'''
_CONFIG = ("# SPDX-License-Identifier: Apache-2.0\n"
           "# Zephyr Utilities adaptation: GPIO8 RGB LED via the local WS2812 SPI driver.\n"
           "CONFIG_LED_STRIP=y\nCONFIG_SPI=y\nCONFIG_WS2812_STRIP_SPI=y\nCONFIG_PRINTK=y\n")


def _sha(content):
    return hashlib.sha256(content.encode()).hexdigest()


def _profile_board(profile, zephyr):
    target = profile.get("target")
    board = target.split("/")[0]
    path = Path(zephyr["path"]) / "boards/espressif" / board / "board.yml"
    expected_id = hashlib.sha256(f"{path}:{board}".encode()).hexdigest()[:20]
    if profile.get("board_id") != expected_id or profile.get("zephyr", {}).get("path") != zephyr["path"]:
        raise ValueError("RGB Blinky requires the selected checkout's official ESP32-C3 board definition. Analyze again.")
    for kind in ("boards", "socs"):
        if any(item.get("path") != zephyr["path"] for item in profile.get("roots", {}).get(kind, [])):
            raise ValueError("RGB Blinky adaptation does not support custom board, SoC or devicetree roots.")
    modules = {f"module:{item.get('name')}": Path(item["path"])
               for item in profile.get("modules", [])
               if item.get("status") == "discovered" and item.get("is_module") and item.get("active") is True
               and isinstance(item.get("path"), str)}
    for entry in profile.get("roots", {}).get("dts", []):
        if entry.get("path") == zephyr["path"]:
            continue
        module = modules.get(entry.get("origin"))
        if (module is None or not isinstance(entry.get("path"), str) or
                ".." in Path(entry["path"]).parts or not Path(entry["path"]).is_relative_to(module)):
            raise ValueError("RGB Blinky adaptation only supports devicetree roots from discovered active modules.")
    return board


def adapt_blinky(contents, provenance, profile, read_input, *, cancel=None):
    """Return a reviewed generated variant, retaining the original copied evidence."""
    if (provenance["id"] != "basic/blinky" or not isinstance(profile, dict) or
            profile.get("target") not in TARGETS):
        return contents, provenance
    zephyr = provenance["zephyr"]
    target = profile["target"]
    board = _profile_board(profile, zephyr)
    inputs, input_contents = [], {}
    for path, expected in _KNOWN_INPUTS.items():
        if path.startswith("boards/") and not path.startswith(f"boards/espressif/{board}/"):
            continue
        check_cancel(cancel)
        try:
            content, evidence = read_input(path)
        except (OSError, ValueError) as exc:
            raise ValueError(f"RGB Blinky adaptation needs a supported local input: {path}. {exc}") from exc
        if _sha(content) != expected:
            raise ValueError(f"RGB Blinky adaptation does not support this changed local input: {path}. "
                             "A newer reviewed adapter is required; no files were created.")
        inputs.append({"path": path, "sha256": expected, **evidence})
        input_contents[path] = content

    generated = {}
    copies = []
    for path, content in contents.items():
        destination = path if path in {"LICENSE", "REUSE.toml", "NOTICE"} else f"upstream/basic-blinky/{path}"
        generated[destination] = content
        copies.append({"source": path, "path": destination, "sha256": _sha(content)})
    for path in (_STRIP + "src/main.c", _OVERLAY):
        destination = "upstream/led-strip/" + path.removeprefix(_STRIP)
        generated[destination] = input_contents[path]
        copies.append({"source": path, "path": destination, "sha256": _sha(input_contents[path])})

    generated["src/main.c"] = _MAIN
    generated["prj.conf"] = _CONFIG
    generated["app.overlay"] = ("/* Zephyr Utilities adaptation: GPIO8 RGB LED for " + target + ". */\n"
                                + input_contents[_OVERLAY])
    generated["CMakeLists.txt"] = (
        "# SPDX-License-Identifier: Apache-2.0\n"
        "# Zephyr Utilities RGB Blinky adaptation; upstream CMake is preserved in upstream/.\n"
        "cmake_minimum_required(VERSION 3.20.0)\n"
        "if(NOT DEFINED DTC_OVERLAY_FILE)\n"
        '  set(DTC_OVERLAY_FILE "${CMAKE_CURRENT_SOURCE_DIR}/app.overlay")\n'
        "endif()\n"
        "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n"
        f'if(NOT "${{BOARD}}/${{BOARD_QUALIFIERS}}" STREQUAL "{target}" OR BOARD_REVISION)\n'
        f'  message(FATAL_ERROR "This RGB Blinky adaptation is for {target}. '
        'Regenerate the sample for a different target.")\n'
        "endif()\n"
        f'if(NOT "${{BOARD_DIRECTORIES}}" STREQUAL "${{ZEPHYR_BASE}}/boards/espressif/{board}")\n'
        '  message(FATAL_ERROR "This RGB Blinky adaptation requires the official Zephyr board definition.")\n'
        "endif()\n"
        "project(rgb_blinky)\n"
        "target_sources(app PRIVATE src/main.c)\n")
    message = (f"{target} uses an addressable RGB LED on GPIO8, not a GPIO led0 device. "
               "This reviewed adaptation blinks it red for 500 ms, then off for 500 ms using Zephyr's LED strip driver.")
    generated["ADAPTATION.md"] = (
        "# ESP32-C3 RGB Blinky adaptation\n\n" + message + "\n\n"
        "Generated by Zephyr Utilities adapter `esp32c3-gpio8-rgb-blinky`, version 1.\n"
        "The root CMakeLists.txt, prj.conf, app.overlay and src/main.c are explicit\n"
        "adaptations, not an unchanged upstream Blinky sample. The upstream Blinky\n"
        "files are preserved byte for byte under upstream/basic-blinky/. The local\n"
        "LED strip source and DevKitM GPIO8 overlay are preserved under upstream/led-strip/.\n"
        "Only the generated root application is built; upstream/ is evidence.\n\n"
        "The overlay retains the local LED strip sample's WS2812 SPI timings and GRB\n"
        "color order. It routes SPI2 MOSI to GPIO8 and retains the board's other SPI2\n"
        "pins (GPIO2, GPIO6 and GPIO10); do not use SPI2 for another device at the same time.\n"
        "The generated code checks device readiness and reports driver errors on the console.\n\n"
        "Official wiring references:\n"
        "- https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32c3/esp32-c3-devkitc-02/user_guide.html\n"
        "- https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32c3/esp32-c3-devkitm-1/user_guide.html\n\n"
        "This adaptation supports only the exact reviewed local board and driver inputs\n"
        "recorded in zephyr-sample.json. It does not guess wiring for clones, revisions\n"
        "or custom board roots. A successful build is not a physical LED test.\n"
        "Use ZEPHYR-SAMPLE.md for standalone commands. To change target, generate a new\n"
        "application and review its hardware requirements. Keep LICENSE, REUSE.toml,\n"
        "any NOTICE and source copyright headers with redistributed copies.\n")
    adaptation = {
        "id": "esp32c3-gpio8-rgb-blinky", "version": 1, "title": "ESP32-C3 RGB Blinky",
        "message": message, "target": target, "inputs": inputs, "copies": copies,
        "environment_roots": deepcopy(profile.get("roots", {})),
        "files": [{"path": path, "sha256": _sha(content)} for path, content in sorted(generated.items())],
        "attribution": [{"path": "src/main.c", "identifier": "Apache-2.0", "source": _STRIP + "src/main.c"},
                        {"path": "app.overlay", "identifier": "Apache-2.0", "source": _OVERLAY}],
    }
    adapted = {**provenance, "schema_version": 2, "source_fingerprint": provenance["fingerprint"],
               "adaptation": adaptation, "requirements": [message, "Use the official, unmodified board and supported local driver inputs."]}
    return generated, adapted
