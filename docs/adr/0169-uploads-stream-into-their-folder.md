---
status: accepted
date: 2026-11-06
area: files
decided-by: agent
---

# ADR-0169: Uploads stream into their folder and stay unnamed until complete

## Context

In 1.0.0 every upload over 1 MiB, in the file manager or as a backup, was first written in full to a temporary file on the system disk (on many boards an SD card), then read into memory in full, and only then written to its folder: twice the writes, wear on the card (ADR-0214), and uploads larger than the free space of the system disk or the free memory failed. The whole body also arrived before a bad folder, name or extension was refused.

## Decision

- `receive_upload` in `supervisor/src/request_limits.py` parses the form as it arrives, after the credentials were checked (ADR-0062), and hands the file's data to an upload sink, holding at most 1 MiB of it in memory. The headers of each part are limited to 8 KiB (HTTP 400 above that), and names in a charset that cannot be decoded are read as Latin-1.
- File manager uploads (`_FileUpload` in `supervisor/src/files/router.py`): the folder must exist inside a pool before any data is read, and the name is checked as soon as it arrives (a folder with that name is refused). On Linux the data goes into an unnamed file in the target folder (O_TMPFILE), which is linked into the folder through `/proc/self/fd` only when it is complete: nothing of it is visible before (in the file manager, over SMB), and when the supervisor stops in the middle, the kernel frees it. An existing file of that name is replaced by a rename, and a symlink there is replaced, never followed.
- Where the folder's filesystem cannot create unnamed files (vfat, exfat, NFS, SMB, FUSE mounts made by hand), where `/proc/self/fd` cannot be used, and on macOS, the data goes into a hidden `.unos-upload-<hex>` file created through the folder's descriptor and renamed when complete.
- Backup uploads (`_BackupUpload` in `supervisor/src/backup/router.py`): the extension (`.tar.gz` or `.tar.gz.enc`) is checked as soon as the name arrives, and the data goes straight into a hidden temporary file in the backup folder, which gets its `uploaded_<timestamp>` name only after the check of ADR-0166.
- A refused, failed or cancelled upload leaves nothing behind.
- Tests: `supervisor/tests/test_upload_streaming.py` (no temporary file, the data reaches the destination while the body still arrives, the server's memory does not grow, a killed supervisor leaves nothing in the folder), `supervisor/tests/test_files_security.py`, `supervisor/tests/test_backup_files.py`.

## Consequences

- An upload is written once, where it belongs, and nothing is copied after its last byte. A file manager upload is limited only by the free space of its folder's disk; a backup by its 256 MiB limit (ADR-0166).
- The unnamed-file path needs Linux (O_TMPFILE, `/proc/self/fd`), so those tests run on Linux (ADR-0187); macOS runs the named-file path.
- A stalled client and the leftovers on filesystems without O_TMPFILE remain open (ADR-0167).
