# ADR 0011: Ordered application fragment selections

Status: accepted

## Context

Named configurations already capture target and SDK selections. Users also need to
choose existing software configuration fragments and Device Tree overlays for an
application. Zephyr distinguishes automatic discovery, appended files and replacement
application-file lists. Treating these as one checkbox list would hide that distinction.

## Decision

Extend named configurations with independent Kconfig and overlay categories. Each has
an `auto`, `append` or `replace` mode and an ordered relative-path list. Automatic lists
are empty; appended/replacement lists contain 1–32 unique files. No empty replacement
is exposed. Pass absolute application paths as a single ordered CMake list argument:
`EXTRA_CONF_FILE` / `EXTRA_DTC_OVERLAY_FILE` for append, and
`CONF_FILE` / `DTC_OVERLAY_FILE` for replace. Automatic mode passes neither override.
Board defaults and other Zephyr/application CMake inputs still participate.

Discover files through a bounded, read-only Project Creator endpoint. Reuse the public
core source reader, rooted at the captured application directory, without importing
Inspector internals. Reject symlinks, traversal, non-text files, excluded output/tool
paths and CMake expansion/list-control characters. Revalidate files on Save and build
preview; the existing application input fingerprint rejects edits after preview.
Saving records choices only and never changes firmware sources or launches a job.

Use profile `schema_version: 2` when a `fragments` envelope is supplied. The envelope
has its own `schema_version: 1` and explicit category modes/lists. Outer configuration
and attempt records retain version 1. Version-1 profiles without fragments remain
automatic and are not rewritten. Older readers already reject unknown profile versions,
so they cannot accidentally execute a new selection as an automatic build. Reject
unknown versions and inconsistent profile/envelope combinations without modifying data.

Choices reference current files; contents are captured by each reviewed build, not
frozen when naming the configuration. Show modes and list order in ordinary summaries,
reviews and attempt history. Keep command arrays and diagnostic detail collapsed.
Missing selected files stay visible and block saving until resolved or explicitly removed.

## Consequences

- One app can have several repeatable file-selection recipes while remaining editable
  outside Utilities. A recipe is not an immutable copy of all transitive inputs.
- Configuration and build remain separate explicit actions. They establish software
  processing/compilation evidence, not peripheral wiring or hardware validity.
- Visual editing, fragment creation, external module selections, portable export,
  shields/snippets and sysbuild remain separate future slices.
