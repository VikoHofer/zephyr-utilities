"""Bounded structural reader for generated DTS text, without evaluating values."""

from dataclasses import dataclass
import re


MAX_BYTES = 4 * 1024 * 1024
MAX_TOKENS = 200000
MAX_NODES = 5000
MAX_PROPERTIES = 30000
MAX_DEPTH = 64
NAME = re.compile(r"[a-zA-Z0-9,._+*#?@-]+\Z")
LABEL = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*\Z")


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int
    line: int


def _tokens(text):
    position, line, tokens = 0, 1, []
    punctuation = "{};=:<>[]()"
    while position < len(text):
        start, start_line = position, line
        if text[position].isspace():
            line += text[position] == "\n"
            position += 1
            continue
        if text.startswith("/*", position):
            end = text.find("*/", position + 2)
            if end < 0:
                raise ValueError(f"Unterminated comment at line {line}.")
            line += text[position:end + 2].count("\n")
            position = end + 2
            continue
        if text.startswith("//", position):
            end = text.find("\n", position + 2)
            position = len(text) if end < 0 else end
            continue
        if text[position] == '"':
            position += 1
            while position < len(text):
                character = text[position]
                if character == "\\":
                    position += 2
                elif character == '"':
                    position += 1
                    break
                elif character == "\n":
                    raise ValueError(f"Unterminated string at line {start_line}.")
                else:
                    position += 1
            else:
                raise ValueError(f"Unterminated string at line {start_line}.")
        elif text[position] in punctuation:
            position += 1
        else:
            while (position < len(text) and not text[position].isspace()
                   and text[position] not in punctuation + '"'
                   and not text.startswith(("/*", "//"), position)):
                position += 1
        tokens.append(Token(text[start:position], start, position, start_line))
        if len(tokens) > MAX_TOKENS:
            raise ValueError("The structural view exceeds its token limit.")
    return tokens


class _Parser:
    def __init__(self, text):
        self.text = text
        self.tokens = _tokens(text)
        self.position = 0
        self.nodes = []
        self.paths = set()
        self.property_count = 0

    def peek(self, offset=0):
        index = self.position + offset
        return self.tokens[index].value if index < len(self.tokens) else None

    def take(self, expected=None):
        if self.position >= len(self.tokens):
            raise ValueError("Unexpected end of resolved DTS text.")
        token = self.tokens[self.position]
        if expected is not None and token.value != expected:
            raise ValueError(f"Expected {expected!r} at line {token.line}.")
        self.position += 1
        return token

    def labels(self):
        labels = []
        while self.peek(1) == ":":
            token = self.take()
            if not LABEL.fullmatch(token.value):
                raise ValueError(f"Unsupported label at line {token.line}.")
            labels.append(token.value)
            self.take(":")
        return labels

    def value(self):
        start = self.position
        stack = []
        pairs = {">": "<", "]": "[", ")": "(", "}": "{"}
        while self.peek() is not None:
            token = self.take()
            if token.value == ";" and not stack:
                if self.position - 1 == start:
                    raise ValueError(f"Empty property assignment at line {token.line}.")
                return self.text[self.tokens[start].start:token.start].strip()
            if token.value == ";":
                raise ValueError(f"Unclosed property value at line {token.line}.")
            if token.value in {"/include/", "/incbin/", "/delete-node/", "/delete-property/"}:
                raise ValueError(f"Unresolved source directive at line {token.line}.")
            if token.value in {"<", "[", "("}:
                stack.append(token.value)
            elif token.value == "{":
                previous = self.tokens[self.position - 2].value if self.position >= 2 else None
                if previous != "&":
                    raise ValueError(f"Unsupported property syntax at line {token.line}.")
                stack.append(token.value)
            elif token.value in pairs:
                if not stack or stack.pop() != pairs[token.value]:
                    raise ValueError(f"Unbalanced property value at line {token.line}.")
            if len(stack) > MAX_DEPTH:
                raise ValueError("The structural view exceeds its value nesting limit.")
        raise ValueError("Unterminated property value.")

    def node(self, token, labels, parent=None, depth=0):
        if depth > MAX_DEPTH or len(self.nodes) >= MAX_NODES:
            raise ValueError("The structural view exceeds its node count or depth limit.")
        if parent is None:
            if token.value != "/":
                raise ValueError("Expected a resolved root node; source overlays are not supported here.")
            path = "/"
        else:
            if not NAME.fullmatch(token.value):
                raise ValueError(f"Unsupported node name at line {token.line}.")
            path = parent.rstrip("/") + "/" + token.value
        if path in self.paths:
            raise ValueError(f"Repeated node {path}; inspect the raw text instead.")
        self.paths.add(path)
        item = {"path": path, "name": token.value, "parent": parent, "depth": depth,
                "labels": labels, "line": token.line, "properties": []}
        self.nodes.append(item)
        property_names = set()
        self.take("{")
        while self.peek() != "}":
            labels = self.labels()
            name = self.take()
            if self.peek() == "{":
                self.node(name, labels, path, depth + 1)
                continue
            if not NAME.fullmatch(name.value) or name.value in property_names:
                raise ValueError(f"Unsupported or repeated property at line {name.line}.")
            property_names.add(name.value)
            boolean = self.peek() == ";"
            if boolean:
                self.take(";")
                value = ""
            else:
                self.take("=")
                value = self.value()
            item["properties"].append({"name": name.value, "value": value, "boolean": boolean,
                                       "line": name.line, "labels": labels})
            self.property_count += 1
            if self.property_count > MAX_PROPERTIES:
                raise ValueError("The structural view exceeds its property count limit.")
        self.take("}")
        self.take(";")

    def parse(self):
        self.take("/dts-v1/")
        self.take(";")
        reservations = []
        while self.peek() is not None:
            labels = self.labels()
            token = self.take()
            if token.value == "/memreserve/" and not self.nodes:
                reservations.append({"labels": labels, "value": self.value(), "line": token.line})
            else:
                self.node(token, labels)
        if not self.nodes:
            raise ValueError("The resolved DTS contains no root node.")
        return {"status": "parsed", "nodes": self.nodes, "reservations": reservations, "diagnostics": []}


def parse_resolved_dts(text):
    """Expose structure and verbatim values, not binding validation or a compiler."""
    try:
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError("The resolved DTS exceeds the 4 MiB structural-view limit.")
        return _Parser(text).parse()
    except ValueError as exc:
        return {"status": "unsupported", "nodes": [], "reservations": [], "diagnostics": [
            f"Node navigation is unavailable: {exc} The resolved text remains available below."]}
