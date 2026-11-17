"""Pure version-one data contracts for the planned curated TinyML demo handoff.

These validators neither admit source bytes nor establish compatibility. A future
Projects service must capture/revalidate local files, module roots and toolchains;
fingerprints bind those observations, not trust an assertion received as JSON.
There is no catalogue, source reader, token store or execution behavior here.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath
import re


SCHEMA_VERSION = 1
MAX_FILES = 128
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
# Identity admission only, not an available-demo catalogue or a source path map.
_CURATED = {
    ("tflm-sine", "reference"): ("tflm", "tflm-sine-v1", "zephyr-sample", "zephyr-tflm-hello-world", "tflite-c-source", "tflite-micro"),
    ("emlearn-xor", "loadable-trees"): ("emlearn", "emlearn-xor-v1", "bundled-seed", "synthetic-emlearn-xor", "emlearn-trees-c-source", "emlearn"),
}
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_ID = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
_PART = re.compile(r"[A-Za-z0-9_.+-]+\Z")
_OUTPUT_PARTS = {"build", "out", "output", "dist", "CMakeFiles", "__pycache__"}


def _object(value, required, optional=()):
    if (not isinstance(value, dict) or any(not isinstance(key, str) for key in value)
            or set(value) - set(required) - set(optional) or set(required) - set(value)):
        raise ValueError("Unsupported or incomplete demo contract fields.")
    return value


def _version(value):
    if type(value) is not int or value != SCHEMA_VERSION:
        raise ValueError("Unsupported demo contract schema version.")


def _text(value, *, nullable=False, maximum=1000):
    if value is None and nullable:
        return None
    if (not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum
            or any(not character.isprintable() for character in value)):
        raise ValueError("Demo contract strings must be bounded readable text.")
    return value


def _sha(value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("A demo fingerprint must be a lowercase SHA-256 value.")
    return value


def _choice(value, options):
    return isinstance(value, str) and value in options


def _relative(value):
    _text(value, maximum=512)
    path = PurePosixPath(value)
    if (not path.parts or path.is_absolute() or str(path) != value or len(path.parts) > 8
            or any(part in {".", ".."} or not _PART.fullmatch(part)
                   or part in _OUTPUT_PARTS or part.startswith(("build-", "build_", "cmake-build-"))
                   or part.startswith(".") and value != ".gitignore" for part in path.parts)):
        raise ValueError("Demo evidence paths must be bounded relative paths without traversal.")
    return value


def _absolute(value):
    _text(value, maximum=4096)
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts or ";" in value:
        raise ValueError("Demo observation paths must be absolute without traversal or semicolons.")
    return value


def _items(value, maximum=128):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("Demo contract lists exceed their supported bounds.")
    return value


def _unique(values):
    if len(values) != len(set(values)):
        raise ValueError("Demo contract entries must have distinct identities.")


def _curated(demo_id, variant_id):
    if not isinstance(demo_id, str) or not isinstance(variant_id, str) or (demo_id, variant_id) not in _CURATED:
        raise ValueError("Choose an admitted demo and variant identity.")
    return _CURATED[demo_id, variant_id]


def _adapter(value, expected):
    _object(value, {"id", "version"})
    if value["id"] != expected or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("This demo adapter version is not admitted.")


def _licenses(value, files):
    _items(value, 16)
    if not value:
        raise ValueError("Demo sources need explicit license evidence.")
    for item in value:
        _object(item, {"spdx", "path", "sha256"})
        if not _choice(item["spdx"], {"Apache-2.0", "MIT"}):
            raise ValueError("This demo license needs separate admission.")
        _relative(item["path"])
        _sha(item["sha256"])
        if files.get(item["path"]) != item["sha256"]:
            raise ValueError("Demo license evidence must match a reviewed runtime file.")
    _unique([item["path"] for item in value])


def validate_descriptor(value):
    """Return a detached, normalized descriptor, never read or qualify its source.

    Source fingerprint is an observed capture hash, or null before qualification.
    Model SHA identifies the textual model artifact named by its source format.
    The emlearn example remains an unqualified future seed while that hash is null.
    """
    value = deepcopy(_object(value, {"schema_version", "demo_id", "variant_id", "framework_id", "title", "task",
                                    "adapter", "source", "runtime", "model", "requirements", "licenses", "expected_output"}))
    _version(value["schema_version"])
    framework, adapter, kind, locator, model_format, module = _curated(value["demo_id"], value["variant_id"])
    if value["framework_id"] != framework:
        raise ValueError("The framework does not match this curated demo.")
    _adapter(value["adapter"], adapter)
    _text(value["title"], maximum=120)
    _text(value["task"], maximum=500)
    source = _object(value["source"], {"kind", "locator_id", "revision", "fingerprint"})
    if source["kind"] != kind or source["locator_id"] != locator:
        raise ValueError("A demo source must use its curated locator, never a caller path.")
    _text(source["revision"], nullable=True, maximum=200)
    _sha(source["fingerprint"], nullable=True)
    runtime = _object(value["runtime"], {"files", "omitted"})
    files, total = {}, 0
    for item in _items(runtime["files"], MAX_FILES):
        _object(item, {"path", "sha256", "bytes"})
        _relative(item["path"])
        _sha(item["sha256"])
        if type(item["bytes"]) is not int or not 0 <= item["bytes"] <= MAX_FILE_BYTES:
            raise ValueError("A demo runtime file exceeds the current byte limit.")
        total += item["bytes"]
        if item["path"] in files:
            raise ValueError("Demo runtime paths must be distinct.")
        files[item["path"]] = item["sha256"]
    if not files or total > MAX_TOTAL_BYTES:
        raise ValueError("Demo runtime files must fit the current bounded publication plan.")
    paths = [PurePosixPath(path) for path in files]
    if any(str(parent) in files for path in paths for parent in path.parents if str(parent) != "."):
        raise ValueError("Demo runtime files conflict with a directory path.")
    for item in _items(runtime["omitted"]):
        _object(item, {"path", "reason"})
        _relative(item["path"])
        _text(item["reason"], maximum=500)
        if item["path"] in files:
            raise ValueError("A demo file cannot be both included and omitted.")
    _unique([item["path"] for item in runtime["omitted"]])
    model = _object(value["model"], {"format", "origin", "sha256"})
    if model["format"] != model_format:
        raise ValueError("The model format does not match the curated demo.")
    _text(model["origin"], maximum=500)
    _sha(model["sha256"], nullable=True)
    if model["sha256"] is not None and model["sha256"] not in files.values():
        raise ValueError("The textual model identity must match a reviewed runtime artifact.")
    requirements = _object(value["requirements"], {"modules", "kconfig", "hardware"})
    if requirements["modules"] != [module]:
        raise ValueError("The demo must retain its explicitly admitted framework module requirement.")
    for item in _items(requirements["kconfig"], 32):
        _object(item, {"symbol", "value"})
        if not isinstance(item["symbol"], str) or not re.fullmatch(r"CONFIG_[A-Z0-9_]+", item["symbol"]):
            raise ValueError("A demo configuration requirement needs a Kconfig symbol.")
        if not _choice(item["value"], {"y", "n"}):
            raise ValueError("This contract admits boolean Kconfig requirements only.")
    _unique([item["symbol"] for item in requirements["kconfig"]])
    if requirements["hardware"] != []:
        raise ValueError("The initial demo identities admit fixed-input CPU inference only.")
    _licenses(value["licenses"], files)
    for line in _items(value["expected_output"], 16):
        _text(line, maximum=500)
    if not value["expected_output"]:
        raise ValueError("A demo needs an expected output description.")
    for field in ("files", "omitted"):
        runtime[field].sort(key=lambda item: item["path"])
    requirements["kconfig"].sort(key=lambda item: item["symbol"])
    value["licenses"].sort(key=lambda item: item["path"])
    return value


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def descriptor_fingerprint(value):
    """Bind semantic descriptor data separately from availability/target evidence."""
    return _fingerprint(validate_descriptor(value))


def validate_compatibility_identity(value):
    """Validate detached observation identities; null evidence remains unknown."""
    value = deepcopy(_object(value, {"schema_version", "workspace", "environment_fingerprint", "descriptor_fingerprint",
                                    "source_fingerprint", "model_sha256", "demo_id", "variant_id", "adapter", "target",
                                    "sdk", "modules", "roots", "configuration"}))
    _version(value["schema_version"])
    _absolute(value["workspace"])
    _, adapter, _, _, _, required_module = _curated(value["demo_id"], value["variant_id"])
    _adapter(value["adapter"], adapter)
    for name in ("environment_fingerprint", "descriptor_fingerprint"):
        _sha(value[name])
    for name in ("source_fingerprint", "model_sha256"):
        _sha(value[name], nullable=True)
    if value["target"] is not None:
        target = _object(value["target"], {"board_id", "requested", "effective", "revision"})
        _text(target["board_id"])
        _text(target["requested"])
        _text(target["effective"], nullable=True)
        _text(target["revision"], nullable=True, maximum=200)
    if value["sdk"] is not None:
        sdk = _object(value["sdk"], {"id", "fingerprint", "toolchain_fingerprint"})
        _text(sdk["id"])
        _sha(sdk["fingerprint"], nullable=True)
        _sha(sdk["toolchain_fingerprint"], nullable=True)
    for item in _items(value["modules"]):
        _object(item, {"id", "path", "revision", "fingerprint", "status"})
        if not isinstance(item["id"], str) or not _ID.fullmatch(item["id"]):
            raise ValueError("Invalid observed module identity.")
        if not _choice(item["status"], {"present", "missing", "unknown"}):
            raise ValueError("Invalid module observation state.")
        if item["path"] is not None:
            _absolute(item["path"])
        _text(item["revision"], nullable=True, maximum=200)
        _sha(item["fingerprint"], nullable=True)
        if item["status"] != "present" and item["fingerprint"] is not None:
            raise ValueError("A missing or unknown module cannot claim an installed fingerprint.")
        if item["status"] == "present" and item["path"] is None:
            raise ValueError("A present module needs an observed root.")
    _unique([item["id"] for item in value["modules"]])
    if required_module not in {item["id"] for item in value["modules"]}:
        raise ValueError("Retain an explicit present/missing/unknown framework-module observation.")
    for item in _items(value["roots"]):
        _object(item, {"kind", "path", "fingerprint"})
        if not _choice(item["kind"], {"boards", "socs", "dts", "bindings", "snippets"}):
            raise ValueError("Unsupported observed discovery-root kind.")
        _absolute(item["path"])
        _sha(item["fingerprint"], nullable=True)
    _unique([(item["kind"], item["path"]) for item in value["roots"]])
    configuration = _object(value["configuration"], {"kconfig_fingerprint", "overlays_fingerprint"})
    for field in configuration:
        _sha(configuration[field], nullable=True)
    # Preserve configured ordering: discovery and include precedence may change
    # when the same roots/modules are supplied in another order.
    return value


def compatibility_key(value):
    """Bind source/model/adapter, target/revision, SDK, roots, modules and config."""
    return _fingerprint(validate_compatibility_identity(value))


def _bind_descriptor(identity, descriptor):
    descriptor = validate_descriptor(descriptor)
    for field in ("demo_id", "variant_id", "adapter"):
        if identity[field] != descriptor[field]:
            raise ValueError("Demo evidence belongs to another descriptor identity.")
    if (identity["descriptor_fingerprint"] != descriptor_fingerprint(descriptor)
            or identity["source_fingerprint"] != descriptor["source"]["fingerprint"]
            or identity["model_sha256"] != descriptor["model"]["sha256"]):
        raise ValueError("Demo evidence does not match its descriptor/source/model identity.")
    return descriptor


def _source_ready(identity):
    return (identity["source_fingerprint"] is not None and identity["model_sha256"] is not None
            and all(item["status"] == "present" and item["fingerprint"] is not None for item in identity["modules"]))


def _complete_check_identity(identity):
    return (_source_ready(identity) and identity["target"] is not None and identity["target"]["effective"] is not None
            and identity["sdk"] is not None and all(identity["sdk"][name] is not None for name in ("fingerprint", "toolchain_fingerprint"))
            and all(item["fingerprint"] is not None for item in identity["roots"])
            and all(item is not None for item in identity["configuration"].values()))


def validate_compatibility_evidence(value, *, descriptor=None):
    """Validate an observation and its binding, never manufacture a successful check."""
    value = deepcopy(_object(value, {"schema_version", "identity", "key", "availability", "configure", "build", "diagnostics"}))
    _version(value["schema_version"])
    identity = value["identity"] = validate_compatibility_identity(value["identity"])
    if descriptor is not None:
        _bind_descriptor(identity, descriptor)
    if _sha(value["key"]) != compatibility_key(identity):
        raise ValueError("Compatibility identity changed; the saved check key is stale.")
    if not _choice(value["availability"], {"source-ready", "missing-module", "unsupported-layout", "unknown"}):
        raise ValueError("Unsupported demo availability observation.")
    if value["availability"] == "source-ready" and not _source_ready(identity):
        raise ValueError("Unknown model/source/module evidence cannot be source-ready.")
    if value["availability"] == "missing-module" and not any(item["status"] == "missing" for item in identity["modules"]):
        raise ValueError("Missing-module availability needs an explicit missing module observation.")
    for operation in ("configure", "build"):
        check = _object(value[operation], {"state", "evidence"})
        if not _choice(check["state"], {"not-checked", "passed", "failed", "unknown"}):
            raise ValueError("Unsupported demo check state.")
        evidence = check["evidence"]
        if evidence is not None:
            _object(evidence, {"kind", "id"})
            if (not _choice(evidence["kind"], {"sample-check", "project-attempt"})
                    or not isinstance(evidence["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", evidence["id"])
                    or operation == "build" and evidence["kind"] != "project-attempt"):
                raise ValueError("A demo check needs the appropriate Projects evidence identity.")
        if check["state"] == "not-checked" and evidence is not None:
            raise ValueError("An unperformed check cannot have execution evidence.")
        if check["state"] == "passed" and (evidence is None or value["availability"] != "source-ready" or not _complete_check_identity(identity)):
            raise ValueError("A passed check needs complete captured identity and explicit Projects evidence.")
    if value["build"]["state"] == "passed" and value["configure"]["state"] != "passed":
        raise ValueError("A build success must retain successful configuration evidence.")
    for message in _items(value["diagnostics"], 32):
        _text(message, maximum=1800)
    if value["availability"] in {"missing-module", "unsupported-layout"} and not value["diagnostics"]:
        raise ValueError("Unavailable demos need a specific visible reason.")
    return value


def validate_handoff_selection(value, *, descriptor=None):
    """Validate preselection only; this is not a publication or execution plan.

    The versioned captured selection mirrors the future POST request fields.
    Projects remains authoritative for current workspace, catalogue and profile.
    """
    value = deepcopy(_object(value, {"schema_version", "workspace", "demo_id", "variant_id", "source_fingerprint", "environment_fingerprint"}, {"selection"}))
    _version(value["schema_version"])
    _absolute(value["workspace"])
    _curated(value["demo_id"], value["variant_id"])
    _sha(value["source_fingerprint"])
    _sha(value["environment_fingerprint"])
    if value.get("selection") is not None:
        selected = _object(value["selection"], {"board_id", "target", "sdk_id"})
        for field in selected:
            _text(selected[field])
    value.setdefault("selection", None)
    if descriptor is not None:
        descriptor = validate_descriptor(descriptor)
        if (any(value[field] != descriptor[field] for field in ("demo_id", "variant_id"))
                or value["source_fingerprint"] != descriptor["source"]["fingerprint"]
                or descriptor["model"]["sha256"] is None):
            raise ValueError("Only the current qualified demo source/model can be preselected.")
    return value


def validate_demo_origin(value):
    """Validate optional publication provenance; None preserves legacy records.

    No existing Projects record reader/writer is changed by defining this shape.
    The full descriptor retains license/model/omission facts; identity adds the
    captured environment, modules and target. Configuration hashes may be null
    because creating source does not itself configure the application.
    """
    if value is None:
        return None
    value = deepcopy(_object(value, {"schema_version", "descriptor", "identity", "transformations"}))
    _version(value["schema_version"])
    value["descriptor"] = validate_descriptor(value["descriptor"])
    value["identity"] = validate_compatibility_identity(value["identity"])
    _bind_descriptor(value["identity"], value["descriptor"])
    if not _source_ready(value["identity"]) or value["identity"]["target"] is None or value["identity"]["sdk"] is None:
        raise ValueError("Published demo provenance needs source/model/module and selected target/SDK identity.")
    files = {item["path"]: item["sha256"] for item in value["descriptor"]["runtime"]["files"]}
    for item in _items(value["transformations"]):
        _object(item, {"path", "source_sha256", "published_sha256", "reason"})
        _relative(item["path"])
        _sha(item["source_sha256"])
        _sha(item["published_sha256"])
        _text(item["reason"], maximum=500)
        if files.get(item["path"]) != item["source_sha256"] or item["source_sha256"] == item["published_sha256"]:
            raise ValueError("A transformation must bind an admitted source file to changed published bytes.")
    _unique([item["path"] for item in value["transformations"]])
    value["transformations"].sort(key=lambda item: item["path"])
    return value
