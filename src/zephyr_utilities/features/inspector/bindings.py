"""Navigate current binding YAML without executing checkout code or resolving schemas."""

from pathlib import Path
import re

import yaml

from .parser import parse_resolved_dts


MAX_BINDING_FILES = 10000
MAX_BINDING_BYTES = 256 * 1024
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_YAML_EVENTS = 20000
MAX_DIAGNOSTICS = 100
MAX_CANDIDATES = 200
MAX_INCLUDES = 100


class _BindingLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("Binding mappings require unique text keys.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def binding_mapping(text):
    """Parse bounded plain YAML; aliases, custom constructors and duplicate keys fail."""
    if len(text.encode("utf-8")) > MAX_BINDING_BYTES:
        raise ValueError("Binding YAML exceeds the 256 KiB navigation limit.")
    depth = 0
    try:
        for count, event in enumerate(yaml.parse(text, Loader=yaml.SafeLoader)):
            if count >= MAX_YAML_EVENTS or isinstance(event, yaml.AliasEvent):
                raise ValueError("Binding YAML aliases or event limits are unsupported.")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
            if depth > 32:
                raise ValueError("Binding YAML exceeds its nesting limit.")
        data = yaml.load(text, Loader=_BindingLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"Cannot navigate this binding YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Binding YAML must contain a mapping.")
    return data


def _text(value):
    return value if isinstance(value, str) else None


def _diagnostic(items, message):
    if len(items) < MAX_DIAGNOSTICS:
        items.append(str(message))
    elif len(items) == MAX_DIAGNOSTICS:
        items.append("Further binding diagnostics were omitted.")


def _compatibles(node):
    value = next((item["value"] for item in node["properties"] if item["name"] == "compatible"), "")
    if not value:
        return []
    # Only literal compatible strings are useful here; never evaluate DTS expressions.
    if not re.fullmatch(r'\s*"[^"\\\x00-\x1f]+"\s*(?:,\s*"[^"\\\x00-\x1f]+"\s*)*', value):
        raise ValueError("The node's compatible value is not a supported literal string list.")
    return re.findall(r'"([^"\\]+)"', value)


def _recorded_binding(scope, node_path, diagnostics):
    try:
        header = scope.artifact_text("devicetree-header")["text"]
    except (ValueError, OSError):
        _diagnostic(diagnostics, "No supported generated binding-path record is available for this image.")
        return None
    for comment in re.finditer(r'/\*(.*?)\*/', header, re.DOTALL):
        lines = [re.sub(r'^\s*\* ?', '', line).strip() for line in comment[1].splitlines()]
        if f"Devicetree node: {node_path}" not in lines:
            continue
        for index, line in enumerate(lines[:-1]):
            match = re.fullmatch(r'Binding \(compatible = ([^()\r\n]+)\):', line)
            if not match:
                continue
            path = lines[index + 1]
            if path.startswith("No yaml"):
                _diagnostic(diagnostics, "The generated header reports inferred properties rather than a YAML binding.")
                return None
            if path.startswith("$ZEPHYR_BASE/"):
                base = scope.context.get("zephyr_base")
                if not base:
                    _diagnostic(diagnostics, "The recorded binding uses an unknown Zephyr checkout path.")
                    return None
                path = str(Path(base) / path[len("$ZEPHYR_BASE/"):])
            if "$" in path or Path(path).suffix not in {".yaml", ".yml"}:
                _diagnostic(diagnostics, "The generated header's binding path is unsupported.")
                return None
            return path, match[1]
    _diagnostic(diagnostics, "This node has no binding path recorded in the generated header; child bindings may not have a path record.")
    return None


class BindingNavigator:
    """Cache a bounded current-checkout index only for the lifetime of a source scope."""

    def index(self, scope):
        if hasattr(scope, "binding_index"):
            return scope.binding_index
        scope.guard.verify()
        entries, by_name, diagnostics, seen = [], {}, [], set()
        total = 0
        roots = scope.binding_dirs
        if not roots:
            _diagnostic(diagnostics, "No binding search directories are available. Candidate search is unavailable.")
        if len(roots) > 64:
            _diagnostic(diagnostics, f"Only the first 64 {scope.locations_description} binding directories were searched.")
        exhausted = False
        for root in roots[:64]:
            try:
                directory = scope.reader.path(root, scope.base_dir)
                paths = scope.reader.list_files(directory, {".yaml", ".yml"}, max_files=MAX_BINDING_FILES, max_dirs=2000,
                                                 **scope.binding_scan_options)
            except (ValueError, OSError) as exc:
                _diagnostic(diagnostics, f"Cannot search binding directory {root}: {exc}")
                continue
            for path in paths:
                if str(path) in seen:
                    continue
                seen.add(str(path))
                if len(seen) > MAX_BINDING_FILES:
                    _diagnostic(diagnostics, "Binding search reached its file count limit; results are incomplete.")
                    exhausted = True
                    break
                remaining = MAX_INDEX_BYTES - total
                if remaining <= 0:
                    _diagnostic(diagnostics, "Binding search reached its 16 MiB reading limit; results are incomplete.")
                    exhausted = True
                    break
                allowance = min(MAX_BINDING_BYTES, remaining)
                try:
                    document = scope.reader.read_text(path, limit=allowance)
                except (ValueError, OSError) as exc:
                    # Invalid text may have consumed the entire allowance before decoding failed.
                    total += allowance
                    _diagnostic(diagnostics, f"Cannot index {path}: {exc}")
                    continue
                total += len(document["text"].encode("utf-8"))
                try:
                    entry = {"path": str(path), "sha256": document["sha256"], "data": None}
                    # Keep include navigation available even for unsupported YAML.
                    entries.append(entry)
                    by_name.setdefault(path.name, []).append(entry)
                    data = binding_mapping(document["text"])
                    entry["data"] = {key: data[key] for key in ("compatible", "description", "on-bus", "bus") if key in data}
                except (ValueError, OSError) as exc:
                    _diagnostic(diagnostics, f"Cannot index {path}: {exc}")
            if exhausted:
                break
        scope.guard.verify()
        scope.binding_index = {"entries": entries, "by_name": by_name, "diagnostics": diagnostics}
        return scope.binding_index

    def _file(self, scope, entry, origin):
        file = scope.add_file(entry["path"], "binding", origin)
        if file["availability"] == "available":
            document = scope.read(file["id"])
            if document["sha256"] != entry["sha256"]:
                raise ValueError("A binding changed since this source catalogue was indexed. Refresh the source list.")
        return file

    def find(self, scope, node_path):
        if not isinstance(node_path, str) or not node_path.startswith("/") or len(node_path) > 4096:
            raise ValueError("Choose a node from the resolved Device Tree.")
        tree = parse_resolved_dts(scope.artifact_text("resolved-dts")["text"])
        selected = next((node for node in tree["nodes"] if node["path"] == node_path), None)
        if selected is None:
            raise ValueError("Choose a node from this image's resolved Device Tree.")
        diagnostics = []
        try:
            compatibles = _compatibles(selected)
        except ValueError as exc:
            compatibles = []
            _diagnostic(diagnostics, exc)
        index = self.index(scope)
        diagnostics.extend(index["diagnostics"])
        recorded = _recorded_binding(scope, node_path, diagnostics)
        bindings, visited = [], set()
        if recorded:
            path, compatible = recorded
            file = scope.add_file(path, "binding", "Binding path recorded in generated header")
            data = {}
            if file["availability"] == "available":
                document = scope.read(file["id"])
                try:
                    data = binding_mapping(document["text"])
                except ValueError as exc:
                    _diagnostic(diagnostics, exc)
            bindings.append(self._candidate(file, data, compatible, "recorded-binding"))
            if data.get("compatible") and data["compatible"] != compatible:
                _diagnostic(diagnostics, "The current YAML compatible differs from the generated header's binding record.")
            visited.add(file["path"])
        for compatible in compatibles:
            for entry in index["entries"]:
                data = entry["data"]
                if not data or data.get("compatible") != compatible or entry["path"] in visited:
                    continue
                if len(bindings) >= MAX_CANDIDATES:
                    _diagnostic(diagnostics, "Only the first 200 binding candidates are shown.")
                    break
                file = self._file(scope, entry, "Compatible candidate in recorded binding directories")
                bindings.append(self._candidate(file, data, compatible, "compatible-candidate"))
                visited.add(file["path"])
        if not compatibles:
            _diagnostic(diagnostics, "No literal compatible strings were found. A parent child-binding may apply; it is not resolved here.")
        _diagnostic(diagnostics, "Candidates use current YAML and compatible strings only. Bus rules, include merging and child-binding inheritance are not resolved or validated.")
        scope.guard.verify()
        return {"node_path": node_path, "compatibles": compatibles, "bindings": bindings, "diagnostics": diagnostics}

    @staticmethod
    def _candidate(file, data, compatible, evidence):
        bus = data.get("bus")
        if not isinstance(bus, str) and not (isinstance(bus, list) and all(isinstance(item, str) for item in bus)):
            bus = None
        return {"file": file, "compatible": compatible, "description": _text(data.get("description")),
                "on_bus": _text(data.get("on-bus")), "bus": bus, "evidence": evidence}

    def document(self, scope, file_id):
        document = scope.read(file_id)
        file = scope.files[file_id]
        diagnostics, includes, properties = [], [], []
        try:
            data = binding_mapping(document["text"])
        except ValueError as exc:
            return {"file": file, "document": document, "includes": [], "properties": [], "diagnostics": [str(exc)]}
        index = self.index(scope)
        raw_includes = data.get("include", [])
        if isinstance(raw_includes, (str, dict)):
            raw_includes = [raw_includes]
        if not isinstance(raw_includes, list):
            raw_includes = []
            _diagnostic(diagnostics, "Unsupported include declaration; inspect the YAML text.")
        if len(raw_includes) > MAX_INCLUDES:
            _diagnostic(diagnostics, "Only the first 100 binding includes are shown.")
        for item in raw_includes[:MAX_INCLUDES]:
            name = item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name or len(name) > 256:
                _diagnostic(diagnostics, "An unsupported include declaration has no literal filename.")
                continue
            files = []
            if Path(name).name == name and Path(name).suffix in {".yaml", ".yml"}:
                files = [self._file(scope, entry, f"Included by {file['name']}") for entry in index["by_name"].get(name, [])]
            note = ""
            if not files:
                note = f"No supported include file was found in the {scope.locations_description} binding directories."
            elif len(files) > 1:
                note = "Ambiguous include name; all candidates are shown."
            if isinstance(item, dict):
                note += " Include filters are recorded in the YAML; no merged schema is computed."
            if any(candidate["id"] == file_id for candidate in files):
                note += " This include refers to the current file."
            includes.append({"name": name, "files": files, "note": note.strip()})
        raw_properties = data.get("properties", {})
        if isinstance(raw_properties, dict):
            for name, settings in list(raw_properties.items())[:1000]:
                if not isinstance(settings, dict):
                    continue
                row = {"name": name}
                for key in ("type", "description"):
                    if isinstance(settings.get(key), str):
                        row[key] = settings[key]
                if isinstance(settings.get("required"), bool):
                    row["required"] = settings["required"]
                # Values stay textual: YAML scalar types need not be JSON serializable.
                for key in ("enum", "default"):
                    if key in settings:
                        row[key] = yaml.safe_dump(settings[key], sort_keys=False).strip()
                properties.append(row)
        if "child-binding" in data:
            _diagnostic(diagnostics, "This file declares a child-binding. Child schemas and inherited properties are not merged in this view.")
        if includes:
            _diagnostic(diagnostics, "Properties below are declared in this file only. Open included files to read their declarations.")
        scope.guard.verify()
        return {"file": file, "document": document, "includes": includes, "properties": properties, "diagnostics": diagnostics}
