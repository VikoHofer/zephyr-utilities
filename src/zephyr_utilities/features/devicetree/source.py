"""Source-preserving, reversible overlay operations on immutable in-memory bytes.

Targets identify declarations, not effective hardware. No includes are loaded, no
macros are evaluated, and no filesystem or tool execution is performed here.
"""

from bisect import bisect_left
from dataclasses import dataclass
from functools import cached_property
import hashlib
import re
import uuid

from .source_syntax import SourceError, Syntax, parse


_NAME = re.compile(r"[A-Za-z_#][A-Za-z_0-9,#.+?-]*")
_NODE = re.compile(r"[A-Za-z_0-9][A-Za-z_0-9,.+_-]*(?:@[A-Za-z_0-9,.+_-]+)?")
_LABEL = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_REFERENCE = re.compile(r"&(?:[A-Za-z_][A-Za-z_0-9]*|\{/[A-Za-z_0-9,./+@-]*\})")
_PATH = re.compile(r"/(?:[A-Za-z_0-9][A-Za-z_0-9,.+_@-]*(?:/[A-Za-z_0-9][A-Za-z_0-9,.+_@-]*)*)?")
_NUMBER = re.compile(rb"(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)")
_BYTE = re.compile(rb"[0-9a-fA-F]{2}")
_TRIVIA = {"whitespace", "comment", "directive", "bom"}


def _hash(source):
    return hashlib.sha256(source).hexdigest()


def _name(value, pattern, description):
    if not isinstance(value, str) or len(value) > 256 or not pattern.fullmatch(value):
        raise SourceError(f"Choose a literal {description} in the supported source syntax.")
    return value


def _integer(value):
    if type(value) is not int or not 0 <= value <= 0xffffffff:
        raise SourceError("Cells must be unsigned 32-bit integers.")
    return value


def _reference(value):
    _name(value, _REFERENCE, "node reference")
    if value.startswith("&{") and not _PATH.fullmatch(value[2:-1]):
        raise SourceError("Node paths must be absolute without empty or parent-traversal components.")
    return value


def _string(value):
    if not isinstance(value, str):
        raise SourceError("String values must be text.")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise SourceError("String values must be valid UTF-8.") from exc
    if len(encoded) > 65536 or any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise SourceError("String values exceed the supported text limits.")


def _quoted(value):
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return b'"' + value.encode("utf-8") + b'"'


@dataclass(frozen=True)
class PropertyValue:
    """Typed literal; strings and cells never accept executable source fragments."""

    kind: str
    value: str | tuple | None = None

    def __post_init__(self):
        if self.kind == "boolean" and self.value is None:
            return
        if self.kind == "string" and isinstance(self.value, str):
            _string(self.value)
            return
        if self.kind == "cells" and isinstance(self.value, tuple) and 1 <= len(self.value) <= 64:
            for cell in self.value:
                _integer(cell)
            return
        if self.kind == "string-array" and isinstance(self.value, tuple) and 1 <= len(self.value) <= 64:
            for item in self.value:
                _string(item)
            if sum(len(item.encode("utf-8")) for item in self.value) > 65536:
                raise SourceError("String arrays exceed the supported text limits.")
            return
        if self.kind == "bytes" and isinstance(self.value, tuple) and 1 <= len(self.value) <= 256:
            if any(type(item) is not int or not 0 <= item <= 255 for item in self.value):
                raise SourceError("Byte arrays require integers between zero and 255.")
            return
        if self.kind == "reference":
            _reference(self.value)
            return
        if self.kind == "path" and isinstance(self.value, str) and len(self.value) <= 256 and _PATH.fullmatch(self.value):
            return
        if self.kind == "phandles" and isinstance(self.value, tuple) and 1 <= len(self.value) <= 64:
            for item in self.value:
                _reference(item)
            return
        if self.kind == "phandle-array" and isinstance(self.value, tuple) and 1 <= len(self.value) <= 64:
            for group in self.value:
                if not isinstance(group, tuple) or len(group) != 2 or not isinstance(group[1], tuple) or len(group[1]) > 64:
                    raise SourceError("Each provider entry needs a reference and up to 64 literal cells.")
                _reference(group[0])
                for cell in group[1]:
                    _integer(cell)
            return
        raise SourceError("Choose a supported typed literal, array or node reference.")

    @classmethod
    def boolean(cls):
        return cls("boolean")

    @classmethod
    def string(cls, value):
        return cls("string", value)

    @classmethod
    def cells(cls, values):
        if not isinstance(values, (tuple, list)):
            raise SourceError("Cells must be a list or tuple of unsigned integers.")
        return cls("cells", tuple(values))

    @classmethod
    def strings(cls, values):
        if not isinstance(values, (tuple, list)):
            raise SourceError("String arrays require a list or tuple of text values.")
        return cls("string-array", tuple(values))

    @classmethod
    def bytes(cls, values):
        if not isinstance(values, (tuple, list)):
            raise SourceError("Byte arrays require a list or tuple of integers.")
        return cls("bytes", tuple(values))

    @classmethod
    def reference(cls, value):
        return cls("reference", value)

    @classmethod
    def path(cls, value):
        return cls("path", value)

    @classmethod
    def phandles(cls, values):
        if not isinstance(values, (tuple, list)):
            raise SourceError("Phandles require a list or tuple of node references.")
        return cls("phandles", tuple(values))

    @classmethod
    def phandle_array(cls, values):
        if not isinstance(values, (tuple, list)):
            raise SourceError("Provider entries require a list or tuple.")
        checked = []
        for item in values:
            if not isinstance(item, (tuple, list)) or len(item) != 2 or not isinstance(item[1], (tuple, list)):
                raise SourceError("Each provider entry needs a reference and literal cell list.")
            checked.append((item[0], tuple(item[1])))
        return cls("phandle-array", tuple(checked))

    def render(self):
        if self.kind == "boolean":
            return b""
        if self.kind == "cells":
            return b"<" + b" ".join(str(value).encode("ascii") for value in self.value) + b">"
        if self.kind == "string-array":
            return b", ".join(_quoted(item) for item in self.value)
        if self.kind == "bytes":
            return b"[" + b" ".join(format(item, "02x").encode("ascii") for item in self.value) + b"]"
        if self.kind == "reference":
            return self.value.encode("ascii")
        if self.kind == "phandles":
            return b"<" + b" ".join(item.encode("ascii") for item in self.value) + b">"
        if self.kind == "phandle-array":
            return b", ".join(b"<" + reference.encode("ascii") +
                b"".join(b" " + str(cell).encode("ascii") for cell in cells) + b">" for reference, cells in self.value)
        return _quoted(self.value)


@dataclass(frozen=True)
class TextEdit:
    start: int
    end: int
    before: bytes
    after: bytes


@dataclass(frozen=True)
class SourceChange:
    """Exact patch with document, revision, hash and range preconditions."""

    document_id: str
    before_revision: int
    after_revision: int
    before_sha256: str
    after_sha256: str
    edits: tuple[TextEdit, ...]
    operation: str
    diagnostics: tuple[str, ...] = ()

    def apply(self, document):
        if (not isinstance(document, SourceDocument) or document.id != self.document_id
                or document.revision != self.before_revision or document.sha256 != self.before_sha256):
            raise SourceError("Stale document identity, revision or source hash. Plan the operation again.")
        if self.after_revision != self.before_revision + bool(self.edits):
            raise SourceError("Invalid patch revision transition.")
        source, previous, pieces = document.source, 0, []
        for edit in self.edits:
            if (type(edit.start) is not int or type(edit.end) is not int
                    or not previous <= edit.start <= edit.end <= len(source)
                    or not isinstance(edit.after, bytes) or source[edit.start:edit.end] != edit.before):
                raise SourceError("Patch source ranges do not match this document.")
            pieces.extend((source[previous:edit.start], edit.after))
            previous = edit.end
        pieces.append(source[previous:])
        candidate = b"".join(pieces)
        if _hash(candidate) != self.after_sha256:
            raise SourceError("Patch result hash does not match.")
        result = SourceDocument.parse(candidate, document_id=document.id, revision=self.after_revision)
        if result.syntax.state == "incomplete":
            raise SourceError("The candidate source is incomplete; the document was not changed.")
        return result

    def inverse(self):
        shift, edits = 0, []
        for edit in self.edits:
            start = edit.start + shift
            edits.append(TextEdit(start, start + len(edit.after), edit.after, edit.before))
            shift += len(edit.after) - len(edit.before)
        return SourceChange(self.document_id, self.after_revision, self.after_revision + bool(edits),
                            self.after_sha256, self.before_sha256, tuple(edits), "undo:" + self.operation,
                            self.diagnostics)


def _unquote(value):
    """Decode ordinary C escapes for no-op comparison; unknown escapes stay opaque."""
    result = bytearray()
    position = 1
    escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord('"'): 34, ord("\\"): 92,
               ord("a"): 7, ord("b"): 8, ord("f"): 12, ord("v"): 11, ord("'"): 39}
    while position < len(value) - 1:
        char = value[position]
        if char == 92:
            position += 1
            if position >= len(value) - 1 or value[position] not in escapes:
                return None
            char = escapes[value[position]]
        result.append(char)
        position += 1
    try:
        return result.decode("utf-8")
    except UnicodeError:
        return None


@dataclass(frozen=True)
class SourceDocument:
    """An immutable source revision; source occurrence IDs expire when bytes change."""

    id: str
    revision: int
    syntax: Syntax

    @classmethod
    def parse(cls, source, *, document_id=None, revision=0):
        if document_id is None:
            document_id = uuid.uuid4().hex
        if not isinstance(document_id, str) or not document_id or len(document_id) > 128:
            raise SourceError("Choose a bounded document identity.")
        if type(revision) is not int or revision < 0:
            raise SourceError("Choose a nonnegative source revision.")
        return cls(document_id, revision, parse(source))

    @property
    def source(self):
        return self.syntax.source

    @property
    def sha256(self):
        return self.syntax.sha256

    @cached_property
    def _node_index(self):
        return {node.id: node for node in self.syntax.nodes}

    @cached_property
    def _property_index(self):
        return {prop.id: (node, prop) for node in self.syntax.nodes for prop in node.properties}

    @cached_property
    def _token_starts(self):
        return tuple(token.start for token in self.syntax.tokens)

    def node(self, node_id):
        found = self._node_index.get(node_id) if isinstance(node_id, str) else None
        if found is None:
            raise SourceError("The source node identity is stale or unknown.")
        return found

    def _path(self, node):
        if node.parent_id is None:
            return node.name if node.name == "/" else node.name[2:-1] if node.name.startswith("&{/") else None
        parent = self._path(self.node(node.parent_id))
        return parent.rstrip("/") + "/" + node.name if parent else None

    def find_node(self, selector):
        if not isinstance(selector, str):
            raise SourceError("Choose a source node reference or path.")
        matches = [node for node in self.syntax.nodes if node.name == selector
                   or self._path(node) == selector or selector in ("&" + label for label in node.labels)]
        if len(matches) != 1:
            raise SourceError("Node lookup is absent or ambiguous; choose one source occurrence explicitly.")
        return matches[0]

    def find_property(self, node_id, name):
        properties = [prop for prop in self.node(node_id).properties if prop.name == name]
        if len(properties) != 1:
            raise SourceError("Property lookup is absent or ambiguous; choose a source occurrence explicitly.")
        return properties[0]

    def _property(self, property_id):
        found = self._property_index.get(property_id) if isinstance(property_id, str) else None
        if found is not None:
            return found
        raise SourceError("The source property identity is stale or unknown.")

    def property_value(self, property_id):
        """Return a safely decoded literal for visual controls, or None if opaque."""
        _, prop = self._property(property_id)
        try:
            value, _ = self._literal(prop)
            if isinstance(value, tuple):
                return PropertyValue.string(value[1]) if value[1] is not None else None
        except SourceError:
            return None
        return value

    def can_edit_node(self, node_id):
        """Whether the current source occurrence permits literal visual edits."""
        node = self.node(node_id)
        try:
            self._editable()
            self._node_editable(node)
        except SourceError:
            return False
        return True

    def _editable(self):
        if self.syntax.blocked or self.syntax.state == "incomplete":
            raise SourceError("Incomplete or structurally opaque source cannot be edited safely.")

    def _node_editable(self, node):
        current = node
        while current:
            targets = {current.name, *("&" + label for label in current.labels)}
            path = self._path(current)
            if path:
                targets.add("&{" + path + "}")
            if any((item.parent_id == current.parent_id and item.target == current.name)
                   or (item.parent_id is None and item.target in targets) for item in self.syntax.deletions):
                raise SourceError("This source node also has a deletion directive; resolve that conflict explicitly.")
            current = self.node(current.parent_id) if current.parent_id else None

    def _edit(self, start, end, after):
        return TextEdit(start, end, self.source[start:end], after)

    def _plan(self, operation, edits, diagnostics=()):
        self._editable()
        edits = tuple(sorted((edit for edit in edits if edit.before != edit.after), key=lambda edit: edit.start))
        previous, pieces = 0, []
        for edit in edits:
            if not previous <= edit.start <= edit.end <= len(self.source):
                raise SourceError("Operation produced overlapping source ranges.")
            pieces.extend((self.source[previous:edit.start], edit.after))
            previous = edit.end
        pieces.append(self.source[previous:])
        candidate = b"".join(pieces)
        syntax = parse(candidate)
        if syntax.state == "incomplete" or syntax.blocked:
            raise SourceError("The operation would create incomplete or structurally opaque source.")
        return SourceChange(self.id, self.revision, self.revision + bool(edits), self.sha256,
                            syntax.sha256, edits, operation, tuple(diagnostics))

    def _tokens(self, start, end):
        first = bisect_left(self._token_starts, start)
        last = bisect_left(self._token_starts, end)
        return [token for token in self.syntax.tokens[first:last] if token.end <= end]

    def _literal(self, prop):
        if prop.kind != "property":
            raise SourceError("A deletion directive is not a property value.")
        if prop.value_start is None:
            return PropertyValue.boolean(), ()
        tokens = tuple(token for token in self._tokens(prop.value_start, prop.value_end) if token.kind not in _TRIVIA)
        values = [self.source[token.start:token.end] for token in tokens]
        if len(tokens) == 1 and tokens[0].kind == "string":
            return ("string", _unquote(values[0])), tokens
        if (len(tokens) >= 3 and len(tokens) % 2 and len(tokens) <= 127 and
                all(token.kind == "string" if index % 2 == 0 else values[index] == b"," for index, token in enumerate(tokens))):
            strings = [_unquote(value) for value in values[::2]]
            if all(value is not None for value in strings):
                return PropertyValue.strings(strings), tokens[::2]
        if (len(tokens) >= 3 and values[0] == b"[" and values[-1] == b"]" and len(tokens) <= 258 and
                all(_BYTE.fullmatch(value) for value in values[1:-1])):
            return PropertyValue.bytes([int(value, 16) for value in values[1:-1]]), tokens[1:-1]
        if len(tokens) == 1 and tokens[0].kind == "reference":
            return PropertyValue.reference(values[0].decode("ascii")), tokens
        if (len(tokens) >= 3 and values[0] == b"<" and values[-1] == b">"
                and len(tokens) <= 66 and all(len(value) <= 10 and _NUMBER.fullmatch(value) for value in values[1:-1])):
            cells = tuple(int(value, 16 if value.lower().startswith(b"0x") else 10) for value in values[1:-1])
            return PropertyValue.cells(cells), tokens[1:-1]
        if len(tokens) >= 3 and values[0] == b"<" and values[-1] == b">":
            groups, leaves, current = [], [], None
            inside, separator = False, False
            for token, value in zip(tokens, values):
                if value == b"<" and not inside and not separator:
                    inside = True
                elif value == b">" and inside and current is not None:
                    groups.append(current)
                    current, inside, separator = None, False, True
                elif value == b"," and separator:
                    separator = False
                elif token.kind == "reference" and inside:
                    if current is not None:
                        groups.append(current)
                    current = [value.decode("ascii"), []]
                    leaves.append(token)
                elif current is not None and inside and len(value) <= 10 and _NUMBER.fullmatch(value):
                    current[1].append(int(value, 16 if value.lower().startswith(b"0x") else 10))
                    leaves.append(token)
                else:
                    break
            else:
                if groups and not inside and separator:
                    if all(not group[1] for group in groups):
                        return PropertyValue.phandles([group[0] for group in groups]), tuple(leaves)
                    return PropertyValue.phandle_array(groups), tuple(leaves)
        raise SourceError("Opaque expressions are preserved; only literal properties can be changed.")

    def _value_edits(self, prop, value):
        if not isinstance(value, PropertyValue):
            raise SourceError("Choose a typed property value.")
        old, tokens = self._literal(prop)
        if old == value or (isinstance(old, tuple) and value.kind == "string" and old[1] == value.value):
            return []
        if isinstance(old, tuple) and value.kind == "path" and old[1] == value.value:
            return []
        reference_kinds = {"phandles", "phandle-array"}
        def groups(item):
            return tuple((ref, ()) for ref in item.value) if item.kind == "phandles" else item.value
        if isinstance(old, PropertyValue) and old.kind in reference_kinds and value.kind in reference_kinds and groups(old) == groups(value):
            return []
        if prop.value_start is None:
            return [self._edit(prop.name_end, prop.name_end, b" = " + value.render())]
        if isinstance(old, PropertyValue) and old.kind == value.kind == "cells" and len(old.value) == len(value.value):
            edits = []
            for token, before, after in zip(tokens, old.value, value.value):
                if before != after:
                    original = self.source[token.start:token.end]
                    rendered = (original[:2] + format(after, "X" if original.startswith(b"0X") else "x").encode("ascii")
                                if original.lower().startswith(b"0x") else str(after).encode("ascii"))
                    edits.append(self._edit(token.start, token.end, rendered))
            return edits
        if isinstance(old, PropertyValue) and old.kind == value.kind and old.kind in {"bytes", "string-array", "phandles", "reference", "phandle-array"}:
            def leaves(item):
                if item.kind == "reference":
                    return (item.value,)
                if item.kind == "phandle-array":
                    return tuple(leaf for ref, cells in item.value for leaf in (ref, *cells))
                return item.value
            before, after = leaves(old), leaves(value)
            same_shape = len(before) == len(after)
            if old.kind == "phandle-array":
                same_shape = same_shape and tuple(len(cells) for _, cells in old.value) == tuple(len(cells) for _, cells in value.value)
            if same_shape:
                edits = []
                for token, previous, desired in zip(tokens, before, after):
                    if previous == desired:
                        continue
                    raw = self.source[token.start:token.end]
                    if old.kind == "string-array":
                        rendered = _quoted(desired)
                    elif old.kind == "bytes":
                        rendered = format(desired, "02X" if any(65 <= char <= 70 for char in raw) else "02x").encode("ascii")
                    elif isinstance(desired, str):
                        rendered = desired.encode("ascii")
                    else:
                        rendered = (raw[:2] + format(desired, "X" if raw.startswith(b"0X") else "x").encode("ascii")
                                    if raw.lower().startswith(b"0x") else str(desired).encode("ascii"))
                    edits.append(self._edit(token.start, token.end, rendered))
                return edits
        start = prop.name_end if value.kind == "boolean" else prop.value_start
        if any(token.kind in {"comment", "directive"} for token in self._tokens(start, prop.value_end)):
            raise SourceError("Changing the literal shape would remove comments; edit the source explicitly instead.")
        return [self._edit(start, prop.value_end, value.render())]

    def _newline(self, position):
        index = self.source.rfind(b"\n", 0, position)
        if index < 0:
            index = self.source.find(b"\n", position)
        return b"\r\n" if index > 0 and self.source[index - 1:index] == b"\r" else b"\n"

    def _indent(self, position):
        start = self.source.rfind(b"\n", 0, position) + 1
        line = self.source[start:position]
        return line if not line.strip(b" \t") else b""

    def _layout(self, node):
        base = self._indent(node.start)
        for position in sorted([prop.start for prop in node.properties] + [self.node(child).start for child in node.children]):
            indent = self._indent(position)
            if indent.startswith(base) and len(indent) > len(base):
                return base, indent[len(base):], self._newline(position)
        return base, b"\t", self._newline(node.close_brace)

    def _insert(self, node, statement, *, property=False):
        if node is None:
            newline = self._newline(len(self.source))
            prefix = b"" if not self.source or self.source.endswith(b"\n") else newline
            return self._edit(len(self.source), len(self.source), prefix + statement + newline)
        base, unit, newline = self._layout(node)
        child_indent = base + unit
        rendered = child_indent + statement.replace(newline, newline + child_indent) + newline
        anchor = node.close_brace
        if property:
            subnodes = [self.node(child).start for child in node.children]
            subnodes.extend(item.start for item in self.syntax.deletions if item.parent_id == node.id)
            anchor = min([anchor, *subnodes])
        line_start = self.source.rfind(b"\n", 0, anchor) + 1
        if not self.source[line_start:anchor].strip(b" \t"):
            return self._edit(line_start, line_start, rendered)
        suffix = base if anchor == node.close_brace else child_indent
        return self._edit(anchor, anchor, newline + rendered + suffix)

    def plan_set_property(self, node_id, name, value, *, property_id=None):
        self._editable()
        name = _name(name, _NAME, "property name")
        if not isinstance(value, PropertyValue):
            raise SourceError("Choose a typed property value.")
        node = self.node(node_id)
        self._node_editable(node)
        properties = [prop for prop in node.properties if prop.name == name]
        if any(prop.kind == "delete-property" for prop in properties):
            raise SourceError("This property has a deletion directive; remove that directive explicitly first.")
        if property_id is not None:
            found = next((prop for prop in properties if prop.id == property_id), None)
            if found is None:
                raise SourceError("The selected property does not belong to this node and name.")
        elif len(properties) > 1:
            raise SourceError("Repeated property assignments require an explicit source occurrence.")
        else:
            found = properties[0] if properties else None
        if found:
            edits = self._value_edits(found, value)
        else:
            statement = name.encode("ascii") + (b" = " + value.render() if value.kind != "boolean" else b"") + b";"
            edits = [self._insert(node, statement, property=True)]
        return self._plan("set-property", edits)

    def plan_set_status(self, node_id, status):
        if status not in ("okay", "disabled"):
            raise SourceError("The initial status operation supports okay or disabled.")
        return self.plan_set_property(node_id, "status", PropertyValue.string(status))

    def plan_update_node(self, node_id, *, unit_address=None, properties=(), remove_properties=()):
        """Apply explicit local fields and an optional single-cell address in one revision.

        Bus ranges, binding requirements and effective address collisions belong to
        the caller. Source paths are not renamed when a visible consumer uses them.
        """
        self._editable()
        node = self.node(node_id)
        self._node_editable(node)
        if (not isinstance(properties, (tuple, list)) or not isinstance(remove_properties, (tuple, list))
                or len(properties) + len(remove_properties) > 128):
            raise SourceError("Choose at most 128 explicit property updates and removals.")
        checked = []
        for item in properties:
            if not isinstance(item, (tuple, list)) or len(item) != 2 or not isinstance(item[1], PropertyValue):
                raise SourceError("Each property update needs a name and typed literal value.")
            checked.append((_name(item[0], _NAME, "property name"), item[1]))
        removed = [_name(name, _NAME, "property name") for name in remove_properties]
        names = [name for name, _ in checked] + removed
        if len(names) != len(set(names)):
            raise SourceError("A property can appear only once in an atomic node update.")
        edits, diagnostics = [], []
        if unit_address is not None:
            address = _integer(unit_address)
            if "reg" in names:
                raise SourceError("The unit-address operation owns reg; do not also assign or remove it.")
            if node.parent_id is None or not _NODE.fullmatch(node.name):
                raise SourceError("Only a local child declaration can change its unit address.")
            name = _name(node.name.split("@", 1)[0] + "@" + format(address, "x"), _NODE, "child node name")
            reg = [prop for prop in node.properties if prop.name == "reg"]
            if reg:
                value = self.property_value(reg[0].id) if len(reg) == 1 else None
                if value is None or value.kind != "cells" or len(value.value) != 1:
                    raise SourceError("Changing a unit address requires an absent or unique single-cell literal reg property.")
            checked.append(("reg", PropertyValue.cells((address,))))
            if name != node.name:
                edits.append(self._unit_address_edit(node, name))
                diagnostics.append("The local unit address and reg change together; external path references are not resolved.")
        missing = []
        for name, value in checked:
            found = [prop for prop in node.properties if prop.name == name]
            if len(found) > 1:
                raise SourceError("Repeated property declarations cannot be updated as one unambiguous field.")
            if found:
                prop = found[0]
                if prop.kind != "property":
                    raise SourceError("This property has a deletion directive; reset it explicitly before assigning a value.")
                if self.property_value(prop.id) is None:
                    raise SourceError("Opaque expressions are preserved; edit that property's source explicitly.")
                edits.extend(self._value_edits(prop, value))
            else:
                missing.append(name.encode("ascii") + (b" = " + value.render() if value.kind != "boolean" else b"") + b";")
        for name in removed:
            edits.extend(self.plan_reset_property(node_id, name).edits)
        if removed:
            diagnostics.append("Only selected local property declarations are removed; inherited or earlier values may remain.")
        if missing:
            newline = self._layout(node)[2]
            edits.append(self._insert(node, newline.join(missing), property=True))
        return self._plan("update-node", edits, diagnostics)

    def _unit_address_edit(self, node, name):
        old_path = self._path(node)
        new_path = old_path.rsplit("/", 1)[0] + "/" + name if old_path else None
        if any(other.id != node.id and (other.parent_id == node.parent_id and other.name in {node.name, name}
               or old_path and self._path(other) in {old_path, new_path}) for other in self.syntax.nodes):
            raise SourceError("The current or proposed child name has another source contributor; resolve that collision explicitly.")
        if any((item.parent_id == node.parent_id and item.target == name) or
               (new_path and item.parent_id is None and item.target == "&{" + new_path + "}") for item in self.syntax.deletions):
            raise SourceError("The proposed child name has a deletion directive; resolve it explicitly.")
        if old_path:
            for reference in self.reference_inventory()["references"]:
                target = reference["reference"]
                if target.startswith("&{") and (target[2:-1] == old_path or target[2:-1].startswith(old_path + "/")):
                    raise SourceError("A source path references this node or its subtree; edit those path dependencies explicitly before changing its address.")
        tokens = [token for token in self._tokens(node.start, node.open_brace) if token.kind not in _TRIVIA]
        start, end = tokens[len(node.labels) * 2].start, tokens[-1].end
        if self.source[start:end] != node.name.encode("ascii"):
            raise SourceError("The child name cannot be isolated without changing unrelated source.")
        return self._edit(start, end, name.encode("ascii"))

    def plan_remove_property(self, property_id):
        _, prop = self._property(property_id)
        labels = {self.source[token.start:token.end].decode("ascii") for token in self._label_tokens()
                  if prop.start <= token.start < prop.end}
        if labels:
            references = {"&" + label for label in labels}
            if any(token.kind == "reference" and not prop.start <= token.start < prop.end and
                   self.source[token.start:token.end].decode("ascii") in references for token in self.syntax.tokens):
                raise SourceError("Another source declaration references this property's label; remove that reference explicitly first.")
        return self._plan("remove-local-property", [self._edit(prop.start, prop.end, b"")],
                          ("Only this source declaration is removed; inherited or earlier values may remain.",))

    def plan_reset_property(self, node_id, name):
        """Remove one local assignment/deletion without materializing an inherited default."""
        self._editable()
        _name(name, _NAME, "property name")
        node = self.node(node_id)
        self._node_editable(node)
        found = [prop for prop in node.properties if prop.name == name]
        if len(found) > 1:
            raise SourceError("Repeated property declarations cannot be reset as one unambiguous override.")
        return self.plan_remove_property(found[0].id) if found else self._plan("reset-local-property", [])

    def reference_inventory(self):
        """Describe literal local references; external includes/macros stay unresolved."""
        nodes = [{"id": node.id, "path": self._path(node), "labels": list(node.labels), "name": node.name}
                 for node in self.syntax.nodes]
        references = []

        def add(reference, start, end, kind="reference"):
            if reference.startswith("&{"):
                path = reference[2:-1]
                targets = [item["id"] for item in nodes if item["path"] == path]
            else:
                targets = [item["id"] for item in nodes if reference[1:] in item["labels"]]
            references.append({"reference": reference, "start": start, "end": end, "kind": kind,
                               "target_ids": targets, "state": "resolved" if len(targets) == 1 else "ambiguous" if targets else "missing"})

        for token in self.syntax.tokens:
            if token.kind == "reference":
                add(self.source[token.start:token.end].decode("ascii"), token.start, token.end)
        for node in self.syntax.nodes:
            if self._path(node) not in {"/aliases", "/chosen"}:
                continue
            for prop in node.properties:
                value = self.property_value(prop.id)
                if value and value.kind == "string" and _PATH.fullmatch(value.value):
                    add("&{" + value.value + "}", prop.value_start, prop.value_end, "path-string")
        return {"nodes": nodes, "references": references,
                "external_unknown": any(token.kind == "directive" for token in self.syntax.tokens) or
                    any(item.code in {"include", "opaque-value", "opaque-structure"} for item in self.syntax.diagnostics),
                "coverage": "literal-references-in-this-document"}

    def _label_tokens(self):
        significant = [token for token in self.syntax.tokens if token.kind not in _TRIVIA]
        return [left for left, right in zip(significant, significant[1:])
                if self.source[right.start:right.end] == b":" and
                _LABEL.fullmatch(self.source[left.start:left.end].decode("ascii", errors="replace"))]

    def plan_add_label(self, node_id, label):
        self._editable()
        _name(label, _LABEL, "node label")
        node = self.node(node_id)
        self._node_editable(node)
        if node.name == "/":
            raise SourceError("A root block cannot have a label; choose a child node.")
        existing = [token for token in self._label_tokens() if self.source[token.start:token.end] == label.encode("ascii")]
        if existing:
            if len(existing) == 1 and label in node.labels:
                return self._plan("add-node-label", [])
            raise SourceError("The proposed label is already used or ambiguous in the source.")
        return self._plan("add-node-label", [self._edit(node.start, node.start, label.encode("ascii") + b": ")],
                          ("The label was added only to this source occurrence; external label uniqueness requires configuration validation.",))

    def plan_rename_label(self, node_id, old, new, *, external_references_checked=False):
        """Rename only when a caller establishes complete external-reference closure."""
        self._editable()
        node = self.node(node_id)
        self._node_editable(node)
        _name(old, _LABEL, "existing node label")
        _name(new, _LABEL, "new node label")
        if old not in node.labels:
            raise SourceError("The selected node does not declare this label.")
        labels = self._label_tokens()
        old_tokens = [token for token in labels if self.source[token.start:token.end] == old.encode("ascii")]
        if len(old_tokens) != 1:
            raise SourceError("The existing label is ambiguous in this source.")
        if old == new:
            return self._plan("rename-node-label", [])
        if any(self.source[token.start:token.end] == new.encode("ascii") for token in labels):
            raise SourceError("The proposed label is already used in the source.")
        if external_references_checked is not True or self.reference_inventory()["external_unknown"]:
            raise SourceError("Label rename needs proven external-reference closure and source without includes or opaque expressions. Keep the label or edit source explicitly.")
        edits = [self._edit(old_tokens[0].start, old_tokens[0].end, new.encode("ascii"))]
        edits.extend(self._edit(token.start, token.end, ("&" + new).encode("ascii"))
                     for token in self.syntax.tokens if token.kind == "reference" and
                     self.source[token.start:token.end] == ("&" + old).encode("ascii"))
        return self._plan("rename-node-label", edits)

    def plan_set_named_reference(self, kind, name, reference):
        """Set an alias/chosen link, creating missing local blocks in one reversible change."""
        self._editable()
        if kind not in {"aliases", "chosen"}:
            raise SourceError("Choose aliases or chosen for a named node reference.")
        _name(name, _NAME, "named reference")
        if kind == "aliases" and not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise SourceError("Alias names use lowercase letters, digits and hyphens.")
        value = PropertyValue.reference(reference)
        targets = [node for node in self.syntax.nodes if
                   (self._path(node) == reference[2:-1] if reference.startswith("&{") else reference[1:] in node.labels)]
        if len(targets) != 1:
            raise SourceError("Choose a uniquely resolved local node target. Missing, inherited or ambiguous targets require source editing.")
        self._node_editable(targets[0])
        blocks = [node for node in self.syntax.nodes if self._path(node) == "/" + kind]
        if len(blocks) > 1:
            raise SourceError("Repeated aliases/chosen blocks require explicit source editing.")
        if blocks:
            return self.plan_set_property(blocks[0].id, name, value)
        roots = [node for node in self.syntax.nodes if node.name == "/" and node.parent_id is None]
        if len(roots) > 1:
            raise SourceError("Repeated root contributors require an explicit aliases/chosen block in source.")
        if roots:
            return self.plan_add_node(roots[0].id, kind, properties=((name, value),))
        # A local labelled child can belong to an external reference override;
        # adding the ordinary root block leaves that original subtree untouched.
        newline = self._newline(len(self.source))
        statement = (b"/ {" + newline + b"\t" + kind.encode("ascii") + b" {" + newline + b"\t\t" +
                     name.encode("ascii") + b" = " + value.render() + b";" + newline + b"\t};" + newline + b"};")
        return self._plan("set-named-reference", [self._insert(None, statement)])

    def plan_remove_named_reference(self, kind, name):
        self._editable()
        if kind not in {"aliases", "chosen"}:
            raise SourceError("Choose aliases or chosen for a named node reference.")
        _name(name, _NAME, "named reference")
        blocks = [node for node in self.syntax.nodes if self._path(node) == "/" + kind]
        if len(blocks) > 1:
            raise SourceError("Repeated aliases/chosen blocks require explicit source editing.")
        if not blocks:
            return self._plan("remove-named-reference", [])
        return self.plan_reset_property(blocks[0].id, name)

    def plan_add_node(self, parent_id, name, *, label=None, properties=()):
        self._editable()
        parent = self.node(parent_id) if parent_id is not None else None
        if parent:
            self._node_editable(parent)
            _name(name, _NODE, "child node name")
        elif name != "/":
            _name(name, _REFERENCE, "top-level reference")
        if label is not None:
            _name(label, _LABEL, "node label")
            if name == "/":
                raise SourceError("A root block cannot have a label; label a child or reference override instead.")
        if not isinstance(properties, (tuple, list)) or len(properties) > 128:
            raise SourceError("Choose at most 128 literal child properties.")
        checked = []
        for item in properties:
            if not isinstance(item, (tuple, list)) or len(item) != 2 or not isinstance(item[1], PropertyValue):
                raise SourceError("Each property needs a name and typed literal value.")
            checked.append((_name(item[0], _NAME, "property name"), item[1]))
        if len({name for name, _ in checked}) != len(checked):
            raise SourceError("New nodes cannot contain duplicate property names.")
        siblings = [node for node in self.syntax.nodes if node.parent_id == parent_id and node.name == name]
        if siblings:
            same = len(siblings) == 1 and siblings[0].labels == ((label,) if label else ())
            if same and not siblings[0].children and len(siblings[0].properties) == len(checked):
                try:
                    same = all(not self._value_edits(self.find_property(siblings[0].id, key), value) for key, value in checked)
                except SourceError:
                    same = False
                if same and not any(item.parent_id == siblings[0].id for item in self.syntax.deletions):
                    self._node_editable(siblings[0])
                    return self._plan("add-node", [])
            raise SourceError("A different or ambiguous node already has this name; select its declaration explicitly.")
        if label:
            significant = [token for token in self.syntax.tokens if token.kind not in _TRIVIA]
            if any(self.source[left.start:left.end] == label.encode("ascii")
                   and self.source[right.start:right.end] == b":"
                   for left, right in zip(significant, significant[1:])):
                raise SourceError("The proposed label is already used or ambiguous in the source.")
        if any(item.parent_id == parent_id and item.target == name for item in self.syntax.deletions):
            raise SourceError("This node has a deletion directive; remove it explicitly first.")
        _, unit, newline = self._layout(parent) if parent else (b"", b"\t", self._newline(len(self.source)))
        header = ((label + ": ") if label else "") + name
        body = []
        for key, value in checked:
            body.append(unit + key.encode("ascii") + (b" = " + value.render() if value.kind != "boolean" else b"") + b";")
        statement = header.encode("ascii") + b" {" + newline
        if body:
            statement += newline.join(body) + newline
        statement += b"};"
        return self._plan("add-node", [self._insert(parent, statement)])

    def plan_remove_node(self, node_id):
        node = self.node(node_id)
        descendants = [item for item in self.syntax.nodes if node.start <= item.start < node.end]
        labels = {self.source[token.start:token.end].decode("ascii") for token in self._label_tokens()
                  if node.start <= token.start < node.end}
        paths = {self._path(item) for item in descendants} - {None}
        references = {"&" + label for label in labels} | {"&{" + path + "}" for path in paths}
        for token in self.syntax.tokens:
            if token.kind == "reference" and not node.start <= token.start < node.end:
                if self.source[token.start:token.end].decode("ascii") in references:
                    raise SourceError("Another source declaration references this node or its labels; remove that reference explicitly first.")
        for item in self.reference_inventory()["references"]:
            if item["kind"] == "path-string" and not node.start <= item["start"] < node.end and item["reference"] in references:
                raise SourceError("An alias or chosen path references this node; remove that reference explicitly first.")
        return self._plan("remove-local-node", [self._edit(node.start, node.end, b"")],
                          ("Only the selected source block is removed; shared parents and other contributors remain.",
                           "References in included or macro-generated source are not resolved."))

    def plan_delete_property(self, node_id, name):
        self._editable()
        name = _name(name, _NAME, "property name")
        node = self.node(node_id)
        self._node_editable(node)
        found = [prop for prop in node.properties if prop.name == name]
        if found:
            if len(found) == 1 and found[0].kind == "delete-property":
                return self._plan("delete-property", [])
            raise SourceError("Remove or select the local property declaration before emitting an inherited-property deletion.")
        return self._plan("delete-property", [self._insert(node, b"/delete-property/ " + name.encode("ascii") + b";", property=True)],
                          ("This emits a Device Tree deletion directive; it does not remove a local override.",))

    def plan_delete_node(self, parent_id, name):
        self._editable()
        name = _name(name, _NODE, "child node name")
        parent = self.node(parent_id)
        self._node_editable(parent)
        if any(self.node(child).name == name for child in parent.children):
            raise SourceError("Remove the local child declaration explicitly before emitting a node deletion.")
        found = [item for item in self.syntax.deletions if item.parent_id == parent_id and item.target == name]
        if len(found) > 1:
            raise SourceError("Repeated node deletion directives are ambiguous.")
        return self._plan("delete-node", [] if found else [self._insert(parent, b"/delete-node/ " + name.encode("ascii") + b";")],
                          ("This emits a child deletion directive; effective results require selected-environment validation.",))

    def plan_delete_reference(self, reference):
        self._editable()
        _name(reference, _REFERENCE, "node reference")
        found = [item for item in self.syntax.deletions if item.parent_id is None and item.target == reference]
        if len(found) > 1:
            raise SourceError("Repeated node deletion directives are ambiguous.")
        return self._plan("delete-reference", [] if found else [self._insert(None, b"/delete-node/ " + reference.encode("ascii") + b";")],
                          ("This emits an explicit reference deletion; it does not merely disable the device.",))
