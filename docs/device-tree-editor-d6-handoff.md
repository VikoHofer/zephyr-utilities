# Device Tree Editor D6 handoff

Status: local binding/property/reference implementation complete and verified on
2026-11-06 within the limits below.
D5 was committed as `cb03606` before this stage. D6 changes are uncommitted.

## Delivered workflow

Open an existing application overlay and choose **Load binding properties**. The editor
reads local schema metadata for this exact canonical draft revision. Select **Binding
node** and **Binding property** to inspect description, type, required/default/constant
values, enumerations and the current local source. Loading neither adds properties nor
runs project tools. **Cancel inspection** discards a pending metadata request without
changing source.

**Apply binding property** uses the existing source command path and shared Undo/Redo
history. **Reset local override** removes a unique local assignment or deletion directive;
the resulting inherited value remains unknown. **Delete property in overlay** introduces
an explicit deletion directive where supported. Boolean false also uses explicit deletion.
Required properties and defaults never materialize without an explicit Apply action.
The original generic property/source editor remains available for manual values.

Forms cover booleans, unsigned 32-bit integers, strings, numeric/string/byte arrays,
phandles, paths, reference lists and phandle arrays. Scalar enumerations select one value;
array enumerations use ordered entries and preserve repetitions. Provider rows use the
actual local provider's binding cell names and explicit matching cell count, including
zero-cell providers. No fabricated pin map, inherited hardware declaration or external
reference is offered as a known target.

Safe **Add label**, **Set alias**, **Set chosen** and local named-reference removal use
the same canonical commands. Only unique existing overlay declarations are reference
targets. Label rename is withheld because the editor has not proven reference closure
across other source files. Unresolved references, opaque macros, repeated contributors,
unsupported schema fields and incompatible provider layouts stay source-only with an
explanation. Unrelated source bytes are preserved.

All changes remain unsaved drafts. D4 **Review and Save**, D5 **Review Configure** and
Projects Build remain separate actions. No binding inspection or property change
implicitly saves, configures, compiles or flashes firmware.

## Architecture and observation limits

Feature-owned `bindings.py` interprets bounded local YAML as data, using the existing
workspace source reader. It resolves supported includes and allow/block filters, nested
child bindings and bus-qualified compatible matching. Missing, ambiguous and unsupported
bindings are explicit. Unknown property constraints withhold the affected field; unresolved
composition or incomplete catalogue observations cannot silently become a valid match.

`binding_service.py` projects local declarations and schema fields, pins a `schema_id`
to the context and exact canonical draft revision/hash, and revalidates catalogue
fingerprints around command planning. Root existence, directory identity, file contents,
added candidates and changed includes participate. The authenticated
`/api/devicetree/editor/drafts/bindings` endpoint observes metadata; all transformations
still enter `DraftService.command` and the existing `SourceDocument` patch/history and
recovery path. There is no second generated-source store or persistence migration.

The catalogue permits 64 roots, 10,000 files, 2,000 directories, 12,000 encountered entries,
16 MiB total YAML and 256 KiB per file. Directory/YAML nesting is bounded at 32, YAML
events at 20,000, include/child depth at 16, include lists at 64 and dependency files at
128. A schema permits 1,024 properties; candidate and diagnostic lists are capped at 128
and 100 respectively. Aliases, anchors, explicit YAML tags, duplicate keys and symlink
traversal are refused. At most 16 observations are retained for ten minutes. Command
arrays are limited to 64 entries. Existing source/draft/history/Save limits are unchanged.

The browser controller is separate from the view. Metadata loads are explicit; source
typing, Undo/Redo, context/workspace changes and navigation invalidate stale schemas.
Late reads and commands cannot restore another view's metadata or clear its busy state.
An accepted source mutation remains identified even if the subsequent metadata refresh
fails. Disposing the panel aborts reads and listeners; it does not cancel project jobs.

This stage does not implement inherited effective-tree resolution, macro evaluation,
whole-checkout semantic validation, peripheral topology, module placement, pin assignment
or label rename UI. The selected checkout's schema presence is not proof of hardware
behavior. A separate explicit Configure remains the available tool-based feedback path.

## Verification

| Area | Result |
| --- | --- |
| Frontend binding controller and value boundaries | Passed: 26 focused tests |
| Existing draft, Save and validation frontend regressions | Passed: all three focused test files |
| New/changed frontend JavaScript syntax and patch whitespace | Passed before final documentation update |
| Binding resolver/source/reference/service tests | Passed: 19 catalogue, 30 source, 19 reference and 23 service tests; authenticated route checks included in full suite |
| Quick/full repository checks | Passed: quick 822 Python tests (20 loopback tests excluded); full 842 Python and 362 frontend tests, lint, syntax and architecture |
| Installed package assets | Passed `python scripts/verify_package.py`, including new binding browser modules |
| Browser desktop/narrow/scrolled/error/leave-reopen checks | Passed: explicit load, informational defaults, scalar/provider/array-enum edits, alias insertion, reset/undo, 600-pixel validation feedback, retained draft after leave/reopen, stale-binding rejection and nearby reconciliation; sticky desktop source stays visible beside forms |
| Real selected-environment Configure evidence | Passed: nine D6 commands, reviewed Save and explicit Configure for `frdm_mcxn947/mcxn947/cpu0` with Zephyr 4.4.2 and SDK 1.0.1; no compilation or flash |

Frontend cases cover explicit loading without default insertion, guarded canonical
commands, source/context/workspace invalidation, rejected/uncertain commands, metadata
response identity, inspection cancellation, disposal/navigation races, failed refresh
after an accepted mutation, integer/byte bounds, literal strings, ordered array enums,
reference entry boundaries and provider cell counts. Backend regressions also cover
changed includes/new candidates, ancestor overrides, node/property/value label collisions,
unsupported existing reference layouts and malformed inputs.

The real Configure check resolved `gpio-leds` child properties and `zephyr,gpio-emul`
provider cells, then exercised strings, integer/array/string-array/byte values, alias and
chosen commands. Generated DTS contained the expected values; defaults stayed absent,
draft commands left disk unchanged, Configure preserved application inputs, and no
`zephyr.elf` was produced. The disposable application/build were removed. Local evidence
is retained under `/tmp/zephyr-d6-real-9b207cae/`, including `verification.json`,
`configure-result.json`, the candidate overlay and generated DTS. Initial schema lookup
took 4.8 seconds across the selected checkout; subsequent validated cached loads took
about 0.4 seconds. These measurements do not establish a general performance bound.

## Next stage

Implement **D7 only** from the [master plan](device-tree-editor-masterplan.md): common
peripheral and bus-device forms and an accessible topology list. Reuse canonical source
commands, schema observations, reviewed Save and separate explicit Configure/Build.
Modules, pin routing and later multi-file/image work remain outside that next slice.
