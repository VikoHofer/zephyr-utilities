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
| Load an application or overlay into the current view | Show loading/result/error by the trigger; preserve scroll and focus; offer an explicit control to open its editor or details |
| Select a board, node, source or artifact | Update the detail in place; provide an explicit Show/Open control if the destination is elsewhere |
| Edit, Undo, Redo, Sync, refresh or filter | Show loading/count/result/empty/error in its own section; preserve scroll and typing focus |
| Save/remove a record | Visible confirmation/error and updated local list; preserve scroll and restore focus to a useful surviving control |
| Show source, open details or view a result | Reveal the explicitly requested destination once |
| Expand optional evidence | Expand next to its disclosure control; do not hide the required next action inside it |

Ordinary actions preserve the user's reading position and keyboard focus. Put busy,
success and error feedback by the initiating control. When that feedback cannot remain
visible, use the fixed shared notice as well as the retained inline message. Do not scroll
to a status paragraph, a whole editor section or an input error merely because a request
finished. Offer a clear Show/Open control for a result or next step elsewhere in the page.

Use `context.reveal(element)` for deliberate navigation to a requested destination.
It focuses the destination without implicit focus scrolling, then uses immediate
`block: "nearest"` alignment to minimize movement in the page and nested scroll containers.
Route navigation explicitly uses `block: "start"` for its destination heading. Use
`focus: false` when retaining an input's keyboard focus is necessary. The helper ignores
hidden, closed-dialog and disconnected destinations.
It opens ancestor disclosures for an explicitly requested result.
Content targets made focusable by the helper do not show a control-style outline.
Keep visible keyboard focus indicators on buttons, links and other interactive controls.

Call reveal only for an explicit navigation action, never from a polling render or an
ordinary action's completion handler. After an awaited navigation request,
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
600-pixel widths. Verify that routine actions visibly respond without moving the page,
and that explicit destination controls reveal their target once. Check page scrolling
and nested scrolling regions. Leave/reopen each changed feature, switch workspace during a request, and
confirm background updates preserve focus and reading position. Verify packaged assets
when adding a shared helper or feature browser file.
