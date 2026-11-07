---
status: accepted
date: 2026-11-07
area: files
decided-by: agent
---

# ADR-0232: The file manager renames one selected item in its row and never replaces an entry

## Context

The rename API (`POST /api/v1/files/rename`) worked, but the File Browser page had no way to start a rename: the name field of its rows was never shown (feature `files.rename`). The API also looked for an entry with the new name first and then called rename(2), which replaces an entry of that name, an empty folder included: one that an SMB user or an app created in between was lost.

## Decision

- The "Selected items" toolbar of `webui/src/routes/files/+page.svelte` has Rename, enabled while exactly one item (a file, a folder or a symbolic link) is selected and no upload, copy, move, delete or other rename is running. It turns the item's name into a field labelled "New name for <name>", which takes the keyboard focus with the whole name selected. The field is the shared `Input` (ADR-0012), which got what a field in a list row needs: `ariaLabel` (a name for screen readers without a visible label), `size="sm"`, `selectOnMount`, `spellcheck`, and the keydown, click and double-click events passed on.
- Enter, or leaving the field, renames the item; Escape keeps the name, and so does a field that is unchanged or empty. Spaces at either end are dropped, as in New Folder. Each rename is sent once: another Enter or a blur while it is on its way sends nothing, and neither does the blur that Chrome sends to a field that goes away (after Escape or a rename). A blur while the page has no focus (`document.hasFocus()` is false: the user switched to another app or tab, or to the address bar) is not leaving the field: it stays open, as typed, and has the focus again on the return.
- A refusal shows the server's message as "Could not rename: <detail>" in the page's error, which screen readers announce (`role="alert"`), and the field stays open for another name; opening another folder closes it.
- After a rename the item stays selected under its new name, and the items chosen for Copy or Move follow it, also those inside a renamed folder. After Enter or Escape the keyboard focus goes on at the item's checkbox, which screen readers read with the item's name. A rename by leaving the field does the same when it leaves the focus on nothing: after Shift+Tab to the item's checkbox, which the new row replaces, and after a click on a place that takes no focus.
- From the moment a rename is sent until the list shows the new name, the toolbar's actions (Download too) and Copy here / Move here and Cancel wait: they would start with the old path, which the server may already have renamed. A click on one of them that leaves the field sends the rename, and does its own action only if the list shows the new name by the time the mouse button is released.
- The server checks the name as before (`_validate_name`), refuses the pools directory and the pool roots (403) and volumes, other subvolumes and mount points (409, ADR-0170), and renames with the C library's exclusive rename (`_rename_exclusive` in `supervisor/src/files/router.py`): renameat2 with RENAME_NOREPLACE on Linux, renameatx_np with RENAME_EXCL on macOS, where the stand-in runs. An existing name is refused in the same step, also one that appeared at the last moment: HTTP 400 "Already exists: <name>". Where the C library has no such function, or the filesystem cannot rename that way (EINVAL from NFS and from FUSE filesystems without it, ENOSYS, ENOTSUP), the name is looked for first, as before.
- Tests: `webui/tests/files.spec.ts` (the rename tests of "File Browser operations"), `supervisor/tests/test_files_security.py` (`test_rename_*`).

## Consequences

- On a filesystem without exclusive renames, mounted below a pool, a rename can still replace an entry that is created in the moment between the look and the rename.
- A move within one filesystem (`_transfer`) still looks for the name first and then calls rename(2), with the same moment.
- Renaming a folder that a share, an NFS export or an app uses is not refused, as moving or deleting it is not: they keep a path that no longer exists.
- Download is disabled while an upload, copy, move, delete or rename is on its way, like the other actions of the toolbar.
- The supervisor calls the C library through ctypes for this one function; its arguments are a directory descriptor, the two names (both checked) and a flag.

## Considered options

- A dialog with the new name: a field in the row keeps the item where it is in the list, as New Folder works on the page itself.
- A hard link to the new name, then the removal of the old one, which also fails on an existing name: folders cannot be linked.
- Telling a window blur by `document.activeElement` still being the field: browsers report the body during a blur within the page, although the focus update steps of the HTML standard still have the field there. `document.hasFocus()` is false during a window blur by the standard and in Chromium.
