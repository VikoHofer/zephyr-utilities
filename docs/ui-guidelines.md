# UI guidelines

The application should make each action's effect apparent where the user is working.
A successful request that silently adds a panel below the viewport is an incomplete
interaction. These rules apply to implemented tools and future features.

## Colors

The palette comes from the Zephyr Project's [official branding page](https://zephyrproject.org/branding/)
and [Branding Assets Quick Guide](https://www.zephyrproject.org/wp-content/uploads/2023/10/Zephyr_BrandGuide_083023.pdf),
pages 3–4. Primary colors are purple `#7929D2`, blue `#0070C5`, and light blue `#7DD2F7`.
Neutral surfaces use white, `#F6F7F8` and `#F0F2F4`; text uses the guide's dark grays.
Accessible dark/soft variants support interactive states and readable text.

Use the shell's `--app-*` variables for brand, text, surfaces, borders, focus, success,
warning and danger. Feature styles inherit those values instead of inventing another
palette. Purple/blue indicate navigation and primary actions. Semantic green may indicate
success or a measured reduction; it is not the application's overall theme. Status and
chart meaning must also have text, shape or numeric labels, never color alone.

## Action and result placement

| Action | Expected response |
| --- | --- |
| Open a feature or subview | Destination heading comes into view; same-view refresh does not reset scroll |
| Preview files or commands | Focused review dialog with summary and primary next action visible; technical evidence expandable |
| Start analysis/build/report | Immediate busy state and visible progress; cancellation remains reachable |
| Finish background work | Update status and offer an explicit way to open the result; preserve what the user is reading |
| Select a board, node, source or artifact | Reveal the requested detail once if it is elsewhere in the layout |
| Refresh or filter a catalogue | Show loading/count/empty/error in its own section; retain typing focus |
| Save/remove a record | Visible confirmation/error and updated local list; restore focus to a useful surviving control |
| Expand optional evidence | Expand next to its disclosure control; do not hide the required next action inside it |

Prefer a nearby result to scrolling. When a result must live elsewhere,
`context.reveal(element, {focus: true, block: "start"})` focuses and reveals it once,
without animation. Use `focus: false` when retaining an input's keyboard focus is
necessary. The helper ignores hidden, closed-dialog and disconnected destinations.
It opens ancestor disclosures for an explicitly requested result.
Content targets made focusable by the helper do not show a control-style outline.
Keep visible keyboard focus indicators on buttons, links and other interactive controls.

Call reveal from an explicit action, not from a polling render. After an awaited request,
check the feature's lifetime, view, request identity and captured workspace first. Late
responses must not pull the user back after they navigate, start another action or switch
workspace. Cancellation and errors follow the same rule.

Use native HTML dialogs for bounded review/confirmation tasks. Label the dialog, place
important context before optional technical details, keep action controls reachable in
short/narrow windows, allow Escape when safe, and return focus when it closes. A review
never starts execution by opening or by becoming focused. Closing or changing context
must not leave an executable stale review visible.

Shared notices stay visible independently of page scroll. Routine notices disappear;
errors remain dismissible and should also be available beside the affected input/result.
Do not rely on a transient notice as the only place to retain a critical error or job log.

## Review before shipping

Check keyboard operation, empty/error states and a long scrolled page at desktop and
600-pixel widths. Verify that the initiating action visibly responds and the next action
is reachable. Leave/reopen each changed feature, switch workspace during a request, and
confirm background updates preserve focus and reading position. Verify packaged assets
when adding a shared helper or feature browser file.
