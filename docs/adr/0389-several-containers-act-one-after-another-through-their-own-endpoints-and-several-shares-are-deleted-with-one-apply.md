---
status: superseded
superseded-by: ADR-0430
date: 2026-11-17
area: webui
decided-by: agent
---

# ADR-0389: Several containers act one after another through their own endpoints, and several shares are deleted with one request and one apply

## Context

Superseded by ADR-0430: only in part, the drawer's actions: the container drawer has none, a container's actions are its row's; every other rule here stays in force.

Feature `webui.bulk-actions` lets an administrator select several containers and start, stop or
restart them, and select several shares and delete them after one question. The owner delegated
the details, "above all that it be safe" (in translation). The existing endpoints already carry
the guards: the containers API refuses to stop, restart or remove UNOS's own containers
(ADR-0350) and checks an app's pool before a start with `prepare_app_start` (ADR-0318, which
superseded ADR-0039), and `DELETE /api/v1/shares/smb/{name}` holds `smb_lock` (ADR-0080),
applies smb.conf (ADR-0089) and is limited to 10 requests a minute.

## Decision

- **Containers: the existing endpoints, one after another.** The Containers page
  (`webui/src/routes/containers/+page.svelte`) calls `POST /api/v1/containers/{id}/start`,
  `/stop` or `/restart` for each selected container, in the order of the list, the next one only
  after the answer for the one before, and shows "Stopping <name> (2 of 5)" meanwhile. Every
  guard, the 409 of an app whose pool is not mounted, Docker's timeouts (ADR-0254) and the audit
  entries (`container_started`, `container_stopped`, ADR-0350) stay those of a single action. Not a
  few at a time: each Docker call holds a worker thread of the executor that bcrypt and host
  commands share (ADR-0254), a stop waits up to the container's stop timeout, and the containers
  of one app then stop and start in a known order. No bulk endpoint and no question: each action
  can be undone with another, as a single Start, Stop or Restart is. There is no bulk Remove: a
  removal stays one container at a time, with its question. Restart selected restarts the
  selected containers that run, as a container's own row offers Restart only for a running one:
  a container that does not run gets no request and is listed "not restarted: it is not
  running". While a run goes on, the bar, the checkboxes, each row's and card's own actions and
  the drawer's actions wait for its end.
- **Which containers can be selected.** Each container the list shows has a checkbox, in the table
  and in the cards, and "Select all shown" selects those the search and the state filter show.
  The list leaves out UNOS's own containers (`is_system_container` of
  `supervisor/src/containers/docker.py`: `unos-supervisor`, `unos-webui` and every name that
  starts with `unos-`), so the selection never offers them, and the server still refuses a stop
  or a restart of them for any client (404). The containers of apps can be selected as they can
  be stopped one by one on this page; their start and restart keep the app's checks.
- **A failure among containers.** A container's failure does not stop the others. When all are
  done, the page lists each selected container: its state after the action ("e2e-web: exited"),
  or "failed:" with the server's reason; the title counts them ("Stopped 4 of 5 containers").
  What was not done stays selected, for another try; the others leave the selection. A 401,
  which ends the session, and leaving the page end the run after the container it is at.
- **Shares: one request, one apply.** `POST /api/v1/shares/smb/bulk-delete` (`delete_shares` of
  `supervisor/src/shares/service.py`) takes
  `{"shares": [{"name", "expected_version", "expected_path"}]}`, 1 to 200 shares, each name once
  (case-insensitive), else 422, and the same rate limit as a single Delete; the Shares page
  offers Delete for at most 200 selected shares and says so beyond that, instead of showing the
  server's 422. Under one hold of `smb_lock` it deletes the row of each share whose version and
  folder are still the ones sent, then stages, checks with testparm, commits and installs
  smb.conf once, so Samba is reloaded once. The reasons: 10 single Deletes a minute would refuse an eleventh share with 429
  halfway; each apply writes smb.conf to the system disk (ADR-0214) and reloads Samba; and one
  apply under one hold of the lock leaves smb.conf with all of them gone or, when testparm
  rejects the file or the database is busy, with none of them gone, never a part.
- **What a deletion of several does.** As a single Delete: the folders and the files in them are
  kept, the open connections to each deleted share are closed (ADR-0103), and a running repair of
  one of them stops (ADR-0363). Each deleted share gets its own `share_deleted` audit entry with
  the details of a single Delete (ADR-0320); a share that is not deleted, and a request that
  deleted nothing, write none. An entry that cannot be written, such as on a full `/data`, keeps
  no other from being written: it is in the supervisor's log (ADR-0251), and the answer keeps
  every share's result with a warning that names the shares whose entries are missing. A single
  Delete answers 500 then (ADR-0251); here the results matter more, as for the changes of
  ADR-0350: a 500 would hide which shares are gone and invite a retry.
- **A failure among shares.** The answer has one result per share sent, in the order sent:
  `deleted`, `not_found` (no share has the name: it was deleted or renamed meanwhile) or `changed`
  (its version or its folder is no longer the one the page loaded: it is not deleted, as a PATCH
  with an old version is refused; the folder counts too because a share made again under the
  name starts at version 1 again). The others are deleted all the same. The Shares page says
  which were deleted, lists each one that was not with its reason in the page's language (a key
  per status, ADR-0287), and keeps those selected. When testparm rejects the new smb.conf or the
  database is busy, nothing is deleted. On any failed request the question stays open with the
  reason, and the page reads the list again: "No share was deleted" only when it still lists
  every share the question named; "The deletion failed, and some of these shares are no longer
  listed" when it does not (an answer that never came, or an error after the deletion); and "The
  deletion failed, and the list could not be read again: which shares are left is not known"
  when the list cannot be read.
- **The one question.** "Delete 3 shares": "Delete these 3 shares? Open connections to them are
  closed.", then each share's name and folder, then "Their folders and the files in them are
  kept.", and the button "Delete 3 shares". It names the shares as the page had them when it
  asked, and the request carries their versions and folders. Only SMB shares can be selected;
  NFS exports are deleted one at a time.
- **When the list changes.** A selection is kept by container id and by share name and folder.
  Whenever a list is loaded again, what it no longer has leaves the selection, so a selection
  never moves to a share made again under the same name in another folder; on the Containers
  page, so does what
  the search or the state filter hides, so that an action never reaches a container the
  page does not show. A container removed elsewhere after it was selected answers 404 "Container
  <id> not found", listed as its failure.
- **On a phone** (ADR-0302) the bar of the selection (Select all, the count and the buttons)
  wraps, a row's checkbox takes the table's first column, the results and the question break long
  names and folders, and the question is a `Modal`, which scrolls when it is taller than the
  screen. Every new text is in English and Russian (`webui/src/lib/i18n/catalog/containers.ts`,
  `shares.ts`).
- Tests: `supervisor/tests/test_share_bulk_delete.py`; `webui/tests/containers.spec.ts` and
  `webui/tests/shares.spec.ts` (Several containers at once; Deleting several shares, on fake data
  and on the stand-in); `webui/tests/mobile-layout.spec.ts` (Containers: an action on several,
  Dialog: delete several shares).

## Consequences

- An action on many containers takes as long as their single actions one after another: up to
  the stop timeout of each container, which the page shows as it goes.
- A share that another session changed after the page loaded it is not deleted until the
  administrator looks at it again. Only a share deleted and made again in between under the same
  name and in the same folder, and not changed since, is deleted: the question named that name
  and folder, and a single Delete by name would delete it too.
- A second way to delete shares means a second path to keep in step with `delete_share`: both
  write their audit details through `_deleted_details`, and the backend tests check the
  entries of a deletion of several word for word.

## Considered options

- Bulk endpoints for containers: one long request would show no progress, and the guards and
  audit entries would have to be repeated or shared with the single routes for no gain.
- Single Deletes of shares from the web UI: the rate limit, one smb.conf write and Samba reload
  per share, and a set that is half deleted when one apply fails.
- Keeping hidden containers selected: an action would reach containers that the page does not
  show at that moment.
