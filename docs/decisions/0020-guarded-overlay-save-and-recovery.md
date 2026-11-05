# ADR 0020: Guarded overlay Save and durable draft recovery

Status: accepted for Device Tree Editor D4, 2026-11-05.

## Context

D3 supplies source-preserving edits and unified undo in session memory. Making the editor
available requires a reviewed file writer and recovery that can distinguish draft text,
current files and uncertain publication. A pre-write hash followed by unconditional
replacement cannot protect a reviewed original from a concurrent writer.

## Decision

Expose only the delivered existing-overlay workflow in the normal application. Hardware
modules, pins, semantic validation and configure/build execution remain later stages.
Keep Inspector's legacy routes and service alias unchanged.

`SavingService` creates bounded ten-minute plans for one existing overlay, containing exact
candidate source, a diff, source/context preconditions and unresolved semantic/usage evidence.
Only an explicit Save consumes a plan. Repeated accepted requests return the same receipt;
unchanged source needs no replacement. Rebinding the context after Save changes the baseline
while preserving unified history, so Undo creates an unsaved inverse.

`OverlayPublisher` requires Linux directory locking and `renameat2(RENAME_EXCHANGE)`.
It opens each ancestor without following symlinks and pins directory and file identities.
The participating-writer key is parent device/inode plus basename; a directory `flock`
also coordinates separate application processes, conservatively serializing that parent.
Projects currently creates new overlays and replaces only CMake source; it does not replace
existing overlays. There is therefore no shared same-resource writer to extract yet. A
future second writer must join this boundary before replacing an existing overlay.

Publication requires a singly linked regular file owned by the current user, writable
permissions and a bounded UTF-8 source. It preserves owner/group, ordinary permission bits
and bounded extended attributes, including ACL/security labels, or refuses before exchange.
Special permission bits are unsupported. Candidate bytes are staged on the same filesystem
and flushed. A version-1 publication journal durably records original and candidate bytes
before exchange. The writer then rechecks source/context, exchanges, flushes the directory,
and verifies the displaced original plus the published candidate before reporting success.

Pre-exchange refusal leaves the destination untouched. Post-exchange interference or a
durability failure is an uncertain outcome: the candidate may already be visible. Keep
the displaced file and exact original/candidate evidence. Do not automatically roll back,
retry, or replay on restart; rollback itself could overwrite another external write.
Same-session completed receipts are cached only after durable completion. Disk records
loaded after restart are publication evidence requiring comparison, even when their last
recorded state was saved; they are not fresh evidence about current disk contents.

The editor cannot exclude arbitrary writes through external descriptors during or after
exchange. Directory locks coordinate only participating writers. A successful receipt
records verified publication at that time, not continuing ownership of the file.

`DraftStore` keeps version-1 UTF-8 JSON records under
`feature_data("devicetree")/drafts/`, with source/base hashes, exact text, bounded history,
selection and file observations. Acknowledgement follows persistence; storage failures
leave pending browser text available and expose recovery uncertainty. Unknown or malformed
records are preserved. D3 had no persistent format, so no migration runs. Newer versions
must add an explicit migration policy rather than reinterpret these records.

Recovery never publishes firmware. Users compare a record against current source before
restoring it as an unsaved draft or discarding it. Comparisons bind a revision and current
file identity with expiry; a new external change invalidates the choice. Application moves,
identity replacement and changed project selections cannot silently retarget recovery.
Unavailable sources remain exportable. Unsent browser typing is not durable across page
closure; the interface distinguishes it from acknowledged recovery data.

## Limits and consequences

- Existing-file replacement only; missing files require external recreation and a new
  context, or source export. No automatic overlay creation or recipe changes.
- Known incomplete source cannot be saved. Losslessly retained opaque source can be
  saved without claiming semantic validation, binding correctness or hardware validity.
- At most 16 session drafts, 8 MiB retained source and 32 history entries per draft.
  Durable recovery has at most 64 records and 32 MiB total, with a 16 MiB record ceiling.
- At most 64 publication journals, each at most 4 MiB. Hidden displaced files remain beside
  their overlay. Evidence is not silently evicted; capacity exhaustion requires preservation
  and deliberate cleanup. Unchanged Save does not consume a publication journal.
- Review/comparison pools hold at most 32 entries each; completed Save responses hold 64.
- Recovery metadata remains separate from ordinary firmware source and is unnecessary
  for an independent build. Saving never runs project code or creates build jobs.

See the [D4 handoff](../device-tree-editor-d4-handoff.md) for implementation and verification.
