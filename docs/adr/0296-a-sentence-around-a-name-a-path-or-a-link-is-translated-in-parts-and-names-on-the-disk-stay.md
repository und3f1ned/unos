---
status: accepted
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0296: A sentence around a name, a path or a link is translated in parts, and names on the disk stay as they are

## Context

ADR-0287 takes every text of the web UI from catalogs, and its second phase translates one area at a time, starting with the Users and Snapshots pages. Some of their sentences hold an element of their own: a user's or a snapshot's name in bold ("Turn off SMB for **alice**? Their open SMB connections are ended."), a path or a name on the disk in monospace (`.snapshots`, `restore_<date>_<time>`), a link to the Storage page. Svelte shows what `$t` returns as text, so a key cannot carry the element, and ADR-0287 does not say how such a sentence is translated, nor whether a name on the disk is.

## Decision

- Such a sentence is translated in parts: each text between its elements is a key of the catalog, in the order of the markup, as `account.smb.signInBefore` and `account.smb.signInAfter` of the Account page already are. Examples: `users.delete.before` and `users.delete.after` around the user's name; `snapshots.restore.before`, `.copy`, `.madeIn` and `.after` around the snapshot's name, the pattern of the copy's name and the path it is made in. The markup keeps the elements and the space between a part and an element; a part that starts with punctuation ("? This action cannot be undone.") follows its element without a space. Each language words its parts so that they read well in that order.
- Names on the disk are the same in every language: the folder `.snapshots` and the prefixes `snap_`, `restore_` and `auto_` of the names that UNOS gives are constants of `webui/src/routes/snapshots/+page.svelte` (the folder's name with `i18n-ignore`). The `<date>` and `<time>` of a pattern of such names are words, translated with it (`snapshots.dateTime`, which the page shows after the prefix).
- `webui/tests/users.spec.ts` and `webui/tests/snapshots.spec.ts` read these sentences whole, in English and in Russian, so that a part that loses its space or its place fails them.

## Consequences

- The other language's sentence follows the order of the English parts. A sentence that cannot be worded well in that order gets other parts, in both halves of its catalog.
- A key of a catalog can start with punctuation, and its meaning is in the parts around it: the catalogs say in a comment which element a group of parts surrounds.
- The values (a user's name, a path) stay out of the catalogs, escaped by Svelte as before.

## Considered options

- `{@html}` with one key per sentence and the values escaped by the page: a natural order in each language, but HTML in the catalogs, and a value that one page forgets to escape, such as a user's name, injects markup.
- A helper or a component that splits a translated text at its `{placeholders}` and shows each value in its element: a natural order in each language, but a new part of `webui/src/lib/i18n/`, which the areas of the second phase do not edit (ADR-0287: an area adds its own catalog and edits no shared file).
- The values as plain placeholders, without their elements: one key per sentence, but the bold names and the monospace paths and names are lost.
