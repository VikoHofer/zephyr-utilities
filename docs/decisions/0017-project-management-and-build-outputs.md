# ADR 0017: Reviewed project management and owned build outputs

## Context

The initial creator writes new applications, captures immutable build selections and
uses fresh output directories. Everyday use also needs existing applications, moved
folders, configuration edits and faster rebuilds without accumulating unmanaged output.

## Decision

Import registers an existing application in the selected workspace after a reviewed
source observation and explicit board/SDK selection. Reconnect updates a registration's
current path and directory identity. Neither operation executes application CMake,
copies sources or creates overlays. Protected tool/dependency locations, symlinks,
duplicate registrations and changes after preview are rejected. Reconnection retains
the application ID, configurations, creation provenance and historical attempt paths.

Imported and updated project/configuration records use schema version 2 with revisions;
unchanged version-1 records remain readable without an opening-time migration. Updates
compare the complete reviewed record before atomic replacement. Editing Original keeps
the creation profile as `initial_profile`. An attempt always retains the profile, source
path and configuration name captured for that operation. Editing a configuration does
not rename or retarget earlier evidence.

Configuration changes have an explicit preview/apply boundary. A user can optionally
add a default overlay for another exact target. The preview includes the new file and
the root CMake changes. Existing generated blocks and sample defaults are retained;
collisions or independently edited activation blocks require resolution before applying.
Guarded rollback removes only this operation's unchanged writes if metadata saving fails.
Concurrent edits are preserved, including a recovery copy when rollback cannot safely
replace the current file. Ordinary profile edits change metadata only.

Fresh Configure/Build remains the default. An explicit reuse operation creates a new
attempt but reconfigures and compiles in a previously managed output directory. Reuse
requires the latest successful attempt, exact project/source/profile and configuration
revision, unchanged output identity, and matching captured CMake/cache evidence. The
selected environment and current application inputs are rechecked before execution.
Earlier attempts sharing that output become historical: their hashes/context remain
unchanged, and their live output is labeled superseded.

Version-1 `outputs/` records store managed directory ownership separately from attempt
history. Removing an attempt does not remove its output registration. Cleanup reviews
one exact managed directory, its bounded inventory and affected attempt identities,
then revalidates it before deletion through directory descriptors without following
links. Source directories, arbitrary paths and replaced outputs are never cleanup targets.
Cleanup uses the shared cancellable job service, retains captured attempt history and
records that the output was cleaned. Cancellation is honored during inventory/revalidation; the final bounded deletion
finishes once started. A filesystem error or concurrent edit can leave partial output;
a new review is required before retrying. Removing an application registration
removes its output metadata but leaves disk contents untouched.

## Consequences

Users can continue work after moving a folder, refresh build choices and iterate without
losing older evidence. Reconnecting does not repair absolute paths in existing CMake
caches: create a fresh output at the new location. Legacy attempts lacking captured
output identity need a fresh build before reuse; recognized legacy output paths can be
explicitly reviewed for cleanup. Import is registration, not arbitrary-project conversion
or compatibility validation. Visual source editing, flashing and multi-image composition
remain separate work.
