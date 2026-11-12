---
status: superseded
date: 2026-11-07
area: webui
decided-by: agent
superseded-by: ADR-0361
---

# ADR-0197: Dialogs, lists and hints in the web UI work with keyboard and screen readers

## Context

Superseded by ADR-0361: open dialogs form a stack whose top one alone answers Escape, and the focus moves into a dialog and back to its opener.

The shared UI components (ADR-0012) had accessibility gaps that the SMB pages exposed. Every dialog had the same title id, one Escape closed two dialogs when one was shown again over another, a long error pushed a dialog's buttons out of a 720 px window, a level list opened again right after a choice showed the previous rows, and hints that existed only as `title` attributes were out of reach of keyboard, touch and screen-reader users.

## Decision

- `Modal.svelte` (`webui/src/lib/components/ui/`): each instance has its own title id (`modal-title-<n>`, used by `aria-labelledby`). Escape closes only an open, closable dialog, and one Escape closes one dialog: the dialog that closes marks the key as used (`preventDefault`), and a dialog leaves an Escape that is already used alone. A dialog taller than the window scrolls with its overlay, so that its buttons stay reachable.
- The Users page declares the "Turn off SMB" confirmation before the edit dialog on purpose (`webui/src/routes/users/+page.svelte`): the Escape that closes the confirmation then reaches the edit dialog, shown again, afterwards, and the edit dialog must stay open. The E2E test of that flow fails when Modal acts on an Escape that is already used.
- `Select.svelte`: the list is built anew each time it opens (`in:fly`), and Escape on an open list closes only the list; with the list closed, Escape belongs to the dialog.
- `Input.svelte`: disabled fields look disabled (`disabled:cursor-not-allowed disabled:opacity-50`).
- Hints that were only `title` attributes are also screen-reader text: the SMB badge adds " (<hint>)", and an access entry that is not active yet adds " (not active yet: <reason>)" (`webui/src/lib/components/shares/`).
- Tests: `webui/tests/users.spec.ts` (Escape in the confirmation, a title id of its own for every dialog) and `webui/tests/shares.spec.ts` (Escape in an open level list, a list reopened at once, the disabled field style).

## Consequences

- Which dialog an Escape closes depends on the order in which the dialogs are declared; a page that ever stacks two dialogs needs a stack of open dialogs (ADR-0175).
- The fixes live in the shared components, so every page that uses them gets them.
