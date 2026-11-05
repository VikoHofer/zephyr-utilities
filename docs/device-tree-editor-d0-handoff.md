# Device Tree Editor D0 handoff

Status: D0 complete, 2026-11-05. The runtime editor remains planned. The accepted scope
was foundation contracts, an initial support matrix, and a bounded source-edit experiment;
no D1 workspace UI or firmware writer was authorized by this stage.

## Delivered scope

- [Foundation contract](device-tree-editor-contract.md): ownership, preserved Inspector
  aliases, minimal version-1 context/draft/change-plan shapes, exact target references,
  source capability limits, and future filesystem concurrency requirements.
- [ADR 0019](decisions/0019-device-tree-editor-foundation.md): canonical source drafts,
  separate semantic interpretation, editor namespace, public Projects handoff and staged
  persistence/execution boundaries.
- [In-memory prototype](../scripts/prototypes/devicetree_source.py): lossless token spans,
  one existing string or unsigned 32-bit numeric-cell edit, whole-document hash guards
  and exact inverse patches. It is a development experiment outside the installed app.
- [Regression tests](../tests/test_devicetree_source_prototype.py) and six synthetic
  fixtures: literal/path-reference input, repeated overrides, conditionals, deletion
  directives and structurally malformed input. Tests also construct CRLF/mixed endings,
  Unicode, BOM, continued directives/comments and stale-patch cases.

Existing documentation changes from the earlier planning task were retained and updated
only to record D0 progress. No production code, feature registration, shared interface,
runtime dependency, application source, build output or saved user record was changed.
No server or background build process was started. No commit was created.

## Verification

Commands ran with the existing workspace development environment activated:

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests -p test_devicetree_source_prototype.py -v` | 22 tests passed |
| `python scripts/check.py --quick` | Passed: Ruff, syntax, architecture, 560 Python tests and 23 frontend checks; 17 loopback tests excluded |
| `python scripts/check.py` | 577 Python tests attempted: 560 passed, 17 failed at loopback socket creation with `PermissionError: [Errno 1] Operation not permitted`; all 23 frontend checks passed |
| Documentation local-link/structure checks and `git diff --check` | Checked before handoff |

The existing direct-dispatch Inspector tests pass in quick mode and verify shared
catalogue IDs and service identity across canonical/legacy routes. Existing feature and
route tests retain planned-feature restrictions and hash redirects. D1 must add actual
editor-provider and both-registration-order coverage with its consumer.

The 17 full-suite errors are the environment's blocked local sockets, not assertions
against the prototype. HTTP integration remains unverified in this sandbox; rerun the
full command in an environment permitting loopback before treating that coverage as
passing. No test was skipped or weakened to conceal the restriction.

No browser/package smoke test was required: D0 adds no browser assets or runtime UI.
No selected-environment configure/build or device validation was performed. The Nordic
and STM32 targets are local metadata observations, not hardware support certifications.

### Performance observation

On Python 3.14.10, Linux x86_64, 20 plan/apply/inverse cycles per generated document
produced the following measurements. The documents contain a target node plus independent
nodes with long string properties and a final padding comment; they exercise bytes,
tokens and source traversal, not preprocessing or a browser graph. The full check was
also running during this observation.

| Document | Nodes | Tokens | Median cycle | Maximum cycle |
| --- | --- | --- | --- | --- |
| 65,536 bytes | 238 | 4,992 | 15.34 ms | 16.83 ms |
| 262,144 bytes | 951 | 19,965 | 62.59 ms | 64.49 ms |

Both fit the proposed development budgets of 100 ms at 64 KiB and 1 s near the prototype
byte limit. These are observations on this synthetic workload, not worst-case timing or
portable performance guarantees. Byte/token/depth limit rejection has behavioral tests;
there are no timing assertions in the normal suite.

Reproduce from the repository root with the development environment activated:

```sh
python - <<'PY'
from pathlib import Path
import importlib.util
import platform
import statistics
import sys
import time

spec = importlib.util.spec_from_file_location(
    'd0_benchmark', Path('scripts/prototypes/devicetree_source.py'))
prototype = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prototype
spec.loader.exec_module(prototype)
print(platform.python_version(), platform.system(), platform.machine())
for size in (64 * 1024, prototype.MAX_BYTES):
    source = bytearray(b'&uart0 { status = "disabled"; };\n')
    index = 0
    while True:
        entry = (f'&device_{index} {{ status = "disabled"; description = "'
                 + 'x' * 220 + '"; };\n').encode()
        if len(source) + len(entry) + 8 > size:
            break
        source.extend(entry)
        index += 1
    source.extend(b'/*' + b' ' * (size - len(source) - 4) + b'*/')
    source = bytes(source)
    timings = []
    for _ in range(20):
        start = time.perf_counter()
        edit = prototype.plan_literal_replacement(
            source, '&uart0', 'status', b'"okay"')
        candidate = edit.apply(source)
        assert edit.inverse().apply(candidate) == source
        timings.append((time.perf_counter() - start) * 1000)
    print(len(source), index + 1, len(prototype.lex(source)),
          statistics.median(timings), max(timings))
PY
```

## Limits and decisions to retain

The prototype preserves unrelated macros/includes but never resolves them. Conditional
and deletion-directive documents, ambiguous/labeled target definitions, nonliteral
values and unmodeled line splicing cannot be edited. BOM input round-trips but is not
editable. This is lexical/source-range evidence, not a full DTS grammar validator or
proof of an effective hardware setting. Limits are 256 KiB, 32,768 tokens and 64 nested
delimiters during planning. No source insertion/deletion or filesystem operation exists.

Future runtime routes use `/api/devicetree/editor/` and the service key
`devicetree_editor`; Inspector keeps `devicetree` as its service alias. Source selection
must distinguish registration revision, original/named configuration revision, fragment
intent and captured historical evidence. Versioned persistent records and the actual
core provider are deferred to their consuming stages.

## Next task

Implement **D1 only** from the [master plan](device-tree-editor-masterplan.md):

1. Add the narrow read-only public project-context provider with a Projects-owned
   implementation and explicit composition. Do not use build preview to load context.
2. Load registered Original/named configurations and partial unregistered application
   directories. Preserve exact target, availability, ordered fragments and separate
   predicted/captured overlay evidence. Opening must not start tools/jobs or write sources.
3. Add the scoped editor workspace through a replacement development/test feature
   definition, preserving the default planned definition. Do not add duplicate feature IDs.
4. Verify both Inspector/editor registration orders, aliases, stale revisions, missing
   sources, workspace changes and late responses; perform D1 browser/package checks for
   its new assets. Leave all mutation/Save controls unavailable.

D1 does not need to promote or import the prototype into the application. Production
source-edit design is D2, live drafts D3, and guarded publication D4. Stop at the D1 exit
gate rather than starting those stages automatically.
