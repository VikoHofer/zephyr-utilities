"""Bounded, lossless source occurrences for overlay edits, without preprocessing.

This is a syntax index, not an effective Device Tree or a complete DTS validator.
Every offset addresses the original UTF-8 bytes. Unknown expressions stay opaque;
unknown structure, conditionals and incomplete input prevent source transformations.
"""

from dataclasses import dataclass
import hashlib
import re

MAX_BYTES = 256 * 1024
MAX_TOKENS = 32768
MAX_DEPTH = 64
_TRIVIA = {"whitespace", "comment", "directive", "bom"}
_DIRECTIVES = {b"include", b"define", b"undef", b"if", b"ifdef", b"ifndef", b"elif",
               b"else", b"endif", b"error", b"warning", b"pragma", b"line"}
_CONDITIONALS = {b"if", b"ifdef", b"ifndef", b"elif", b"else", b"endif"}
_DIRECTIVE_NAME = re.compile(rb"#[ \t]*([A-Za-z]+)\b")
_REFERENCE = re.compile(rb"&(?:[A-Za-z_][A-Za-z_0-9]*|\{/[A-Za-z_0-9,./+@-]*\})")
_PROPERTY_NAME = re.compile(rb"[A-Za-z_#][A-Za-z_0-9,#.+?-]*")
_NODE_NAME = re.compile(rb"[A-Za-z_0-9][A-Za-z_0-9,.+_-]*(?:@[A-Za-z_0-9,.+_-]+)?")
_LABEL = re.compile(rb"[A-Za-z_][A-Za-z_0-9]*")
_NUMBER = re.compile(rb"(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)")


class SourceError(ValueError):
    """Input cannot enter the source model, or exceeds its bounded resources."""


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    start: int
    end: int


@dataclass(frozen=True)
class Property:
    id: str
    name: str
    start: int
    end: int
    name_end: int
    value_start: int | None
    value_end: int | None
    kind: str = "property"
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    labels: tuple[str, ...]
    parent_id: str | None
    start: int
    end: int
    open_brace: int
    close_brace: int
    properties: tuple[Property, ...]
    children: tuple[str, ...]


@dataclass(frozen=True)
class Deletion:
    id: str
    target: str
    parent_id: str | None
    start: int
    end: int


@dataclass(frozen=True)
class Syntax:
    source: bytes
    sha256: str
    tokens: tuple[Token, ...]
    nodes: tuple[Node, ...]
    diagnostics: tuple[Diagnostic, ...]
    state: str
    blocked: bool
    deletions: tuple[Deletion, ...] = ()


def _check_source(source):
    if not isinstance(source, bytes):
        raise SourceError("Source must be bytes.")
    if len(source) > MAX_BYTES:
        raise SourceError("Source byte limit exceeded.")
    try:
        source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SourceError("Source must be valid UTF-8.") from exc
    if b"\x00" in source:
        raise SourceError("NUL bytes are unsupported.")


def _directive_name(value, position=0):
    match = _DIRECTIVE_NAME.match(value, position)
    return match[1] if match else None


def _lex(source):
    tokens, diagnostics = [], []
    position, line_trivia = 0, True
    while position < len(source):
        start, kind = position, "word"
        character = source[position:position + 1]
        if position == 0 and source.startswith(b"\xef\xbb\xbf"):
            position, kind = 3, "bom"
        elif character in b" \t\r\n\v\f":
            kind = "whitespace"
            while position < len(source) and source[position:position + 1] in b" \t\r\n\v\f":
                position += 1
        elif source.startswith(b"//", position):
            kind = "comment"
            newline = source.find(b"\n", position)
            position = len(source) if newline < 0 else newline
        elif source.startswith(b"/*", position):
            kind = "comment"
            end = source.find(b"*/", position + 2)
            if end < 0:
                position, kind = len(source), "opaque"
                diagnostics.append(Diagnostic("incomplete", "Unterminated comment.", start, position))
            else:
                position = end + 2
        elif character in (b'"', b"'"):
            kind = "string" if character == b'"' else "character"
            position += 1
            while position < len(source) and source[position:position + 1] != character:
                if source[position:position + 1] in (b"\r", b"\n"):
                    break
                if source.startswith(b"\\\r\n", position):
                    position += 3
                else:
                    position += 2 if source[position:position + 1] == b"\\" else 1
            if position >= len(source) or source[position:position + 1] != character:
                position, kind = len(source), "opaque"
                diagnostics.append(Diagnostic("incomplete", "Unterminated quoted value.", start, position))
            else:
                position += 1
        elif (character == b"#" and line_trivia and _directive_name(source, position) in _DIRECTIVES):
            kind = "directive"
            while True:
                newline = source.find(b"\n", position)
                if newline < 0:
                    position = len(source)
                    break
                continued = source[position:newline].rstrip(b"\r").endswith(b"\\")
                position = newline + 1
                if not continued:
                    break
        elif character == b"&" and (reference := _REFERENCE.match(source, position)):
            position, kind = reference.end(), "reference"
        elif character in b"{};=<>[](),:&":
            position, kind = position + 1, "punctuation"
        else:
            while position < len(source):
                if (source[position:position + 1] in b" \t\r\n\v\f{};=<>[](),:&\"'"
                        or source.startswith((b"//", b"/*"), position)):
                    break
                position += 1
        tokens.append(Token(start, position, kind))
        if kind in {"whitespace", "comment"}:
            if b"\n" in source[start:position]:
                line_trivia = True
        elif kind != "bom":
            line_trivia = kind == "directive" and source[position - 1:position] == b"\n"
        if len(tokens) > MAX_TOKENS:
            raise SourceError("Source token limit exceeded.")
    return tuple(tokens), diagnostics


class _Incomplete(Exception):
    def __init__(self, message, start, end):
        self.diagnostic = Diagnostic("incomplete", message, start, end)


class _Parser:
    def __init__(self, source, tokens, diagnostics):
        self.source, self.tokens = source, tokens
        self.significant = tuple(token for token in tokens if token.kind not in _TRIVIA)
        self.sha256 = hashlib.sha256(source).hexdigest()
        self.diagnostics, self.nodes, self.deletions = list(diagnostics), [], []
        self.blocked = bool(diagnostics)

    def value(self, token):
        return self.source[token.start:token.end]

    def identity(self, kind, start, end):
        return f"{self.sha256}:{kind}:{start}:{end}"

    def opaque(self, code, message, start, end, *, blocked=False):
        self.diagnostics.append(Diagnostic(code, message, start, end))
        self.blocked |= blocked

    def _end(self, tokens, start, stop, depth):
        stack = []
        openings = {b"{": b"}", b"(": b")", b"[": b"]", b"<": b">"}
        for index in range(start, stop):
            token, value = tokens[index], self.value(tokens[index])
            if token.kind != "punctuation":
                continue
            # C operators within macro/parenthesized expressions are opaque, not cell delimiters.
            if value in (b"<", b">") and b")" in stack:
                continue
            if value in openings:
                stack.append(openings[value])
                if depth + len(stack) > MAX_DEPTH:
                    raise SourceError("Source nesting limit exceeded.")
            elif value in openings.values():
                if not stack or stack.pop() != value:
                    raise _Incomplete("Unbalanced source delimiters.", token.start, token.end)
            elif value == b";" and not stack:
                return index
        end = tokens[stop - 1].end if stop > start else len(self.source)
        raise _Incomplete("Unbalanced delimiters or missing statement terminator.", tokens[start].start, end)

    def _header(self, tokens):
        labels, index = [], 0
        while index + 1 < len(tokens) and self.value(tokens[index + 1]) == b":":
            value = self.value(tokens[index])
            if not _LABEL.fullmatch(value):
                return (), None
            labels.append(value.decode("ascii"))
            index += 2
        remainder = tokens[index:]
        if not remainder:
            return tuple(labels), None
        value = self.source[remainder[0].start:remainder[-1].end]
        return tuple(labels), value

    def _property(self, tokens, terminator):
        equal = next((index for index, token in enumerate(tokens) if self.value(token) == b"="), None)
        header = tokens if equal is None else tokens[:equal]
        labels, name = self._header(header)
        if not name or not _PROPERTY_NAME.fullmatch(name):
            return None
        values = () if equal is None else tokens[equal + 1:]
        if equal is not None and not values:
            raise _Incomplete("Property assignment is missing its value.", tokens[0].start, terminator.end)
        parentheses = 0
        for token in values:
            value = self.value(token)
            if token.kind != "punctuation":
                continue
            if value == b"(":
                parentheses += 1
            elif value == b")":
                parentheses -= 1
            elif not parentheses and value == b"=":
                raise _Incomplete("Unexpected assignment in property value; a semicolon may be missing.",
                                  token.start, token.end)
            elif not parentheses and value in (b"{", b"}"):
                self.opaque("opaque-structure", "A structural value cannot be safely attributed to this property.",
                            token.start, token.end, blocked=True)
        if values and not self._simple_value(values):
            self.opaque("opaque-value", "Property expression is preserved without evaluation.",
                        values[0].start, values[-1].end)
        return Property(self.identity("property", tokens[0].start, terminator.end), name.decode("ascii"),
                        tokens[0].start, terminator.end, header[-1].end,
                        values[0].start if values else None, values[-1].end if values else None,
                        labels=labels)

    def _simple_value(self, tokens):
        if len(tokens) == 1 and tokens[0].kind == "string":
            return True
        if len(tokens) == 1 and tokens[0].kind == "reference":
            return True
        if len(tokens) % 2 and all(token.kind == "string" if index % 2 == 0 else self.value(token) == b","
                                  for index, token in enumerate(tokens)):
            return True
        if len(tokens) >= 3 and self.value(tokens[0]) == b"[" and self.value(tokens[-1]) == b"]":
            return all(re.fullmatch(rb"[0-9a-fA-F]{2}", self.value(token)) for token in tokens[1:-1])
        if len(tokens) >= 3 and self.value(tokens[0]) == b"<" and self.value(tokens[-1]) == b">":
            inside, items, separator = False, False, False
            for token in tokens:
                value = self.value(token)
                if value == b"<" and not inside and not separator:
                    inside, items = True, False
                elif value == b">" and inside and items:
                    inside, separator = False, True
                elif value == b"," and separator:
                    separator = False
                elif inside and (token.kind == "reference" or _NUMBER.fullmatch(value)):
                    items = True
                else:
                    return False
            return not inside and separator
        return False

    def body(self, tokens, start, stop, parent_id=None, depth=0):
        properties, children = [], []
        subnodes_started = False
        index = start
        while index < stop:
            first = tokens[index]
            value = self.value(first)
            if value == b"/include/" and index + 1 < stop and tokens[index + 1].kind == "string":
                last = tokens[index + 1]
                self.opaque("include", "Included source is preserved without loading it.", first.start, last.end)
                index += 2
                if index < stop and self.value(tokens[index]) == b";":
                    index += 1
                continue
            end = self._end(tokens, index, stop, depth)
            statement, terminator = tokens[index:end], tokens[end]
            index = end + 1
            if not statement:
                raise _Incomplete("Unexpected empty statement.", terminator.start, terminator.end)
            if value == b"/delete-node/":
                target = self.source[statement[1].start:statement[-1].end] if len(statement) > 1 else b""
                legal = _REFERENCE.fullmatch(target) if parent_id is None else _NODE_NAME.fullmatch(target)
                if legal:
                    self.deletions.append(Deletion(self.identity("delete-node", first.start, terminator.end),
                        target.decode("ascii"), parent_id, first.start, terminator.end))
                    subnodes_started = True
                    self.opaque("delete-node", "Node deletion is a source operation, not local declaration removal.",
                                first.start, terminator.end)
                else:
                    self.opaque("opaque-structure", "Unsupported node deletion target.",
                                first.start, terminator.end, blocked=True)
                continue
            if value == b"/delete-property/":
                name = self.source[statement[1].start:statement[-1].end] if len(statement) > 1 else b""
                if parent_id is not None and _PROPERTY_NAME.fullmatch(name):
                    if subnodes_started:
                        raise _Incomplete("Properties and property deletions must precede child nodes.",
                                          first.start, terminator.end)
                    properties.append(Property(self.identity("delete-property", first.start, terminator.end),
                        name.decode("ascii"), first.start, terminator.end, statement[-1].end,
                        None, None, "delete-property"))
                else:
                    self.opaque("opaque-structure", "Unsupported property deletion target.",
                                first.start, terminator.end, blocked=True)
                continue
            brace = next((offset for offset, token in enumerate(statement)
                          if self.value(token) in (b"{", b"=")), None)
            if brace is not None and self.value(statement[brace]) == b"{":
                labels, name = self._header(statement[:brace])
                if parent_id is None and name == b"/" and labels:
                    raise _Incomplete("The root node cannot carry a source label.", first.start, terminator.end)
                legal = name and ((name == b"/" or _REFERENCE.fullmatch(name)) if parent_id is None else _NODE_NAME.fullmatch(name))
                if legal and self.value(statement[-1]) == b"}":
                    node_id = self.identity("node", first.start, terminator.end)
                    slot = len(self.nodes)
                    self.nodes.append(None)
                    nested_properties, nested_children = self.body(statement, brace + 1, len(statement) - 1, node_id, depth + 1)
                    self.nodes[slot] = Node(node_id, name.decode("ascii"), labels, parent_id, first.start,
                        terminator.end, statement[brace].start, statement[-1].start,
                        tuple(nested_properties), tuple(nested_children))
                    children.append(node_id)
                    subnodes_started = True
                    continue
            elif parent_id is not None:
                prop = self._property(statement, terminator)
                if prop:
                    if subnodes_started:
                        raise _Incomplete("Properties and property deletions must precede child nodes.",
                                          first.start, terminator.end)
                    properties.append(prop)
                    continue
            if parent_id is None and len(statement) == 1 and value in {b"/dts-v1/", b"/plugin/"}:
                continue
            self.opaque("opaque-structure", "Unsupported structural statement is preserved.",
                        first.start, terminator.end, blocked=True)
        return properties, children

    def parse(self):
        for token in self.tokens:
            if token.kind == "bom":
                self.opaque("bom", "A UTF-8 BOM is preserved but not editable.", token.start, token.end, blocked=True)
            elif token.kind == "directive":
                conditional = _directive_name(self.value(token)) in _CONDITIONALS
                self.opaque("conditional" if conditional else "preprocessor",
                    "Conditional source requires preprocessing before safe targeting." if conditional else
                    "Preprocessor directive is preserved without evaluation.", token.start, token.end, blocked=conditional)
        token_index = 0
        for splice in re.finditer(rb"\\\r?\n", self.source):
            while self.tokens[token_index].end <= splice.start():
                token_index += 1
            if self.tokens[token_index].kind != "directive":
                self.opaque("line-splice", "Line splicing outside recognized directives is not editable.",
                            splice.start(), splice.end(), blocked=True)
        if not any(item.code == "incomplete" for item in self.diagnostics):
            try:
                self.body(self.significant, 0, len(self.significant))
            except _Incomplete as exc:
                self.diagnostics.append(exc.diagnostic)
                self.blocked = True
                self.nodes.clear()
                self.deletions.clear()
        state = "incomplete" if any(item.code == "incomplete" for item in self.diagnostics) else "opaque" if self.diagnostics else "complete"
        return Syntax(self.source, self.sha256, self.tokens, tuple(node for node in self.nodes if node is not None),
                      tuple(self.diagnostics), state, self.blocked, tuple(self.deletions))


def parse(source):
    """Index declarations while retaining unsupported and incomplete source exactly.

    ``complete`` means complete within this structural subset, not validated DTS.
    ``opaque`` may still admit edits to indexed declarations when ``blocked`` is false.
    ``incomplete`` and resource/encoding errors cannot be transformed by the engine.
    """
    _check_source(source)
    tokens, diagnostics = _lex(source)
    return _Parser(source, tokens, diagnostics).parse()
