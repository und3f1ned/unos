---
status: accepted
date: 2026-11-06
area: files
decided-by: agent
---

# ADR-0168: The file manager downloads with the session and never overwrites on copy or move

## Context

In 1.0.0 the file manager could neither download nor delete: Download opened the API address in the browser without the session's Bearer token (ADR-0009), and Delete sent no list of paths. The page also assumed that the pools directory is `/mnt/pools`, showed errors only in browser dialogs or the console, had no Copy or Move, and gave its items no keyboard or screen-reader access.

## Decision

- Downloads go through the authenticated API client: `downloadFile` in `webui/src/lib/api/files.ts` fetches `/files/download` with the token (`downloadAs` in `webui/src/lib/api/client.ts`) and saves the file once all of it has arrived. The page has a Download button for one selected regular file, and a double-click downloads a file.
- Delete sends the list of paths as its JSON body; Copy and Move send the selected items with the destination, the folder that is open when "Copy here" or "Move here" is chosen.
- Copy and Move never overwrite: when the destination already has an item with the same name, the request is refused (HTTP 400 "Already exists: <name>") before anything is copied or moved (`_transfer` in `supervisor/src/files/router.py`).
- Every name is a button that opens a folder or downloads a file, also from the keyboard (Enter or Space); the checkboxes are labelled "Select <name>". The mouse works as before: a click selects a row, a double-click opens it.
- The page starts at the pools directory that the server lists (`UNOS_POOLS_BASE_PATH`), shows errors on the page, and an expired session during an upload or a download ends the session and opens the sign-in page, like every other request.
- Tests: `webui/tests/files.spec.ts` ("File Browser operations"), `supervisor/tests/test_files_security.py`.

## Consequences

- A download is held in the page's memory until it is complete, so the browser shows it only at the end, and a very large file needs that much browser memory.
- To replace an item by copy or move, the user deletes the old one first. An upload, unlike copy and move, replaces a file of the same name (ADR-0169).
- Paths stay confined to the pools and never follow symlinks (ADR-0042); volumes and mounts are protected separately (ADR-0170).

## Considered options

- Opening the download address in a new window, as 1.0.0 did: such a request carries no Bearer token, so it is refused.
