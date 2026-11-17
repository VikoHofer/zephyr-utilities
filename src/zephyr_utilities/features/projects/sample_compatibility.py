"""Explicit, temporary configuration checks for the supported sample requirements."""

from collections import deque
from concurrent.futures import CancelledError
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
from tempfile import TemporaryDirectory

from .builds import execute_build
from .creation import path_identity
from .overlay_usage import overlay_usage
from .probing import check_cancel, run_probe


_MAX_FILES = 128
_MAX_FILE_BYTES = 1024 * 1024
_MAX_TOTAL_BYTES = 4 * 1024 * 1024
_LIMITATION = "Configuration checked; compilation and hardware operation were not tested."
_MNIST_ID = "tinyml/mnist-shell"
_MNIST_SOURCES = ("src/main.cpp", "src/inference.cpp", "src/assets.cpp")
_MNIST_INPUTS = {*_MNIST_SOURCES, "src/inference.hpp", "src/assets.hpp", "model/model_metadata.hpp",
                 "model/metadata.json", "model/model.inc", "data/images.inc", "data/test_samples.inc"}
_MNIST_CONFIG = ("CONFIG_CPP", "CONFIG_STD_CPP17", "CONFIG_TENSORFLOW_LITE_MICRO", "CONFIG_CRC", "CONFIG_CONSOLE",
                 "CONFIG_SERIAL", "CONFIG_UART_CONSOLE", "CONFIG_SHELL", "CONFIG_SHELL_BACKEND_SERIAL",
                 "CONFIG_CBPRINTF_FP_SUPPORT")

# Use the selected checkout's public dtlib API in the selected environment. Only
# bounded JSON facts cross this process boundary; never deserialize edt.pickle.
_DT_PROBE = r'''
import json
import sys
sys.path.insert(0, sys.argv[1])
from devicetree import dtlib
tree = dtlib.DT(sys.argv[2])

def strings(node, name):
    prop = node.props.get(name)
    return prop.to_strings() if prop is not None else []

def number(node, name):
    prop = node.props.get(name)
    return prop.to_num() if prop is not None else None

def enabled(node):
    while node is not None:
        status = node.props.get("status")
        if status is not None and status.to_string() not in ("okay", "ok"):
            return False
        node = node.parent
    return True

def gpio(node):
    prop = node.props.get("gpios")
    if prop is None:
        return None
    raw = prop.value
    if len(raw) < 4 or len(raw) % 4:
        return {"valid": False, "enabled": False}
    cells = [int.from_bytes(raw[i:i + 4], "big") for i in range(0, len(raw), 4)]
    controller = tree.phandle2node.get(cells[0])
    if controller is None or "gpio-controller" not in controller.props:
        return {"valid": False, "enabled": False}
    count = number(controller, "#gpio-cells")
    return {"valid": isinstance(count, int) and count > 0 and len(cells) == count + 1,
            "enabled": enabled(controller), "controller": controller.path}

def describe(node):
    return {"path": node.path, "enabled": enabled(node),
            "compatible": strings(node, "compatible"), "gpios": gpio(node),
            "chain_length": number(node, "chain-length"), "code": number(node, "zephyr,code")}

keys = []
for count, node in enumerate(tree.node_iter()):
    if count >= 10000:
        raise ValueError("Resolved Device Tree exceeds the node inspection limit.")
    if enabled(node) and "gpio-keys" in strings(node, "compatible"):
        for child in node.nodes.values():
            if enabled(child):
                keys.append(describe(child))
                if len(keys) > 128:
                    raise ValueError("Resolved Device Tree exceeds the button inspection limit.")
print(json.dumps({"schema_version": 1,
    "aliases": {name: describe(tree.alias2node[name]) if name in tree.alias2node else None
                for name in ("led0", "sw0", "led-strip")}, "gpio_keys": keys}))
'''


def _result(status, message, details=()):
    return {"status": status, "message": message, "details": [str(item)[:1800] for item in details][:8]}


def _validated_files(files, *, complete=True, sample_id=None):
    if not isinstance(files, list) or not 1 <= len(files) <= _MAX_FILES:
        raise ValueError("A sample check needs between 1 and 128 reviewed files.")
    contents, total = {}, 0
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
            raise ValueError("Sample check files need a relative path and text content.")
        name, content = item["path"], item["content"]
        path = PurePosixPath(name)
        if (not name or len(name) > 512 or path.is_absolute() or str(path) != name or len(path.parts) > 8 or
                any(part in {".", "..", ".git", "build"} or not re.fullmatch(r"[A-Za-z0-9_.+-]+", part)
                    for part in path.parts)):
            raise ValueError("Sample check files must use bounded relative paths without traversal.")
        size = len(content.encode("utf-8"))
        total += size
        if size > _MAX_FILE_BYTES or total > _MAX_TOTAL_BYTES:
            raise ValueError("Sample check files exceed the 1 MiB file or 4 MiB total limit.")
        if name in contents:
            raise ValueError("Sample check files contain duplicate paths.")
        contents[name] = content
    for name in contents:
        if any(str(parent) in contents for parent in PurePosixPath(name).parents):
            raise ValueError("Sample check files contain conflicting file and directory paths.")
    if sample_id == _MNIST_ID:
        if missing := _MNIST_INPUTS - contents.keys():
            raise ValueError("MNIST requirements need the complete reviewed runtime inputs: " + ", ".join(sorted(missing)) + ".")
    elif "src/main.c" not in contents:
        raise ValueError("Sample requirements need a readable src/main.c.")
    if complete and not {"CMakeLists.txt", "prj.conf"}.issubset(contents):
        raise ValueError("Sample check needs CMakeLists.txt, prj.conf and the reviewed application sources.")
    return contents


def _kind(contents, sample_id):
    if sample_id == _MNIST_ID:
        main = re.sub(r"/\*.*?\*/|//[^\n]*", "", contents["src/main.cpp"], flags=re.DOTALL)
        inference = re.sub(r"/\*.*?\*/|//[^\n]*", "", contents["src/inference.cpp"], flags=re.DOTALL)
        if ("SHELL_CMD_REGISTER(ai," in main and "mnist::Initialize()" in main and
                "tflite::MicroInterpreter" in inference and "AddFullyConnected()" in inference and "AddSoftmax()" in inference):
            return "mnist-shell"
        return None
    main = re.sub(r"/\*.*?\*/|//[^\n]*", "", contents["src/main.c"], flags=re.DOTALL)
    if sample_id == "hello_world":
        return "hello"
    if sample_id == "basic/blinky":
        if re.search(r"DT_ALIAS\s*\(\s*led_strip\s*\)", main) and "led_strip_update_rgb" in main:
            return "strip"
        if re.search(r"DT_ALIAS\s*\(\s*led0\s*\)", main) and "GPIO_DT_SPEC_GET" in main:
            return "gpio-led"
    if sample_id == "basic/button":
        if "INPUT_CALLBACK_DEFINE" in main and "zephyr/input/input.h" in main:
            return "input-button"
        if re.search(r"DT_ALIAS\s*\(\s*sw0\s*\)", main) and "gpio_pin_interrupt_configure" in main:
            return "gpio-button"
    return None


def _read_generated(build_dir, name, limit, *, in_zephyr=True):
    """Read bounded regular generated text, rejecting redirected output paths."""
    path_identity(build_dir)
    directory = os.open(build_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if in_zephyr:
            nested = os.open("zephyr", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = nested
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError(f"Generated {name} is not bounded regular text.")
        content = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(content) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError(f"Generated {name} changed during inspection or exceeded its size limit.")
    return content.decode("utf-8")


def _compiled_sources(build_dir, source_dir, sources=("src/main.c",)):
    """Require every reviewed entrypoint to be an input of the configured app target."""
    if source_dir is None:
        raise ValueError("The application source directory was not supplied for compile-command verification.")
    source_dir = Path(source_dir)
    path_identity(source_dir)
    expected = {source_dir / name for name in sources}
    found = set()
    entries = json.loads(_read_generated(build_dir, "compile_commands.json", 8 * 1024 * 1024, in_zephyr=False))
    if not isinstance(entries, list) or len(entries) > 10000:
        raise ValueError("Generated compile commands are invalid or exceed the inspection limit.")

    def relative_to_directory(value, directory):
        if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
            raise ValueError("Generated compile commands contain an invalid path.")
        path = Path(value)
        return Path(os.path.normpath(path if path.is_absolute() else directory / path))

    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Generated compile commands contain an invalid entry.")
        directory = relative_to_directory(entry.get("directory"), build_dir)
        source = relative_to_directory(entry.get("file"), directory)
        if source not in expected:
            continue
        output = entry.get("output")
        if output is None:
            args = entry.get("arguments")
            if args is None:
                command = entry.get("command")
                if not isinstance(command, str):
                    raise ValueError("The sample compile command has no recognizable output.")
                args = shlex.split(command)
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise ValueError("The sample compile command has invalid arguments.")
            indices = [index for index, arg in enumerate(args) if arg == "-o"]
            if len(indices) != 1 or indices[0] + 1 >= len(args):
                raise ValueError("The sample compile command has no recognizable output.")
            output = args[indices[0] + 1]
        output = relative_to_directory(output, directory)
        if output.is_relative_to(build_dir):
            parts = output.relative_to(build_dir).parts
            if any(parts[index:index + 2] == ("CMakeFiles", "app.dir") for index in range(len(parts) - 2)):
                found.add(source)
                if found == expected:
                    return
    missing = ", ".join(str(path.relative_to(source_dir)) for path in sorted(expected - found))
    raise ValueError(f"The configured app does not compile this sample's {missing}; its requirements cannot be applied.")


def _facts(value):
    data = json.loads(value)
    if (not isinstance(data, dict) or data.get("schema_version") != 1 or
            not isinstance(data.get("aliases"), dict) or not isinstance(data.get("gpio_keys"), list) or
            len(data["gpio_keys"]) > 128 or set(data["aliases"]) != {"led0", "sw0", "led-strip"}):
        raise ValueError("Device Tree probe returned an unsupported result.")
    for node in [*data["aliases"].values(), *data["gpio_keys"]]:
        if node is None:
            continue
        if (not isinstance(node, dict) or not isinstance(node.get("path"), str) or
                not isinstance(node.get("enabled"), bool) or not isinstance(node.get("compatible"), list) or
                not all(isinstance(item, str) for item in node["compatible"]) or
                any(node.get(key) is not None and type(node[key]) is not int for key in ("chain_length", "code"))):
            raise ValueError("Device Tree probe returned invalid node evidence.")
        gpio = node.get("gpios")
        if gpio is not None and (not isinstance(gpio, dict) or not isinstance(gpio.get("valid"), bool) or
                                 not isinstance(gpio.get("enabled"), bool)):
            raise ValueError("Device Tree probe returned invalid GPIO evidence.")
    if any(node is None for node in data["gpio_keys"]):
        raise ValueError("Device Tree probe returned invalid button evidence.")
    return data


def _gpio_problem(node):
    gpio = node.get("gpios")
    if not gpio or not gpio["valid"]:
        return "a valid single GPIO specifier"
    if not gpio["enabled"]:
        return "an enabled GPIO controller"
    return None


def _evaluate(kind, facts, config):
    def incompatible(message):
        return _result("incompatible", message, ["Checked the configured Device Tree and Kconfig."])

    def missing_config(names):
        return [name for name in names if name not in config]

    if kind == "hello":
        return _result("compatible", "Hello World configured successfully; it needs no extra sample peripheral.", [_LIMITATION])
    if kind == "mnist-shell":
        if missing := missing_config(_MNIST_CONFIG):
            return _result("incompatible", "The MNIST shell demo requires enabled Kconfig options: " + ", ".join(missing) + ".",
                           ["Checked configured C++ application inputs and Kconfig."])
        return _result("compatible", "MNIST C++ sources, TensorFlow Lite Micro and the serial shell are configured.",
                       ["No sensor, LED or button aliases are required.", _LIMITATION])
    if kind in {"gpio-led", "gpio-button", "strip"}:
        alias = {"gpio-led": "led0", "gpio-button": "sw0", "strip": "led-strip"}[kind]
        node = facts["aliases"][alias]
        if node is None:
            return incompatible(f"This sample requires the '{alias}' Device Tree alias, which this configuration does not provide.")
        if not node["enabled"]:
            return incompatible(f"The '{alias}' Device Tree alias points to disabled hardware.")
        required = ["CONFIG_GPIO"]
        if kind == "strip":
            if node["chain_length"] != 1:
                return incompatible("This RGB Blinky adaptation requires led-strip chain-length = <1>.")
            drivers = {"worldsemi,ws2812-spi": ["CONFIG_WS2812_STRIP_SPI", "CONFIG_SPI"],
                       "worldsemi,ws2812-i2s": ["CONFIG_WS2812_STRIP_I2S", "CONFIG_I2S"],
                       "worldsemi,ws2812-gpio": ["CONFIG_WS2812_STRIP_GPIO", "CONFIG_GPIO"]}
            driver = next((name for name in node["compatible"] if name in drivers), None)
            if driver is None:
                return _result("unknown", "The configured LED strip driver is not supported by this compatibility checker.",
                               [node["path"], _LIMITATION])
            required = ["CONFIG_LED_STRIP", *drivers[driver]]
            needs_gpio = driver == "worldsemi,ws2812-gpio"
        else:
            needs_gpio = True
        if needs_gpio and (problem := _gpio_problem(node)):
            return incompatible(f"The '{alias}' Device Tree node requires {problem}.")
        if missing := missing_config(required):
            return incompatible("The sample requires enabled Kconfig options: " + ", ".join(missing) + ".")
        return _result("compatible", "Configured sample peripheral requirements are present.",
                       [f"Alias {alias}: {node['path']}", _LIMITATION])
    if "CONFIG_INPUT" not in config:
        return incompatible("This Button sample requires CONFIG_INPUT=y.")
    keys = facts["gpio_keys"]
    if keys and "CONFIG_INPUT_GPIO_KEYS" in config:
        for node in keys:
            if node["code"] is None:
                return incompatible(f"The enabled GPIO button {node['path']} needs a zephyr,code property.")
            if problem := _gpio_problem(node):
                return incompatible(f"The enabled GPIO button {node['path']} requires {problem}.")
        if "CONFIG_GPIO" not in config:
            return incompatible("The GPIO button driver requires CONFIG_GPIO=y.")
        if any(0 < node["code"] <= 65535 for node in keys):
            return _result("compatible", "An enabled GPIO button can provide input KEY events to this sample.", [_LIMITATION])
    return _result("unknown", "No supported enabled input KEY source was confirmed for this Button sample.",
                   ["The checker recognizes gpio-keys with CONFIG_INPUT_GPIO_KEYS; other input drivers need review.",
                    "An LED is optional for this version of the Button sample.", _LIMITATION])


def inspect_sample_requirements(profile, files, sample_id, build_dir, *, source_dir=None, emit=None, cancel=None):
    """Inspect fresh configured outputs without compiling or changing the application."""
    check_cancel(cancel)
    try:
        contents = _validated_files(files, complete=False, sample_id=sample_id)
        kind = _kind(contents, sample_id)
        if kind is None:
            return _result("unknown", "This sample source version needs a reviewed compatibility adapter.")
        build_dir = Path(build_dir)
        _compiled_sources(build_dir, source_dir, _MNIST_SOURCES if kind == "mnist-shell" else ("src/main.c",))
        config_text = _read_generated(build_dir, ".config", _MAX_FILE_BYTES)
        config = set(re.findall(r"^(CONFIG_[A-Z0-9_]+)=y$", config_text, re.MULTILINE))
        if kind == "mnist-shell":
            result = _evaluate(kind, None, config)
            if emit:
                emit(result["message"])
            return result
        dts = _read_generated(build_dir, "zephyr.dts", _MAX_TOTAL_BYTES)
        if re.search(r"/(?:include|incbin)/", dts):
            raise ValueError("Generated Device Tree contains unresolved file directives.")
        python = next((item.get("path") for item in profile.get("tools", [])
                       if item.get("name") == "Python" and item.get("status") == "discovered"), None)
        if not isinstance(python, str) or not Path(python).is_absolute():
            raise ValueError("The selected environment has no available absolute Python executable.")
        zephyr = Path(profile["zephyr"]["path"])
        if not zephyr.is_absolute() or ".." in zephyr.parts:
            raise ValueError("The selected Zephyr checkout must use an absolute path.")
        # A private copy prevents dtlib following a redirected output after the read.
        with TemporaryDirectory(prefix="sample-dts-", dir=build_dir) as directory:
            source = Path(directory) / "resolved.dts"
            source.write_text(dts, encoding="utf-8")
            output = run_probe([python, "-I", "-B", "-c", _DT_PROBE,
                                str(zephyr / "scripts/dts/python-devicetree/src"), str(source)],
                               cwd=directory, environ={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"},
                               cancel=cancel, timeout=12, max_output=128 * 1024)
        check_cancel(cancel)
        result = _evaluate(kind, _facts(output), config)
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        result = _result("unknown", "Configured sample requirements could not be verified.", [str(exc)])
    if emit:
        emit(result["message"])
    return result


def check_sample(profile, files, sample_id, *, root, environ=None, emit=None, cancel=None, default_overlay=None):
    """Configure reviewed sample files in disposable storage after an explicit request."""
    check_cancel(cancel)
    contents = _validated_files(files, sample_id=sample_id)
    if _kind(contents, sample_id) is None:
        return _result("unknown", "This sample source version needs a reviewed compatibility adapter.")
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("Sample check storage needs an absolute directory without traversal.")
    existing = root
    while not existing.exists():
        existing = existing.parent
    path_identity(existing)
    root.mkdir(parents=True, exist_ok=True)
    path_identity(root)
    recent = deque(maxlen=6)

    def progress(message):
        recent.append(str(message)[-1800:])
        if emit:
            emit(message)

    with TemporaryDirectory(prefix="sample-check-", dir=root) as directory:
        source, build = Path(directory) / "source", Path(directory) / "build"
        source.mkdir()
        for name, content in contents.items():
            check_cancel(cancel)
            destination = source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        try:
            result = execute_build(profile, source, build, "configure", environ=environ, emit=progress, cancel=cancel)
            check_cancel(cancel)
            if result.get("status") == "cancelled":
                raise CancelledError()
            if result.get("status") != "configured":
                return _result("unknown", "The sample could not be configured; compatibility is unverified.",
                               [result.get("error", "Configuration did not complete."), *recent])
            compatibility = inspect_sample_requirements(profile, files, sample_id, build, source_dir=source, emit=emit, cancel=cancel)
            compatibility["overlay_usage"] = overlay_usage(profile, source, files=files, context=result.get("context"),
                                                           default_overlay=default_overlay)
            return compatibility
        except (OSError, ValueError) as exc:
            return _result("unknown", "The sample could not be configured; compatibility is unverified.", [str(exc), *recent])
