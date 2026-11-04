# ADR 0008: Application source browsing before configuration

Status: accepted

## Context

Source navigation initially requires an inspected image with generated DTS. Newly
created applications need a useful read-only source view before configuration. Reusing
a fabricated inspection or treating a requested profile target as effective would blur
the existing distinction between current source content and build evidence.

## Decision

Add an explicit application mode in Device Tree. Projects links to its saved application
path through the shell; Device Tree also accepts a directory directly. It validates a
workspace-contained, non-symlink application directory with a readable `CMakeLists.txt`
marker. It does not import Projects internals, execute the marker or create a profile.

Extract a neutral current-source scope for catalogue IDs, hashes, budgets and document
navigation. Build scopes retain their existing artifact guard and inspection lifetime.
Application scopes pin the directory and marker instead, with separate authenticated
catalogue/document endpoints and no inspection/domain fields. No persistence changes.

Extend the public workspace reader with bounded directory scan filters and directory
identity checks. Device Tree owns the accepted filenames, generated/dependency directory
exclusions and conventional include roots. List application DTS/overlays/configuration
and local binding YAML; follow headers through literal includes. Treat application and
conventional workspace Zephyr include/binding roots as candidates, never recorded or
effective configuration. Do not infer the selected Zephyr checkout, board, applied
overlays, module roots or resolved nodes.

Reads require the original application identity and current catalogue hashes. App
replacement/deletion or source changes require a new catalogue. Browser mode/workspace
changes and disposal abort requests and clear text; reopening performs a fresh read.
The existing build inspection, node-binding and Memory handoff contracts remain intact.

## Consequences

- Applications can be browsed before the first configure/build, including applications
  with no saved creator profile.
- Source discovery is bounded and intentionally incomplete; generated files and common
  dependency trees are excluded, and custom roots require later explicit support.
- No compiler, preprocessor, YAML constructor, CMake or checkout code executes.
- Source editing, binding validation and per-property attribution remain separate work.
