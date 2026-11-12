---
status: accepted
date: 2026-11-12
area: webui
decided-by: agent
supersedes: [ADR-0197]
---

# ADR-0361: Open dialogs form a stack whose top one alone answers Escape, and the focus moves into a dialog and back to its opener

## Context

Under ADR-0197 one Escape closed the first open dialog in the page's markup, and the Users page hid its edit dialog while the Turn off SMB confirmation was shown, with the confirmation declared first, so that the Escape that closed it left the edit dialog, shown again, open. A page that shows a dialog over another one needs a stack of open dialogs instead (ADR-0175): in markup order, Escape closed the dialog under the confirmation. Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent gave Modal that stack and made the Users page show the confirmation over the edit dialog.

## Decision

- `webui/src/lib/components/ui/Modal.svelte` keeps the open dialogs as a stack in its module script, by title id in the order they opened. Only the top dialog answers Escape, whatever the markup order: it marks the key as used (`preventDefault`) and closes when it is closable; a busy top dialog (not closable) keeps the key, so that neither a dialog under it nor the narrow layout's menu acts on it. A dialog leaves an Escape that is already used alone: the dialog under a confirmation that this Escape closed is on top by the time its listener runs. The overlay of a dialog is drawn over those of the dialogs open under it (z-index 50, one more for each).
- The keyboard focus moves into a dialog that opens, onto its `role="dialog"` element (`tabindex="-1"`), unless an element of it already has it (an `Input` with `selectOnMount`); a page that focuses one of its buttons after a tick (the Delete dialog of Files) still does. When the dialog closes, the focus goes back to the element that had it when the dialog opened, if the focus was in the dialog or lost and that element is still on the page; a page that focuses something else after the close (the Network tab's countdown) still does.
- The Users page (`webui/src/routes/users/+page.svelte`) shows the Turn off SMB confirmation over the edit dialog, which stays open under it.
- Kept from ADR-0197: every Modal has its own title id (`modal-title-<n>`, used by `aria-labelledby`); a dialog taller than the window scrolls with its overlay, so that its buttons stay reachable. `Select.svelte` builds its list anew each time it opens (`in:fly`), and Escape on an open list closes only the list (the key stops at the list's button); with the list closed, Escape belongs to the dialog. `Input.svelte` fields look disabled when they are (`disabled:cursor-not-allowed disabled:opacity-50`). Hints that were only `title` attributes are also screen-reader text: the SMB badge adds " (<hint>)", and an access entry that is not active yet adds " (not active yet: <reason>)" (`webui/src/lib/components/shares/`).
- Tests: `webui/tests/users.spec.ts` ('Escape closes only the Turn off SMB confirmation over the edit dialog, then the edit dialog', with the focus in the confirmation, then back on the checkbox, then on the Edit button; 'every dialog is named by its own title, with a title id of its own', which reads the confirmation's title id while the edit dialog is open under it and fails when the dialogs share one) and `webui/tests/shares.spec.ts` (Escape in an open level list, a list reopened at once, the disabled field style).

## Consequences

- The markup order of the dialogs no longer decides which one Escape closes or which one is drawn on top; a dialog asked from a dialog can be shown over it.
- Opening a dialog takes the focus from the element that opened it, so a key such as Space no longer acts under the dialog; Tab can still leave a dialog, since Modal does not trap the focus.
- ContainerDetailDrawer and the restore dialog of the Backup tab are not Modals and stay out of the stack; neither opens over a Modal.
