"""Bounded local binding schemas, resolved as data without checkout code execution."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import stat

import yaml

from ...core.source_files import WorkspaceSourceReader


MAX_ROOTS = 64
MAX_FILES = 10000
MAX_DIRECTORIES = 2000
MAX_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024
MAX_EVENTS = 20000
MAX_INCLUDES = 64
MAX_PROPERTIES = 1024
MAX_DEPENDENCIES = 128
MAX_CANDIDATES = 128
MAX_DEPTH = 16
MAX_DIAGNOSTICS = 100
_PROPERTY = re.compile(r"[A-Za-z_#][A-Za-z_0-9,#.+?\-]*")
_SPACE = re.compile(r"[a-zA-Z_][a-zA-Z_0-9\-]*")
_TYPES = {"boolean", "int", "array", "uint8-array", "string", "string-array",
          "phandle", "phandles", "phandle-array", "path", "compound"}
_PROP_KEYS = {"description", "type", "required", "enum", "const", "default", "deprecated", "specifier-space"}


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class _ResolutionError(ValueError):
    def __init__(self, message, state="unsupported"):
        super().__init__(message)
        self.state = state


class _Loader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("Binding mappings require unique text keys.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def binding_mapping(text):
    """Read plain bounded YAML, rejecting aliases, tags and duplicate keys."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("Binding YAML exceeds the 256 KiB file limit.")
    try:
        depth = 0
        for count, event in enumerate(yaml.parse(text, Loader=yaml.SafeLoader)):
            if count >= MAX_EVENTS or isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise ValueError("Binding YAML anchors, aliases or event limits are unsupported.")
            if getattr(event, "tag", None):
                raise ValueError("Explicit YAML tags are unsupported in editor bindings.")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
            if depth > 32:
                raise ValueError("Binding YAML exceeds its nesting limit.")
        data = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as exc:
        raise ValueError(f"Cannot read binding YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Binding YAML must contain a mapping.")
    return data


def _merge(destination, source, *, local=False, trail=()):
    """Merge supported include semantics, preserving stronger requirements."""
    for key, value in source.items():
        if key not in destination:
            destination[key] = deepcopy(value)
        elif isinstance(destination[key], dict) and isinstance(value, dict):
            _merge(destination[key], value, local=local, trail=(*trail, key))
        elif key == "required":
            if type(value) is not bool or type(destination[key]) is not bool:
                raise _ResolutionError("Binding required constraints must be boolean.")
            if local and value and not destination[key]:
                raise _ResolutionError("A local binding cannot weaken an included required property.")
            destination[key] = destination[key] or value
        elif destination[key] != value and key not in {"title", "description", "compatible", "examples"}:
            raise _ResolutionError(f"Conflicting binding include value at {'.'.join((*trail, key))}.")


def _filter(raw, filters, depth=0):
    if not isinstance(raw, dict) or not isinstance(filters, dict) or set(filters) - {"property-allowlist", "property-blocklist", "child-binding"}:
        raise _ResolutionError("Unsupported binding include filter.")
    if depth > MAX_DEPTH:
        raise _ResolutionError("Binding child filters exceed their depth limit.")
    allow, block = filters.get("property-allowlist"), filters.get("property-blocklist")
    if allow is not None and block is not None:
        raise _ResolutionError("Choose an include property allowlist or blocklist, not both.")
    for selected in (allow, block):
        if selected is not None and (not isinstance(selected, list) or len(selected) > MAX_PROPERTIES or
                                     any(not isinstance(item, str) for item in selected)):
            raise _ResolutionError("Binding include filters must be bounded lists of property names.")
    properties = raw.get("properties")
    if isinstance(properties, dict):
        raw["properties"] = {key: value for key, value in properties.items()
                             if (allow is None or key in allow) and (block is None or key not in block)}
    child = filters.get("child-binding")
    if child is not None:
        # Validate filters even when this included file has no child schema.
        _filter(raw.get("child-binding", {}), child, depth + 1)


def _constraint_value(kind, value):
    if kind == "int":
        return type(value) is int and -(2 ** 31) <= value < 2 ** 32
    if kind == "string":
        return isinstance(value, str) and "\x00" not in value and len(value.encode("utf-8")) <= MAX_FILE_BYTES
    if kind in {"array", "uint8-array", "string-array"}:
        if not isinstance(value, list) or len(value) > MAX_PROPERTIES:
            return False
        element = "string" if kind == "string-array" else "int"
        return all(_constraint_value(element, item) and (kind != "uint8-array" or 0 <= item <= 255) for item in value)
    return False


def _property(name, raw):
    result = {"name": name, "type": None, "required": False, "deprecated": False,
              "supported": False, "reason": ""}
    reasons = []
    if not isinstance(raw, dict):
        result["reason"] = "Property settings are not a mapping."
        return result
    kind = raw.get("type")
    result["type"] = kind if isinstance(kind, str) else None
    if not isinstance(kind, str) or kind not in _TYPES:
        reasons.append("Property type is absent or unsupported.")
    kind = result["type"]
    if set(raw) - _PROP_KEYS:
        reasons.append("Unknown property constraints: " + ", ".join(sorted(set(raw) - _PROP_KEYS)) + ".")
    if "description" in raw:
        if isinstance(raw["description"], str):
            result["description"] = raw["description"]
        else:
            reasons.append("Property description must be text.")
    for key in ("required", "deprecated"):
        if key in raw:
            if type(raw[key]) is bool:
                result[key] = raw[key]
            else:
                reasons.append(f"Property {key} must be boolean.")
    if result["required"] and result["deprecated"]:
        reasons.append("A deprecated property cannot also be required.")
    if "specifier-space" in raw:
        value = raw["specifier-space"]
        if kind != "phandle-array" or not isinstance(value, str) or not _SPACE.fullmatch(value):
            reasons.append("Specifier space needs a phandle-array and a literal space name.")
        else:
            result["specifier_space"] = value
    elif kind == "phandle-array":
        if name.endswith("s"):
            result["specifier_space"] = next((space for space in ("gpio", "io-channel") if name.endswith(space + "s")), name[:-1])
        else:
            reasons.append("A phandle-array property needs an explicit specifier space when its name does not end in s.")
    for key in ("default", "const"):
        if key in raw:
            if kind == "uint8-array" and key == "const":
                reasons.append("Byte-array constants require unsupported byte-sequence comparison semantics.")
            elif _constraint_value(kind, raw[key]):
                result[key] = deepcopy(raw[key])
            else:
                reasons.append(f"Unsupported {key} value for this property type.")
    if "enum" in raw:
        values = raw["enum"]
        if (kind not in {"int", "string", "array", "string-array"} or
                not isinstance(values, list) or not values or len(values) > MAX_PROPERTIES or
                not all(_constraint_value("string" if kind == "string-array" else "int" if kind == "array" else kind, value) for value in values)):
            reasons.append("Unsupported enum constraints for this property type.")
        else:
            result["enum"] = deepcopy(values)
    if "const" in result and "default" in result and result["const"] != result["default"]:
        reasons.append("The property default conflicts with its constant.")
    if "enum" in result:
        for key in ("default", "const"):
            if key in result:
                values = result[key] if isinstance(result[key], list) else [result[key]]
                if any(value not in result["enum"] for value in values):
                    reasons.append(f"The property {key} conflicts with its enum.")
    if name in {"status", "#address-cells", "#size-cells"} and "default" in raw:
        reasons.append("This standard property's default is defined by Device Tree and cannot be overridden by a binding.")
    if kind == "compound":
        reasons.append("Compound properties remain source-only.")
    result["supported"] = not reasons
    result["reason"] = " ".join(reasons)
    return result


def _schema(raw, path, depth=0):
    if depth > MAX_DEPTH or not isinstance(raw, dict):
        raise _ResolutionError("Binding child schemas exceed the supported structure or depth.")
    allowed = {"title", "description", "compatible", "bus", "on-bus", "properties", "child-binding", "examples"}
    if any(key not in allowed and not key.endswith("-cells") for key in raw):
        raise _ResolutionError("Binding schema contains unsupported top-level keys.")
    properties = raw.get("properties", {})
    if not isinstance(properties, dict) or len(properties) > MAX_PROPERTIES:
        raise _ResolutionError("Binding properties exceed the supported mapping limit.")
    if any(not _PROPERTY.fullmatch(name) for name in properties):
        raise _ResolutionError("Binding contains an unsupported property name.")
    bus = raw.get("bus", [])
    if isinstance(bus, str):
        bus = [bus]
    if not isinstance(bus, list) or len(bus) > 32 or any(not isinstance(item, str) or not _SPACE.fullmatch(item) for item in bus):
        raise _ResolutionError("Binding bus must be a literal name or bounded name list.")
    on_bus = raw.get("on-bus")
    if on_bus is not None and (not isinstance(on_bus, str) or not _SPACE.fullmatch(on_bus)):
        raise _ResolutionError("Binding on-bus must be a literal name.")
    compatible = raw.get("compatible")
    if compatible is not None and (not isinstance(compatible, str) or not compatible or len(compatible) > 512):
        raise _ResolutionError("Binding compatible must be a bounded literal string.")
    cells = {}
    for key, values in raw.items():
        if key.endswith("-cells"):
            space = key[:-6]
            if (not _SPACE.fullmatch(space) or not isinstance(values, list) or len(values) > 64 or
                    any(not isinstance(item, str) or not _SPACE.fullmatch(item) for item in values) or
                    len(set(values)) != len(values)):
                raise _ResolutionError("Provider cell names must be unique literal names in a bounded list.")
            cells[space] = list(values)
    result = {"path": path, "compatible": compatible, "on_bus": on_bus, "bus": list(bus), "cells": cells,
              "properties": {name: _property(name, settings) for name, settings in properties.items()},
              "child_binding": None}
    if isinstance(raw.get("description"), str):
        result["description"] = raw["description"]
    if "child-binding" in raw:
        result["child_binding"] = _schema(raw["child-binding"], path, depth + 1)
    return result


class BindingCatalog:
    """One explicit schema observation; rebuilding detects changed or new candidates."""

    def __init__(self, workspace, roots, *, _parse=True):
        if not isinstance(roots, (list, tuple)) or len(roots) > MAX_ROOTS:
            raise ValueError("Choose at most 64 local binding directories.")
        self.workspace = str(workspace)
        self.reader = WorkspaceSourceReader(workspace)
        self.roots = list(dict.fromkeys(str(self.reader.path(root)) for root in roots))
        self.diagnostics = []
        self._parse = _parse
        self.complete = True
        self.entries, self.by_name, self._resolved = {}, {}, {}
        self._directories = {}
        self._entries_seen = 0
        observations = []
        self._bytes = 0
        for root in self.roots:
            path = Path(root)
            try:
                self._scan(path, observations)
            except FileNotFoundError:
                # Conventional application roots need not exist. A later root
                # appearance changes the fingerprint and requires a new review.
                observations.append({"path": root, "state": "missing"})
                if root in self._directories:
                    self.complete = False
                    self._diagnostic(f"Binding directory changed while scanning: {root}")
            except (OSError, ValueError) as exc:
                self.complete = False
                self._diagnostic(f"Cannot inspect binding directory {root}: {exc}")
                observations.append({"path": root, "state": "unsupported", "reason": str(exc)})
        self.fingerprint = _fingerprint({"workspace": self.workspace,
            "workspace_identity": self.reader.directory_identity(self.reader.workspace),
            "roots": self.roots, "observations": observations,
            "files": [{"path": key, "sha256": entry["sha256"]} for key, entry in sorted(self.entries.items())]})

    def _diagnostic(self, message):
        if len(self.diagnostics) < MAX_DIAGNOSTICS:
            self.diagnostics.append(str(message))

    def _scan(self, path, observations, depth=0):
        identity = self.reader.directory_identity(path)
        if str(path) in self._directories:
            return
        if len(self._directories) >= MAX_DIRECTORIES or depth > 32:
            raise ValueError("Binding directory scan exceeds its directory/depth limits.")
        before = path.stat()
        self._directories[str(path)] = identity
        observations.append({"path": str(path), "identity": identity})
        children = []
        for child in path.iterdir():
            self._entries_seen += 1
            if self._entries_seen > MAX_FILES + MAX_DIRECTORIES:
                raise ValueError("Binding directory scan exceeds its entry limit.")
            children.append(child)
        for child in sorted(children):
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("Symbolic links are unsupported in binding directories.")
            if stat.S_ISDIR(info.st_mode):
                if not child.name.startswith(".") and child.name not in {"build", "__pycache__"}:
                    self._scan(child, observations, depth + 1)
            elif child.suffix in {".yaml", ".yml"}:
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Binding files must be regular files.")
                key = str(child)
                if key in self.entries:
                    continue
                if len(self.entries) >= MAX_FILES:
                    raise ValueError("Binding files exceed the 10,000-file index limit.")
                document = self.reader.read_text(child, limit=MAX_FILE_BYTES)
                self._bytes += len(document["text"].encode("utf-8"))
                if self._bytes > MAX_BYTES:
                    raise ValueError("Binding contents exceed the 16 MiB index limit.")
                data = None
                if self._parse:
                    try:
                        data = binding_mapping(document["text"])
                        compatible = data.get("compatible")
                        if compatible is not None and (not isinstance(compatible, str) or not compatible or len(compatible) > 512):
                            raise ValueError("Binding compatible metadata is not a bounded literal string.")
                    except ValueError as exc:
                        self.complete = False
                        self._diagnostic(f"Unsupported binding file {child}: {exc}")
                self.entries[key] = {"path": key, "sha256": document["sha256"], "data": data}
                self.by_name.setdefault(child.name, []).append(key)
        after = path.stat()
        if (self.reader.directory_identity(path) != identity or
                (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError("Binding directory changed during inspection. Load bindings again.")

    def _resolve(self, path, stack=()):
        if path in stack or len(stack) >= MAX_DEPTH:
            raise _ResolutionError("Binding includes contain a cycle or exceed their depth limit.")
        if path in self._resolved:
            return deepcopy(self._resolved[path])
        entry = self.entries[path]
        if entry["data"] is None:
            raise _ResolutionError("The selected binding YAML is unsupported.")
        resolved, dependencies = self._resolve_mapping(deepcopy(entry["data"]), (*stack, path))
        dependencies[path] = entry["sha256"]
        self._resolved[path] = (resolved, dependencies)
        return deepcopy(self._resolved[path])

    def _resolve_mapping(self, raw, stack):
        if not isinstance(raw, dict):
            raise _ResolutionError("A binding or child binding must be a mapping.")
        includes = raw.pop("include", [])
        if isinstance(includes, str):
            includes = [includes]
        if not isinstance(includes, list) or len(includes) > MAX_INCLUDES:
            raise _ResolutionError("Binding includes must be a filename or a bounded list.")
        merged, dependencies = {}, {}
        for include in includes:
            if isinstance(include, str):
                name, filters = include, {}
            elif isinstance(include, dict) and "name" in include:
                name, filters = include["name"], {key: value for key, value in include.items() if key != "name"}
            else:
                raise _ResolutionError("Unsupported binding include declaration.")
            if not isinstance(name, str) or Path(name).name != name or Path(name).suffix not in {".yaml", ".yml"}:
                raise _ResolutionError("Binding includes require local YAML basenames without traversal.")
            candidates = self.by_name.get(name, [])
            if not candidates:
                raise _ResolutionError(f"Included binding {name} was not found.", "missing")
            if len(candidates) != 1:
                raise _ResolutionError(f"Included binding {name} is ambiguous.", "ambiguous")
            content, included = self._resolve(candidates[0], stack)
            _filter(content, filters)
            _merge(merged, content)
            dependencies.update(included)
            if len(dependencies) > MAX_DEPENDENCIES:
                raise _ResolutionError("Binding includes exceed the 128-file dependency limit.")
        _merge(raw, merged, local=True)
        if "child-binding" in raw:
            raw["child-binding"], child_dependencies = self._resolve_mapping(raw["child-binding"], stack)
            dependencies.update(child_dependencies)
            if len(dependencies) > MAX_DEPENDENCIES:
                raise _ResolutionError("Binding child includes exceed the 128-file dependency limit.")
        return raw, dependencies

    def match(self, compatibles, *, bus=None, bus_known=True, parent_binding=None):
        if not self._parse:
            raise ValueError("A fingerprint-only binding observation cannot resolve schemas.")
        if (not isinstance(compatibles, (list, tuple)) or len(compatibles) > 64 or
                any(not isinstance(item, str) or not item or len(item) > 512 for item in compatibles)):
            raise ValueError("Choose a bounded literal compatible list.")
        buses = [bus] if isinstance(bus, str) else [] if bus is None else bus
        if type(bus_known) is not bool or not isinstance(buses, (list, tuple)) or len(buses) > 32 or any(not isinstance(item, str) for item in buses):
            raise ValueError("Choose known literal parent bus names or explicitly unknown bus context.")
        result = {"state": "missing", "schema": None, "candidates": [], "diagnostics": list(self.diagnostics),
                  "fingerprint": self.fingerprint, "dependencies": []}
        if not self.complete:
            result["state"] = "unsupported"
            result["diagnostics"].append("The binding index is incomplete; schema selection is withheld.")
            return result
        if not compatibles:
            parent = parent_binding.get("schema") if isinstance(parent_binding, dict) and "schema" in parent_binding else parent_binding
            child = parent.get("child_binding") if isinstance(parent, dict) else None
            if child is not None:
                result.update(state="matched", schema=deepcopy(child),
                              dependencies=deepcopy(parent_binding.get("dependencies", [])))
            else:
                result["diagnostics"].append("No literal compatible or applicable parent child binding is available.")
            return result
        for compatible in compatibles:
            matching = []
            for path, entry in self.entries.items():
                raw = entry["data"]
                if raw.get("compatible") != compatible:
                    if raw.get("compatible") is not None or "include" not in raw:
                        continue
                    try:
                        inherited, _ = self._resolve(path)
                    except _ResolutionError:
                        # A helper without a compatible cannot be selected by
                        # name. Consumers report its resolution error themselves.
                        continue
                    if inherited.get("compatible") != compatible:
                        continue
                candidate = {"path": path, "compatible": compatible, "on_bus": raw.get("on-bus"), "sha256": entry["sha256"]}
                if len(result["candidates"]) < MAX_CANDIDATES:
                    result["candidates"].append(candidate)
                try:
                    resolved, dependencies = self._resolve(path)
                    schema = _schema(resolved, path)
                except _ResolutionError as exc:
                    result["state"] = exc.state
                    result["diagnostics"].append(f"{path}: {exc}")
                    return result
                candidate["on_bus"] = schema["on_bus"]
                matching.append((candidate, schema, dependencies))
            if not matching:
                continue
            if not bus_known and any(schema["on_bus"] is not None for _, schema, _ in matching):
                result["state"] = "unsupported"
                result["diagnostics"].append("Parent bus context is unknown; a bus-qualified binding cannot be selected safely.")
                return result
            selected = []
            for name in [*buses, None]:
                selected = [item for item in matching if item[1]["on_bus"] == name]
                if selected:
                    break
            if not selected:
                continue
            if len(selected) > 1:
                result["state"] = "ambiguous"
                result["diagnostics"].append("Multiple bindings match the same compatible and bus; no schema was selected.")
                return result
            _, schema, dependencies = selected[0]
            result.update(state="matched", schema=deepcopy(schema), dependencies=[
                {"path": path, "sha256": digest} for path, digest in sorted(dependencies.items())])
            for name, prop in schema["properties"].items():
                if not prop["supported"]:
                    result["diagnostics"].append(f"Property {name} remains source-only: {prop['reason']}")
            return result
        result["diagnostics"].append("No binding matches the literal compatible list and known parent bus.")
        return result

    def revalidate(self):
        """Reject changed files, roots or new candidates before a schema-based edit."""
        current = type(self)(self.workspace, self.roots, _parse=False)
        if current.fingerprint != self.fingerprint or not current.complete or not self.complete:
            raise ValueError("Binding inputs changed. Load bindings again before editing the draft.")
        return self.fingerprint
