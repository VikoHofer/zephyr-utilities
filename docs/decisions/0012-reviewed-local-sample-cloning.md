# ADR 0012: Reviewed local sample cloning

Status: accepted

## Context

Users need a useful application starting point from their installed Zephyr version.
Samples can include board overlays, license annotations, sibling dependencies and
build scripts. Copying only `main.c` loses required inputs; recursively copying any
directory cannot establish that it is an independent application.

## Decision

Add an explicit Local Zephyr sample choice to Project Creator. Start with three curated
paths in the selected checkout: Hello World, Blinky and Button. Inspect their current
contents through a bounded, read-only adapter. The adapter accepts a narrow standalone
CMake grammar and supported local text files; it rejects unsupported dependencies,
scripts, links, special files, binary content, build outputs and incomplete scans.
No CMake, Kconfig, Python or imported script runs during catalogue/preview operations.
Requirements are presented separately from build compatibility.

Preserve supported upstream files byte for byte, including metadata, documentation and
board-specific files. Carry root license/attribution evidence and retain local SPDX
and copyright declarations. Add separately named origin and build instructions instead
of rewriting the upstream README or CMake project declaration. All copied and generated
files are reviewable before creation.

Use template identity `{id: "local-sample", version: 1}` and a version-1 `sample`
provenance envelope containing source identity, checkout observation, requirements and
file hashes. The selected catalogue fingerprint must match at preview. Recheck sample
and license inputs independently of the environment fingerprint before writes and
before final publication. Capture source bytes in the retained server-side plan; the
client submits only selection IDs/fingerprint and later the plan ID.

Generalize the existing atomic publisher to bounded nested paths. Pin created directory
identities, verify the complete staged tree, and publish without replacing a destination.
Failure/cancellation removes only unchanged operation-owned files and empty owned
directories. Foreign edits, additions and substituted directory branches are preserved.
The same implementation continues to publish the unchanged Minimal C/C++ templates.

Outer project/profile records retain their existing formats. Optional versioned origin
evidence is additive and does not change build semantics: once published, a sample copy
builds from its own source and captured profile exactly like other applications. Older
records need no migration. Unknown source adapter/provenance versions cannot be applied
as creation plans; reopening a saved project does not rescan its origin.

## Consequences

- A Zephyr upgrade or local sample edit can change availability. Refreshing reports the
  changed files/layout instead of silently copying an unsupported sample.
- Catalogue availability establishes copy support only. Configure/build remain explicit
  actions, and peripheral operation still requires separate hardware validation.
- Sample test metadata remains present but does not automatically select a Twister
  scenario, overlay, board, shield or snippet. The existing configuration workflow
  controls explicit file selections after creation.
- Arbitrary sample discovery/import, more adapters, modules/blobs, scripts and sysbuild
  are later slices. There is no sample synchronization or upstream write-back.
