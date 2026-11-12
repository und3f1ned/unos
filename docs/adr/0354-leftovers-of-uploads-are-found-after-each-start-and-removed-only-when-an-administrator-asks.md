---
status: accepted
date: 2026-11-12
area: files
decided-by: agent
supersedes: [ADR-0167]
---

# ADR-0354: Leftovers of uploads are found after each start and removed only when an administrator asks

## Context

Where a file manager upload cannot use an unnamed file (vfat, exfat, NFS, SMB or FUSE mounted by hand below the pools; macOS), its data goes into a hidden `.unos-upload-<16 hex digits>` file in its folder, which a stop of the supervisor in the middle of the upload leaves behind with what had arrived (ADR-0169); on btrfs only a stop in the instant between the link of a complete unnamed file under such a name and its rename over the file it replaces leaves one. ADR-0167 left them open, since removing them means deleting files in user folders. The entry `files.upload-edge-cases` asks that they be found and removed with the owner's consent, but not where, when or how. Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent chose it.

## Decision

- Where: every folder below the pools directory, the file manager's whole reach, also on the filesystems mounted there (where most leftovers are), each folder once, one component at a time without following symbolic links (ADR-0042). The trash folders (`.unos-trash`, whose items go with them, ADR-0259) and `.snapshots` folders (read-only copies; walking them would read the tree once per snapshot) are not entered. A leftover is a regular file named `.unos-upload-` and 16 lower-case hex digits, as `_upload_temp_name` in `supervisor/src/files/router.py` names them.
- When: once, `UPLOAD_LEFTOVER_AGE` (10 minutes) after each start of the supervisor (`first_upload_leftover_check`, started in the lifespan, ADR-0043), since leftovers come only from a stop; and whenever an administrator asks, `POST /api/v1/files/upload-leftovers/check`. The walk runs in a worker thread and only reads; a request while a check runs waits for that check. What the last check found is kept in memory, never stored.
- Never the file of an upload in progress: `_FileUpload` puts each hidden name it takes into the in-memory set `_uploads_in_progress` before the file is created, and takes it out in `discard()`, once the file has its own name or is removed; a file of such a name is neither listed nor removed. A file changed in the last `UPLOAD_LEFTOVER_AGE` seconds is left alone too: an upload gets data at least every 120 seconds or is cut off and its file removed (ADR-0347), so only a writer that the set cannot know, a supervisor process that has not ended yet, could still be writing it. O_EXCL keeps a new upload from creating a file under the name of one that exists, so a listed leftover never becomes an upload's file.
- How an administrator sees them: `GET /api/v1/files/upload-leftovers` answers what the last check found, each file looked at again (one that is gone or was changed since is left out), with its path, size and last change, when the check ended, whether one runs, and the folders it could not read. The File Browser shows a notice above its list while there are any, with their number, their size in total and a **Remove…** button. The first check after a start logs a warning with their number when it finds any, and the folders it could not read; nothing otherwise (ADR-0214).
- Removal asks first: **Remove…** opens a dialog that lists each file with its path, size and last change and asks to remove them for good, with the keyboard focus on Cancel. `DELETE /api/v1/files/upload-leftovers` (a JSON array of the listed paths) checks every path before it removes any: one that is not of a hidden upload file answers 400, a file that is not a leftover (not a regular file, an upload's in progress, changed in the last 10 minutes) 409, a file that is gone is skipped. The others are unlinked, not moved to the trash: a mount made by hand has no trash, and a part of a file is of no use. Nothing removes them on its own.
- The three routes are administrator-only with the files router (ADR-0030).
- Tests: `supervisor/tests/test_upload_leftovers.py` (with a supervisor killed in the middle of an upload into a folder without unnamed files, whose hidden file the next check finds and the removal removes), the routes in `supervisor/tests/test_authz.py`, the first check in `supervisor/tests/test_lifespan.py`; the notice and the dialog in `webui/tests/files.spec.ts` (on the stand-in, whose macOS has no unnamed files) and `webui/tests/mobile-layout.spec.ts`.

## Consequences

- The last open case of ADR-0167 is decided, so it is superseded: its backup cases by ADR-0345, the stalled upload by ADR-0347, the leftovers here.
- Ten minutes after each start the supervisor reads the folder tree of every pool and of every mount below the pools once: no writes (the pools are mounted noatime), but minutes of reads for a large pool. A mount that hangs (NFS, SMB) holds the check's worker thread, and later checks wait for it.
- A leftover on a disk mounted by hand after that check is listed after the next start, or after a check asked for through the API: the web UI offers no check of its own.
- A leftover in a snapshot stays until the snapshot is deleted; one in the trash goes with its item.
- A file that someone else names like a hidden upload file is listed once it is 10 minutes old, and removed only when an administrator confirms its path.
- A leftover whose path is not UTF-8 is named among the problems instead of listed: JSON cannot carry its path, so it is removed on the host.

## Considered options

- Remove them on their own at the start: they are files in user folders (ADR-0167), and a file of that name may be someone else's.
- Record each upload with a hidden file in the database, so that a start knows where to look: a write to the system disk for each such upload (ADR-0214), and the stop that leaves a leftover may lose that write too.
- Tell uploads in progress by age alone: a clock that NTP moves forward during an upload makes its file look old.
