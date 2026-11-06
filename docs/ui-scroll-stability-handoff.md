# UI scroll-stability handoff

Status: implemented and verified on 2026-11-06. Full repository, browser and
installed-package checks passed. D11c was committed as `40b3c0a` before this interaction
work. The UI changes are committed as `6b73fa8`.

## Accepted scope

The user requested that routine actions stop moving the page or taking keyboard focus.
Actions must still show their progress, result and errors where the user is working.
This preference takes precedence over the previous guidance to reveal an offscreen
result automatically. The updated [UI guidelines](ui-guidelines.md) and
[ADR 0014](decisions/0014-visible-action-feedback.md) distinguish feedback from explicit
navigation. This work does not expand the D11 peripheral support matrix.

## Implemented behavior

Routine editor loads, row selections, edits, Undo/Redo, Sync and refresh use inline
feedback and preserve the current reading position. Feedback outside the viewport is
also announced through the fixed shared notice. The inline status/error remains
available after that notice. Ordinary action completion does not reveal a status
paragraph, error or entire editor section.

Application/directory opens report beside their forms and offer **Go to overlay
selection**. Overlay opens report beside their form and offer **Go to overlay draft**.
Success feedback and draft navigation wait for the canonical draft to finish binding.
Opening a different application clears the previous overlay feedback and Go control.
The user chooses when to move to the loaded content. Explicit Show source and Go
controls still navigate. Save, removal and Configure reviews retain their
focused dialogs, Escape/cancellation behavior and return to the trigger.

The editor retains the previous rendered result layout as inert while reading context
and document data. An already presented draft similarly remains visible and inert while
the replacement draft loads. This prevents hiding a long editor from shrinking the page
and clamping the browser's scroll position. The retained presentation is never editing
authority. Context loading remains active until the selected document request finishes;
late responses and finalizers are guarded by request generation, workspace and lifetime.

Action focus restoration uses `preventScroll` and does not override a subsequent user
focus change. The shared reveal helper now defaults to immediate nearest alignment and
suppresses focus-induced scrolling. It is used for explicit destination requests.
Shell route navigation deliberately retains start alignment for the destination heading.
Background refresh and polling do not initiate navigation.

## Review findings addressed

- Routine completion and row-selection handlers revealed distant content, moving the
  page even though the user had not requested navigation. They now retain local feedback
  and use notices when needed.
- Hiding the previous context/draft during a read collapsed the long document and
  clamped the scroll position. The prior layout now remains visible and inert.
- The context session published `loading = false` before its selected document read
  completed. Loading now spans the complete context/document operation.
- Restoring a replaced action control must not cause focus scrolling or override a newer
  action. Restoration retains its current-request and user-focus guards and uses
  `preventScroll`.
- A review dialog could close while its initiating Save/Configure button was still
  disabled. The surrounding action now retains the return target and restores focus
  after completion, only while current and without overriding a subsequent focus change.
- Open success feedback could appear before the canonical draft finished binding.
  The action now awaits that binding before reporting readiness and enabling draft
  navigation.
- Opening another application could leave the previous overlay's success message and
  Go control visible. The downstream feedback and control are now cleared for the new
  application request.
- A global reveal default of start alignment moved already visible destinations farther
  than necessary. The default is nearest; route headings explicitly opt into start.

## Verification

Browser observations below used native pointer coordinates, so the interaction itself
did not rely on an automation helper scrolling a target into view.

| Check | Recorded result |
| --- | --- |
| Desktop application open | Final check: page scroll remained at 0; the initiating button stayed at viewport top 311.125 and retained focus |
| Desktop first overlay open | Page scroll remained at 0; the initiating button stayed at viewport top 665 |
| Narrow directory validation error and successful open | Page scroll remained at 657 for both outcomes |
| Interrupt GPIO row selection on a long page | Page scroll remained at 3608 and focus remained on the selected row |
| Reserved-pin error followed by successful GPIO apply | The apply button stayed at viewport top 710, within less than one pixel of rounding; browser scroll compensation accounted for inserted status content |
| Offscreen action feedback | A fixed shared notice was visible without navigating to the inline result |
| Explicit Show source | The requested source textarea was revealed and focused |
| Keyboard Undo from source | The source text was restored and the textarea stayed at viewport top 467, within less than one pixel of rounding |
| Save review completion after the focus correction | Page scroll remained at 1201 and the Save button stayed exactly at viewport top 381.609375; focus returned to the Save button |
| Leave and reopen editor | Home-to-editor navigation restored the saved source with editing enabled |
| Open a different application | The previous overlay's Go control was cleared |
| Browser console | No warnings or errors observed |
| Context/document session regression checks | Focused frontend file passed after adding delayed success/error, superseded-document and workspace-change cases |
| Quick repository checks | Passed: 1,135 Python tests; [quick-check log](/tmp/zephyr-ui-stability-quick.log) |
| Full repository checks | Passed: 1,159 Python tests and 554 frontend tests; [full-check log](/tmp/zephyr-ui-stability-full.log) |
| Installed-package asset verification | Passed for the installed wheel; [package-check log](/tmp/zephyr-ui-stability-package.log) |

The coordinate checks establish stability for these exercised actions and layouts; they
do not claim that every editor action or every browser was tested. The final
[application-open screenshot](/home/vikofed/.codex/visualizations/2026/11/06/01a1b17f-ec4c-7c72-acc3-00b8803fe932/editor-stable-open-application.jpg)
records the retained inline feedback and explicit navigation control.

## Compatibility and remaining work

No backend route, firmware source format, draft storage format or project-record schema
changes are introduced by this interaction work. Save, Configure and build retain their
existing explicit boundaries. The shared reveal default is a browser contract change;
callers requiring route-style alignment must request it explicitly.

No Configure execution or physical hardware validation was performed for this UI work.
The supported Device Tree families and hardware limits remain those in the
[D11c handoff](device-tree-editor-d11c-handoff.md). The verified UI changes are committed
as `6b73fa8`.
