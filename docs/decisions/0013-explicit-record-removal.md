# ADR 0013: Explicit removal of saved application records

## Context

Application folders can be deleted, moved or recreated outside Utilities. Checking only
whether a directory exists makes older registrations reappear when its path is reused.
Named configurations and build attempts also accumulate without a removal control.

## Decision

Creator reconciles saved directory identities with current folders on each catalogue
read. Missing and replaced registrations remain available in a separate cleanup section;
reading the catalogue never deletes evidence. I/O failures remain diagnostics, not proof
of deletion. Same-path registrations are disambiguated using their saved creation history.

Users can explicitly remove application registrations, named configurations and attempt
history. Application removal includes its dependent configuration/attempt records.
Configuration removal preserves the complete profile already captured in attempts.
Original remains part of the application registration. These operations delete only
feature-owned JSON records; firmware source and build output remain user-owned files.

Removal uses authenticated feature routes, expected workspace and parent ownership,
validated record IDs and the existing schema/symlink guards. Related running/queued jobs
prevent removal. Pending reviews are invalidated so removed selections cannot execute.
Unknown formats are preserved. No record schema migration or source marker is introduced.
If older registrations share a current record's exact directory identity, the user must
remove those unavailable entries before removing the current registration. This handles
inode reuse/duplicate records without reviving an older entry or silently deleting its
history. Directory identity alone cannot prove every external replacement when a
filesystem reuses its identifiers and there is no newer saved creation to compare.

## Consequences

Repeated application creation no longer presents obsolete registrations as current apps.
Saved data remains immutable until an explicit removal; removals have a confirmation
describing their scope and no in-app undo. External source deletion is never inferred
from a request to remove saved metadata. Source relocation/import and disk reclamation
for shared captured objects are separate work.
