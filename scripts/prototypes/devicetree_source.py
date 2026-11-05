"""D0 in-memory source-edit experiment; not a runtime parser or filesystem writer.

This deliberately small prototype keeps every UTF-8 source byte in token spans. It
can replace one existing string or single unsigned integer cell in an unambiguous
direct top-level &label or &{/path} block. It does not resolve includes, macros,
bindings, aliases, overlay order or effective hardware state. Conditionals and DTS
deletion directives are preserved by lexing but block all edits in this experiment.
General source parsing, insertion/deletion and production integration belong to D2.
"""

from dataclasses import dataclass
import hashlib
import re

MAX_BYTES = 256 * 1024
MAX_TOKENS = 32768
MAX_DEPTH = 64
_TRIVIA = {"whitespace", "comment", "directive"}
_DIRECTIVES = {
    b"include", b"define", b"undef", b"if", b"ifdef", b"ifndef", b"elif", b"else",
    b"endif", b"error", b"warning", b"pragma", b"line",
}
_CONDITIONALS = {b"if", b"ifdef", b"ifndef", b"elif", b"else", b"endif"}
_REFERENCE = re.compile(rb"&(?:[A-Za-z_][A-Za-z_0-9]*|\{/[A-Za-z_0-9,./+@-]*\})")
_NAME = re.compile(rb"[A-Za-z_#][A-Za-z_0-9,#.+?-]*")
_NUMBER = re.compile(rb"(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)")
_DIRECTIVE_NAME = re.compile(rb"#[ \t]*([A-Za-z]+)\b")


class SourceError(ValueError):
    """The source is malformed, over budget, ambiguous or outside the edit subset."""


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    kind: str


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


def lex(source):
    """Return complete, non-overlapping byte spans without interpreting source."""
    _check_source(source)
    tokens = []
    position = 0
    line_has_only_trivia = True
    while position < len(source):
        start = position
        kind = "word"
        character = source[position:position + 1]
        if character in b" \t\r\n\v\f":
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
                raise SourceError("Unterminated comment.")
            position = end + 2
        elif character in (b'"', b"'"):
            kind = "string" if character == b'"' else "character"
            position += 1
            while position < len(source) and source[position:position + 1] != character:
                if source[position:position + 1] in (b"\r", b"\n"):
                    raise SourceError("Unterminated quoted value.")
                position += 2 if source[position:position + 1] == b"\\" else 1
            if position >= len(source):
                raise SourceError("Unterminated quoted value.")
            position += 1
        elif (character == b"#"
              and line_has_only_trivia
              and _directive_name(source, position) in _DIRECTIVES):
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
            kind = "reference"
            position = reference.end()
        elif character in b"{};=<>[](),:&":
            kind = "punctuation"
            position += 1
        else:
            while position < len(source):
                if (source[position:position + 1] in b" \t\r\n\v\f{};=<>[](),:&\"'"
                        or source.startswith((b"//", b"/*"), position)):
                    break
                position += 1
            if position == start:
                raise SourceError("Unsupported source character.")
        tokens.append(Token(start, position, kind))
        if kind in {"whitespace", "comment"}:
            if b"\n" in source[start:position]:
                line_has_only_trivia = True
        else:
            line_has_only_trivia = kind == "directive" and source[position - 1:position] == b"\n"
        if len(tokens) > MAX_TOKENS:
            raise SourceError("Source token limit exceeded.")
    return tuple(tokens)


def round_trip(source):
    """Reconstruct the input from token spans, including all trivia and opaque text."""
    return b"".join(source[token.start:token.end] for token in lex(source))


def _statements(source, tokens):
    """Split structural statements without expanding or interpreting their contents."""
    openings = {b"{": b"}", b"<": b">", b"[": b"]", b"(": b")"}
    stack = []
    start = 0
    statements = []
    for index, token in enumerate(tokens):
        value = source[token.start:token.end]
        if token.kind == "punctuation":
            if value in openings:
                stack.append(openings[value])
                if len(stack) > MAX_DEPTH:
                    raise SourceError("Source nesting limit exceeded.")
            elif value in openings.values():
                if not stack or stack.pop() != value:
                    raise SourceError("Unbalanced source delimiters.")
            elif value == b";" and not stack:
                if start == index:
                    raise SourceError("Empty statements are outside the prototype subset.")
                statements.append(tokens[start:index])
                start = index + 1
    if stack or start != len(tokens):
        raise SourceError("Unbalanced delimiters or missing statement terminator.")
    return statements


def _literal(source, tokens):
    if len(tokens) == 1 and tokens[0].kind == "string":
        return "string", tokens[0]
    if (len(tokens) == 3 and source[tokens[0].start:tokens[0].end] == b"<"
            and source[tokens[2].start:tokens[2].end] == b">"):
        value = source[tokens[1].start:tokens[1].end]
        if (len(value) <= 10 and _NUMBER.fullmatch(value)
                and int(value, 16 if value.lower().startswith(b"0x") else 10) <= 0xffffffff):
            return "cell", tokens[1]
    raise SourceError("Only a string or one unsigned 32-bit literal cell is editable.")


@dataclass(frozen=True)
class BytePatch:
    """A single in-memory replacement guarded against any document change."""

    before_sha256: str
    after_sha256: str
    start: int
    end: int
    before: bytes
    replacement: bytes

    def apply(self, source):
        _check_source(source)
        if hashlib.sha256(source).hexdigest() != self.before_sha256:
            raise SourceError("Stale document: patch base hash does not match.")
        if not 0 <= self.start <= self.end <= len(source) or source[self.start:self.end] != self.before:
            raise SourceError("Patch source range does not match.")
        result = source[:self.start] + self.replacement + source[self.end:]
        _check_source(result)
        if hashlib.sha256(result).hexdigest() != self.after_sha256:
            raise SourceError("Patch result hash does not match.")
        return result

    def inverse(self):
        return BytePatch(self.after_sha256, self.before_sha256, self.start,
                         self.start + len(self.replacement), self.replacement, self.before)


def plan_literal_replacement(source, node_reference, property_name, replacement):
    """Plan one lexical leaf edit; no claim about the property's effective value.

    ``replacement`` is a complete quoted string or one integer cell, e.g. b'<200>'.
    For cells only the integer token changes, preserving existing brackets/trivia.
    Unknown RHS expressions, repeated definitions, label-prefixed target blocks and
    documents with conditionals/deletion directives are intentionally rejected.
    Structural delimiter checks do not establish that arbitrary opaque nested source
    obeys the complete DTS grammar.
    """
    try:
        reference = node_reference.encode("ascii")
        name = property_name.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise SourceError("Target reference and property name must be ASCII strings.") from exc
    if not _REFERENCE.fullmatch(reference) or not _NAME.fullmatch(name):
        raise SourceError("Invalid target reference or property name.")
    original_tokens = lex(source)
    if source.startswith(b"\xef\xbb\xbf"):
        raise SourceError("A UTF-8 BOM is preserved but not editable by this prototype.")
    # C line splicing can turn a physical next line into a comment or directive.
    # Only continuations already contained in a recognized directive are modeled.
    token_index = 0
    for continuation in re.finditer(rb"\\\r?\n", source):
        while original_tokens[token_index].end <= continuation.start():
            token_index += 1
        if original_tokens[token_index].kind != "directive":
            raise SourceError("Line splicing outside recognized directives is unsupported.")
    if any(token.kind == "directive" and _directive_name(source[token.start:token.end]) in _CONDITIONALS
           for token in original_tokens):
        raise SourceError("Conditional source is preserved but not editable by this prototype.")
    tokens = [token for token in original_tokens if token.kind not in _TRIVIA]
    if any(source[token.start:token.end] in (b"/delete-node/", b"/delete-property/") for token in tokens):
        raise SourceError("Deletion directives are preserved but block prototype edits.")
    targets = []
    for statement in _statements(source, tokens):
        brace = next((i for i, token in enumerate(statement)
                      if token.kind == "punctuation" and source[token.start:token.end] == b"{"), None)
        if brace is not None and any(source[token.start:token.end] == reference for token in statement[:brace]):
            if brace != 1 or source[statement[-1].start:statement[-1].end] != b"}":
                raise SourceError("Target block header is outside the direct-reference subset.")
            targets.append(statement[brace + 1:-1])
    if len(targets) != 1:
        raise SourceError("Target must have exactly one direct top-level definition.")
    values = []
    for statement in _statements(source, targets[0]):
        equal = next((i for i, token in enumerate(statement)
                      if source[token.start:token.end] in (b"=", b"{")), None)
        if equal is not None and source[statement[equal].start:statement[equal].end] == b"=":
            if source[statement[equal - 1].start:statement[equal - 1].end] == name:
                if equal != 1:
                    raise SourceError("Labeled properties are outside the literal-edit subset.")
                values.append(statement[equal + 1:])
        elif len(statement) == 1 and source[statement[0].start:statement[0].end] == name:
            raise SourceError("Boolean properties are outside the literal-edit subset.")
    if len(values) != 1:
        raise SourceError("Property must have exactly one direct literal assignment.")
    kind, token = _literal(source, values[0])
    replacement_tokens = lex(replacement)
    if re.search(rb"\\\r?\n", replacement):
        raise SourceError("Replacement line splicing is outside the literal-edit subset.")
    new_tokens = [item for item in replacement_tokens if item.kind not in _TRIVIA]
    # Whitespace inside a cell is harmless; comments/directives cannot be discarded.
    if any(item.kind in {"comment", "directive"} for item in replacement_tokens):
        raise SourceError("Replacement comments/directives are unsupported.")
    new_kind, new_token = _literal(replacement, new_tokens)
    if kind != new_kind:
        raise SourceError("Replacement must retain the literal kind.")
    new_value = replacement[new_token.start:new_token.end]
    result = source[:token.start] + new_value + source[token.end:]
    _check_source(result)
    return BytePatch(hashlib.sha256(source).hexdigest(), hashlib.sha256(result).hexdigest(),
                     token.start, token.end, source[token.start:token.end], new_value)
