"""Bounded board source closure and explicit preprocessing without project configuration."""

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile

from ...core.source_files import WorkspaceSourceReader
from .board_selection import resolve_board_selection


MAX_FILES = 1000
MAX_BYTES = 16 * 1024 * 1024
INCLUDE = re.compile(r'(?m)^(\s*#\s*)(include_next|include|import|embed)\b[ \t]*([^\n]+)$')
COMMENTS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*', re.S)


def _preprocessing_text(value):
    if "??" in value or "%:" in value:
        raise ValueError("Alternative preprocessing directive tokens need explicit source support.")
    logical = re.sub(r"\\\r?\n", "", value)
    logical = COMMENTS.sub(lambda match: " " + "\n" * match[0].count("\n")
                           if match[0].startswith(("/*", "//")) else match[0], logical)
    if re.search(r"(?m)^\s*#\s*(?:line\b|[0-9])", logical):
        raise ValueError("Input line-marker directives would obscure board source provenance.")
    lines, pending, count = [], "", 0
    for number, line in enumerate(value.splitlines(keepends=True), 1):
        if re.search(r"\\\r?\n$", line):
            pending += re.sub(r"\\\r?\n$", "", line)
            count += 1
            continue
        lines.append(pending + line)
        if count:
            lines.append(f"\n#line {number + 1}\n")
        pending, count = "", 0
    if pending:
        raise ValueError("A trailing preprocessing continuation needs source repair.")
    return COMMENTS.sub(lambda match: (" " + "\n" * match[0].count("\n"))
                        if match[0].startswith(("/*", "//")) else match[0], "".join(lines))


def _signature(path):
    path = Path(path)
    info = path.stat()
    return (str(path.resolve()), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class BoardSources:
    """Fingerprint both present inputs and absent higher-priority include candidates."""

    def __init__(self, context, draft):
        self.context = context
        self.reader = WorkspaceSourceReader(context["workspace"])
        self.files, self.absent, self.tools, self.staged = {}, set(), {}, {}
        self.total = 0
        self.board = context.get("board") or (context.get("environment") or {}).get("board")
        if not isinstance(self.board, dict) or self.board.get("status") != "discovered":
            raise ValueError("This selection has no discovered board metadata. Analyze its environment and select an exact board configuration.")
        target = context.get("target") or {}
        if target.get("requested") not in self.board.get("targets", []):
            raise ValueError("The exact target does not match the captured board metadata.")
        if len(self.board.get("directories", [])) != 1 or target.get("revision") or self.board.get("default_revision") is not None:
            raise ValueError("Board extensions and revision selection require a separate discovery adapter; this board remains visible without inferred topology.")
        environment = context.get("environment") or {}
        self.zephyr = self.reader.path((environment.get("zephyr") or {}).get("path"))
        self.directory = self.reader.path(self.board["directories"][0])
        self.selection = resolve_board_selection(self, context, self.board, self.directory)
        self.board_file = Path(self.selection["board_source"])
        application = self.reader.path(context["application"]["path"])
        if any(context.get(key) for key in ("shields", "snippets")):
            raise ValueError("Shield and snippet composition is outside this board observation.")
        predicted = (context.get("input_evidence") or {}).get("predicted", [])
        selected = []
        for report in predicted:
            if report.get("status") in {"unknown", "unavailable", "ambiguous"}:
                raise ValueError("Overlay composition is unresolved. Select this single overlay explicitly in Projects.")
            selected.extend(self.reader.path(item["path"], base=application) for item in report.get("files", [])
                            if item.get("state") in {"active", "expected", "selected"})
        if selected != [Path(draft.path)]:
            raise ValueError("Board hardware discovery currently requires this draft to be the only selected application overlay.")
        roots = [self.reader.path(item["path"]) for item in context.get("roots", []) if item.get("kind") == "dts"]
        roots += [application, self.directory, self.zephyr]
        self.roots = list(dict.fromkeys(roots))
        arches = [self.selection["architecture"]]
        self.include_dirs = [root / suffix for root in self.roots
                             for suffix in ["include", "include/zephyr", "dts/common", "dts/vendor", *["dts/" + arch for arch in arches], "dts"]
                             if self.exists(root / suffix, directory=True)]
        self.binding_roots = [str(root / "dts/bindings") for root in self.roots if self.exists(root / "dts/bindings", directory=True)]
        python = next((tool.get("path") for tool in environment.get("tools", []) if tool.get("name") == "Python"), None)
        compilers = (environment.get("sdk") or {}).get("toolchains", [])
        compiler = next((tool.get("compiler") for tool in compilers if tool.get("family") == "GNU"), None)
        if not python or not compiler:
            raise ValueError("The selected environment needs its recorded Python and GNU preprocessor; no tools are installed or configured automatically.")
        self.python, self.compiler = python, compiler
        for path in (python, compiler):
            self.tools[path] = _signature(path)
        self.library = self.zephyr / "scripts/dts/python-devicetree/src"
        parser_files = self.reader.list_files(self.library / "devicetree", {".py"}, max_files=32, max_dirs=16,
                                             exclude_dirs={"__pycache__"})
        self.parser_files = parser_files
        if not any(path.name == "dtlib.py" for path in parser_files):
            raise ValueError("The selected Zephyr checkout lacks its devicetree parser.")
        for path in parser_files:
            self.read(path)

    def exists(self, path, *, directory=False):
        path = self.reader.path(path)
        try:
            if directory:
                self.reader.directory_identity(path)
            else:
                self.reader.read_text(path, limit=2 * 1024 * 1024)
            return True
        except FileNotFoundError:
            self.absent.add(str(path))
            return False

    def read(self, path):
        path = self.reader.path(path)
        if str(path) in self.files:
            return self.files[str(path)]["text"]
        value = self.reader.read_text(path)
        self.total += len(value["text"].encode())
        if len(self.files) >= MAX_FILES or self.total > MAX_BYTES:
            raise ValueError("Board source closure exceeds its file or byte limit.")
        self.files[str(path)] = value
        return value["text"]

    def revalidate(self):
        if self.reader.list_files(self.directory, {".yaml"}, max_files=1000, max_dirs=1,
                                  exclude_prefixes=("",)) != self.platform_files:
            raise ValueError("The board platform metadata catalogue changed. Load board hardware again.")
        if self.reader.list_files(self.library / "devicetree", {".py"}, max_files=32, max_dirs=16,
                                  exclude_dirs={"__pycache__"}) != self.parser_files:
            raise ValueError("The selected parser source catalogue changed. Load board hardware again.")
        for path, value in self.files.items():
            if self.reader.read_text(path)["sha256"] != value["sha256"]:
                raise ValueError("A board discovery input changed. Load board hardware again.")
        for path in self.absent:
            if Path(path).exists() or Path(path).is_symlink():
                raise ValueError("A previously absent board/include candidate appeared. Load board hardware again.")
        if any(_signature(path) != signature for path, signature in self.tools.items()):
            raise ValueError("A selected board discovery tool changed. Load board hardware again.")

    def _stage(self, path, destination, *, text=None):
        path = self.reader.path(path)
        if str(path) in self.staged:
            return self.staged[str(path)]
        value = self.read(path) if text is None else text
        # Translation phases splice lines and replace comments before recognizing
        # directives. Validate that spelling, rather than the raw line layout.
        value = _preprocessing_text(value)
        if re.search(r"\b(?:_Pragma|__pragma|__has_include|__has_include_next)\b", value):
            raise ValueError("Preprocessor file-probe and pragma operators are outside bounded board discovery.")
        if any(match[1].strip() != "once" for match in re.finditer(r"(?m)^\s*#\s*pragma\b([^\n]*)", value)):
            raise ValueError("Only #pragma once is supported; other pragmas may access additional files.")
        if re.search(r"/(?:include|incbin)/", value):
            raise ValueError("DTS /include/ and /incbin/ directives need bounded source support before board discovery.")
        output = destination / (hashlib.sha256(str(path).encode()).hexdigest() + path.suffix)
        self.staged[str(path)] = output

        def replace(match):
            literal = re.fullmatch(r'[<"]([^>"\n]+)[>"]\s*(?://[^\n]*|/\*.*\*/)?\s*', match[3])
            if match[2] != "include" or literal is None or Path(literal[1]).is_absolute():
                raise ValueError("Only bounded literal #include directives are supported in board discovery.")
            search = ([path.parent] if match[3].lstrip().startswith('"') else []) + self.include_dirs
            found = None
            for parent in search:
                candidate = Path(os.path.abspath(parent / literal[1]))
                candidate = self.reader.path(candidate)
                if not any(candidate.is_relative_to(root) for root in self.roots):
                    raise ValueError("A board include escapes its captured source roots.")
                if self.exists(candidate):
                    found = candidate
                    break
            selected = self._stage(found, destination) if found else destination / "missing-include"
            return match[1] + 'include "' + str(selected) + '"'

        output.write_text(INCLUDE.sub(replace, value))
        return output

    def run(self, draft):
        with tempfile.TemporaryDirectory(prefix="zephyr-board-") as temporary:
            stage = Path(temporary)
            overlay = self._stage(Path(draft.path), stage, text=draft.source.decode())
            board = self._stage(self.board_file, stage)
            preprocessed, result_path = stage / "board.pre", stage / "result.json"
            empty = stage / "empty.c"
            empty.write_text("")
            command = [self.compiler, "-x", "assembler-with-cpp", "-nostdinc", "-undef", "-D__DTS__",
                       "-include", str(board), "-include", str(overlay), "-E", "-o", str(preprocessed), str(empty)]
            self._run(command, stage)
            if preprocessed.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Preprocessed board source exceeds 2 MiB.")
            if re.search(r"/(?:include|incbin)/", preprocessed.read_text()):
                raise ValueError("Preprocessing produced a DTS file-read directive outside the bounded input closure.")
            request = stage / "request.json"
            library = stage / "python"
            for original in self.parser_files:
                copied = library / original.relative_to(self.library)
                copied.parent.mkdir(parents=True, exist_ok=True)
                copied.write_text(self.files[str(original)]["text"])
            request.write_text(json.dumps({"library": str(library), "source": str(preprocessed), "output": str(result_path)}))
            self._run([self.python, "-I", "-B", str(Path(__file__).with_name("board_worker.py")), str(request)], stage)
            if result_path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Board topology output exceeds 4 MiB.")
            result = json.loads(result_path.read_text())
            encoder = self.files.get(str(self.zephyr / "include/zephyr/dt-bindings/pinctrl/stm32-pinctrl.h"), {})
            result["decoders"] = {"stm32-pinmux-v1": encoder.get("sha256") == "c76c396f9e907d199eb6fb178fbd657c513202444f8d5fc77cc0d2fcc0a02f54"}
            reverse = {str(value): key for key, value in self.staged.items()}
            for node in result["nodes"]:
                for source in [node["source"], *node["property_sources"].values()]:
                    if source["path"] not in reverse:
                        raise ValueError("A parsed board location is outside the observed source closure.")
                    source["path"] = reverse[source["path"]]
            self.revalidate()
            return result

    @staticmethod
    def _run(command, directory):
        environment = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT"}}
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["TMPDIR"] = str(directory)
        request = Path(directory) / "limited-command.json"
        request.write_text(json.dumps({"command": command}))
        wrapper = [sys.executable, "-I", "-B", str(Path(__file__).with_name("board_worker.py")), str(request)]
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(wrapper, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, start_new_session=True)
            try:
                result = process.wait(timeout=15)
            except subprocess.TimeoutExpired as exc:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise ValueError("Board discovery exceeded its 15-second tool limit.") from exc
            log.seek(0)
            message = log.read(16384).decode("utf-8", errors="replace")
            if result:
                raise ValueError("Board discovery failed: " + message)
