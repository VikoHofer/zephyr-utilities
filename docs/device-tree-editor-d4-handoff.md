# Device Tree Editor D4 handoff

Status: complete within the existing-overlay scope, verified on 2026-11-05.
D3 was committed as `bc781d1` before D4 work. D4 makes the limited overlay
editor available in the normal application at `/#/devicetree/editor`.

## Delivered workflow

Open an application/configuration and an existing application overlay, edit supported
status/properties or source, then choose **Review and Save**. The review identifies the
destination, complete candidate, bounded diff, observed/predicted/unknown overlay use,
and semantic validation that has not run. An excluded Replace-recipe overlay is labeled
inactive. Save changes the reviewed overlay only; it does not activate a fragment,
change a project recipe, configure, build or flash firmware.

The ordinary built-in feature now registers the editor and its browser assets. Existing
Inspector HTTP and hash aliases remain intact. Hardware modules, effective-tree editing,
peripheral topology and pin assignment remain planned. The development preview alias
continues to use the same application/server.

Saving is limited to **one existing overlay**. Creating an overlay is not part of this
stage. Missing files, replaced/relocated directories, symbolic links, multiply linked
files and unsupported destinations are refused while retained source remains available
for comparison and export. Read-only files must have their permissions changed outside
the editor before saving. Initial publication requires Linux atomic exchange support.

Complete source within the supported structural subset and losslessly retained opaque
source can be saved. Incomplete source and parser resource-limit failures remain in the
draft/recovery/export workflow until repaired. Neither an opaque state nor a successful
Save establishes valid Zephyr syntax, bindings or hardware behavior.

## Review, publication and conflicts

Feature-owned `SavingService` adds authenticated POST routes below
`/api/devicetree/editor/drafts/`:

| Route | Purpose |
| --- | --- |
| `review` | Capture an immutable candidate and publication observation |
| `save` | Publish only the matching reviewed plan and draft guards |
| `compare` | Inspect retained base, draft and current file even when the old context is stale |
| `reload` | Explicitly discard the draft or retain it against a reviewed current baseline |
| `recovery/list`, `recovery/read` | Inspect versioned draft records and publication evidence |
| `recovery/restore` | Restore reviewed source into its matching context as an unsaved draft |
| `recovery/discard` | Explicitly remove one revision-checked recovery record without changing firmware |

Review plans and comparisons expire after ten minutes and retain at most 32 entries of
each kind. Save requires the original context, draft revision/hash and immutable plan
identity. Same-session duplicate requests retrieve the completed result rather than
performing another exchange; 64 completed service results are retained. New source
changes require a new review. Repeated Save preserves unchanged file bytes and identity
and does not consume another publication journal.
Undo after Save retains the shared edit history and produces an unsaved inverse change.

`OverlayPublisher` opens each ancestor without following symbolic links and records the
directory chain, destination inode, source bytes/hash, mode, owner/group and bounded
extended attributes. Participating writers coordinate by opened parent device/inode
and basename; a directory lock additionally coordinates separate application processes.
Projects currently creates new overlay files without replacement and replaces only a
reviewed CMake file, so it is not a second existing-overlay writer. A future overlapping
writer must join the same coordination boundary.

Publication stages exact candidate bytes beside the destination, preserves supported
permissions and attributes, flushes the file and directories, and writes recovery
evidence before Linux atomic exchange. The displaced file and installed candidate are
verified before success. Special permission bits, unsupported ownership/attribute
preservation and unsupported filesystem operations fail closed.

Before exchange, a refusal/failure leaves the destination unchanged. After exchange, an
interference or durability failure is reported as **uncertain**: candidate bytes may
already be visible. The displaced file and exact base/candidate evidence are retained;
there is no automatic rollback or retry that could overwrite a newer external change.
Publication evidence loaded from a previous/interrupted session requires comparison
instead of silently becoming a success acknowledgement. Opening recovery never replays
a firmware write.

These guards do not isolate nonparticipating processes holding writable descriptors or
writing during/after exchange. A later external write can invalidate a previously
verified result. The UI distinguishes known uncertain publication from an ordinary
pre-publication conflict and exposes recovery locations for explicit reconciliation.

## Durable drafts and recovery

Acknowledged source and visual mutations persist version-1 records under
`context.feature_data("devicetree") / "drafts"`. Records contain the original context,
file observation, exact base/source text and hashes, revision and bounded history. New
storage directory entries, record contents and atomic record replacement are flushed.
A persistence failure does not acknowledge a new canonical source revision. If source
publication succeeds but updating its recovery metadata fails, the UI reports that
separate outcome and retains the recovery-failure indication until persistence succeeds.

Unknown versions and malformed records remain untouched. Recovery explicitly compares
current source and selection identity before restoration; changed configurations,
workspaces or replaced application directories cannot silently reuse the old draft.
An existing edited session draft must be explicitly discarded before another recovery
record can replace it. A recovered draft remains unsaved and requires a fresh Save
review. Unsent browser typing is retained across feature navigation but is not durable
across page/server loss; export remains available.

Publication records use a separate version-1 `publications` namespace and retain exact
original/candidate evidence. Same-directory displaced files use an operation-specific
hidden name and are not automatically deleted. Limits are explicit:

- Existing D3 limits remain: 256 KiB per source, 16 session drafts, 8 MiB retained source
  and history bytes, and 32 shared undo/redo entries per draft.
- At most 64 draft recovery records, 16 MiB per encoded record and 32 MiB across the
  draft store. Capacity refusal preserves existing records and pending text.
- At most 64 publication entries, with a 4 MiB encoded record bound. Retained publication
  evidence must be preserved/archived before admitting more after the limit is reached;
  automatic evidence pruning is not implemented.
- Extended attributes are bounded to 64 names and 128 KiB of values. They must be
  preserved and reverified, not silently stripped.
- Diff previews remain bounded; complete candidate/current source remains separately
  reviewable. No source is truncated to fit a preview.

## Verification

| Area | Result |
| --- | --- |
| Guarded publisher | 24 focused tests passed |
| Save/reload/recovery service | 18 Save and 16 recovery tests passed, plus 20 existing draft tests |
| Authenticated routes and feature promotion | 26 focused HTTP/context tests passed during integration |
| Frontend review/recovery/lifecycle | 20 D4, 20 D3 and 14 D1 focused tests passed |
| Quick repository check | Passed: 729 Python tests; 20 loopback tests excluded; all 26 frontend files passed |
| Full repository check | Passed: 749 Python and 311 frontend tests, plus lint, syntax and architecture checks |
| Browser Save/reopen, conflicts, recovery, keyboard and narrow/scrolled layout | Passed with synthetic projects at 1280 px and 600 px widths |
| Installed-package browser assets | `python scripts/verify_package.py` passed, including the new Save module |

Publisher coverage includes exact reopen bytes and permissions, no-op identity, replaced
inodes with identical bytes, symlink/hard-link/nonregular rejection, parent swaps, extended
attribute preservation, participating locks, held-descriptor writes, exchange races,
post-exchange interference, flush/journal failures, restart ambiguity and bounded evidence
retention. Service/frontend checks cover stale reviews, dirty-draft retention, separate
configurations, explicit recovery and shared Undo after Save.

Browser verification used the normal built-in editor. Visual status/property changes
were reviewed and saved; repeat Save reported no replacement; Undo after Save changed
only the draft. An externally edited file blocked Save and explicit discard/reload
adopted the compared source. An incomplete draft was retained, blocked from Save, then
recovered after a server restart without publishing its text. Its retained undo history
repaired the source. A subsequent reviewed Save survived leaving/reopening the feature.
Escape returned focus to the review trigger; the Save action remained reachable at
600 by 780 pixels and from a scrolled page. Source export matched saved bytes exactly,
including CRLF. The final page emitted no console warnings or errors. Temporary servers
and the disposable download were cleaned up. No emojis appear in the new workflow or
verification previews.

No firmware configure, build, flash or hardware validation is part of D4 verification.

## Next stage

Implement **D5 only** from the [master plan](device-tree-editor-masterplan.md): explicit
selected-environment validation and Projects handoff with candidate/input identity.
Keep validation separate from Save, preserve legacy Inspector behavior, and do not
expand into module placement or pin configuration in that stage.
