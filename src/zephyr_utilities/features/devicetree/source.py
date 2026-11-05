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
_NUMBER = re.compile(rb"(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)")
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


@dataclass(frozen=True)
class PropertyValue:
    """Typed literal; strings and cells never accept executable source fragments."""

    kind: str
    value: str | tuple[int, ...] | None = None

    def __post_init__(self):
        if self.kind == "boolean" and self.value is None:
            return
        if self.kind == "string" and isinstance(self.value, str):
            try:
                encoded = self.value.encode("utf-8")
            except UnicodeError as exc:
                raise SourceError("String values must be valid UTF-8.") from exc
            if len(encoded) > 65536 or any(ord(char) < 32 and char not in "\n\r\t" for char in self.value):
                raise SourceError("String values exceed the supported text limits.")
            return
        if self.kind == "cells" and isinstance(self.value, tuple) and 1 <= len(self.value) <= 64:
            for cell in self.value:
                _integer(cell)
            return
        raise SourceError("Choose a boolean, string or one to 64 unsigned literal cells.")

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

    def render(self):
        if self.kind == "boolean":
            return b""
        if self.kind == "cells":
            return b"<" + b" ".join(str(value).encode("ascii") for value in self.value) + b">"
        value = self.value.replace("\\", "\\\\").replace('"', '\\"')
        value = value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        return b'"' + value.encode("utf-8") + b'"'


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
        if (len(tokens) >= 3 and values[0] == b"<" and values[-1] == b">"
                and len(tokens) <= 66 and all(len(value) <= 10 and _NUMBER.fullmatch(value) for value in values[1:-1])):
            cells = tuple(int(value, 16 if value.lower().startswith(b"0x") else 10) for value in values[1:-1])
            return PropertyValue.cells(cells), tokens[1:-1]
        raise SourceError("Opaque expressions are preserved; only literal properties can be changed.")

    def _value_edits(self, prop, value):
        if not isinstance(value, PropertyValue):
            raise SourceError("Choose a typed property value.")
        old, tokens = self._literal(prop)
        if old == value or (isinstance(old, tuple) and value.kind == "string" and old[1] == value.value):
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

    def plan_remove_property(self, property_id):
        _, prop = self._property(property_id)
        return self._plan("remove-local-property", [self._edit(prop.start, prop.end, b"")],
                          ("Only this source declaration is removed; inherited or earlier values may remain.",))

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
        labels = {label for item in descendants for label in item.labels}
        labels.update(label for item in descendants for prop in item.properties for label in prop.labels)
        paths = {self._path(item) for item in descendants} - {None}
        references = {"&" + label for label in labels} | {"&{" + path + "}" for path in paths}
        for token in self.syntax.tokens:
            if token.kind == "reference" and not node.start <= token.start < node.end:
                if self.source[token.start:token.end].decode("ascii") in references:
                    raise SourceError("Another source declaration references this node or its labels; remove that reference explicitly first.")
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
