# ADR 0009: Project navigation and advanced Build Inspector

Status: accepted

## Context

The user clarified that Project Creator should guide ordinary application work through
sidebar subnavigation. Read-only build/source details are advanced inspection, while
Device Tree Editor is intended to become a visual tool for defining overlays, adding
hardware modules and assigning pins. Further inspection expansion is not the current
priority; finish the Project Creator workflow before designing that visual editor.

## Decision

Project Creator opens Applications, with Create application and Environment as separate
sidebar children. Creation forms and application management have distinct views. Existing
reviewed writes, builds, jobs, saved profiles and missing-application checks retain their
semantics. Advanced source links remain available through optional disclosures.

Move read-only build opening, resolved Device Tree, source and binding navigation into
the explicitly registered `inspector` feature. Its Build Inspector entry lives under an
optional Advanced section, with Build outputs and Sources and Device Tree subviews.
The `devicetree` feature returns to planned status with no routes or frontend, describing
the future visual editor. This avoids calling the current inspection reader an editor.

Add optional validated sidebar entries and a primary/advanced group to feature
descriptors. The shell owns active-route matching, disclosure visibility, compatibility
redirects and keyboard-accessible links. Feature modules retain their existing mount,
navigate and dispose boundary; no feature imports another feature's implementation.

Inspector's canonical HTTP routes use `/api/inspector/`. Preserve existing Projects
build-inspection and Device Tree read APIs as aliases to the same services. Preserve
`projects/builds/...`, `devicetree/inspect/...` and `devicetree/application/...` deep links
through shell redirects. Bare `devicetree` now opens the planned visual-editor scope.
Source IDs, observation identity, workspace confinement and Memory handoffs are unchanged.
No saved schemas or files require migration.

## Consequences

- Application management and creation have direct sidebar destinations.
- Advanced inspection stays available without occupying the visual editor's identity.
- Old deep links and API clients continue to work; private Python implementation imports
  move to Inspector and are not a promised compatibility interface.
- Further per-property attribution and inspection expansion are deferred. Visual editor
  interaction and validation details will be designed after the creator workflow.
