# ADR 0007: Read-only current source and binding navigation

Status: accepted

## Context

The resolved Device Tree reader preserves generated output identity, but users also need
to inspect overlays, board definitions and bindings. Saved paths do not establish that
current source contents match an earlier build. Executing a checkout's Python parser,
unpickling build objects or trusting metadata-selected external paths would cross the
existing data and execution boundaries.

## Decision

Keep source and binding catalogue semantics in `features/devicetree/`. Add a focused
public `WorkspaceSourceReader` in `core/source_files.py` using the existing descriptor-based
read primitives. It confines bounded UTF-8 reads and directory enumeration to a selected
workspace without symlink traversal. It does not decide which references appear in the UI.

Each transient source catalogue is bound to one inspection and domain. A detached build
selection pins resolved/preprocessed DTS, generated header and their context, including
expected absence. Source requests also require the original inspection to remain valid.
Source references come from recorded input lists, verified preprocessor line markers,
supported generated-header binding comments, and literal includes or binding searches.
The client receives opaque file IDs; it cannot supply arbitrary paths to a read endpoint.

Capture current file hashes when offering them. Reads require those bytes to remain
unchanged and recheck the selected build context. Source edits require an explicit new
catalogue. All opened source text is labeled current content, with build-source freshness
unknown. External paths and unavailable files are explicit rather than silently omitted
or granted additional filesystem access.

Binding search parses bounded YAML as data from the recorded binding directories. Show
all compatible candidates and their bus metadata without claiming an effective winner.
Supported header comments can separately identify the binding path recorded by a build;
they do not prove current YAML equals the historical source. Includes are navigation links,
not recursive schema merging. Preserve ambiguous basenames and explain unsupported tags,
filters and child-binding inheritance. Property summaries show only local declarations.

The shared authenticated router serves explicit catalogue, binding-search and document
requests. No tool execution, editing, configuration or build action is introduced. Keep
catalogues/indexes bounded and ephemeral; saved project, build, environment and Memory
formats are unchanged. Browser refresh, workspace/image changes and disposal cancel local
requests and discard source text; they do not cancel server jobs.

## Consequences

- Users can navigate from a resolved node to binding evidence/candidates and read includes.
- Source and generated-output identities remain distinct and independently rechecked.
- Current source inspection requires a readable existing configured image in this slice.
- Source reads outside the selected workspace, effective binding resolution, full dependency
  expansion and per-property source attribution remain unsupported.
- Source edits and binding validation still require later explicit workflows.
