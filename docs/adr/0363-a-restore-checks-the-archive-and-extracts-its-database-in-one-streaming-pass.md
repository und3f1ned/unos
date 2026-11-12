---
status: accepted
date: 2026-11-12
area: backup
decided-by: agent
---

# ADR-0363: A restore checks the archive and extracts its database in one streaming pass

## Context

A restore decompressed the whole archive into its folder in `/tmp` (up to `MAX_TAR_BYTES`, 640 MiB), then checked the members of that copy and extracted the files of `_FILE_MEMBERS` beside it. Until the end of the restore, `/tmp`, a tmpfs in RAM (ADR-0171), held the decompressed archive and the files extracted from it, about twice the database. ADR-0177 names halving that peak as independent of its pending choice of the tmpfs limit, and the ledger entry `backup.streaming-restore` asks for it while every archive refused today stays refused. Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent chose how.

## Decision

- A restore reads the backup in one pass over its gzip stream, the pass of the upload check (ADR-0345): `_read_archive` in `supervisor/src/backup/router.py`, with tarfile in stream mode (`_BackupTarFile`, mode `r|`), `_DecompressedStream` and `_scan_members`, with all their limits and refusals. `_inspect_archive` calls it without a folder and writes nothing; a restore passes its temporary folder. An encrypted backup is decrypted in memory as before (`_open_archive`) and read by the same pass.
- Of the members a restore accepts, only `unos.db` is written to `/tmp`, as it comes (`_stream_members`, `_extract` with tarfile's `data` filter). `manifest.json` is read into memory, and `scheduled_tasks.json`, which a restore does not restore (ADR-0319), is only noted for its warning. A member refused after `unos.db` refuses the backup: nothing is changed before the whole archive is read, and the folder is removed. Data of `unos.db` cut short is "Invalid backup: corrupt archive", as before.
- The answers of the restore stay those it gave. Where the stream reaches a member whose data would end past `MAX_TAR_BYTES`, the upload refuses it at once as too large (ADR-0345); a restore reads the rest of the stream first and answers as when it had decompressed the whole archive: "Backup archive is too large when decompressed" when the stream is, otherwise "Invalid backup: corrupt archive", since the data the header claims is not there (`_DataPastTheLimit`).
- The SMB preparation of the extracted copy (`_prepare_smb`) keeps its rollback journal in memory (`PRAGMA journal_mode = MEMORY`), not beside the copy in `/tmp`: the copy is temporary, and a crash loses it anyway.
- `_decompress` and `_open_tar` are gone. The peak of `/tmp` during a restore is the database, at most `MAX_DB_BYTES`; `_largest_tmp_use` of `supervisor/tests/test_deploy_scripts.py` and the comment of `deploy/docker-compose.yml` count it so. The tmpfs limit stays 2 GiB, ADR-0177's choice.
- Tests in `supervisor/tests/test_backup_restore.py`: `test_a_restore_holds_at_most_half_as_much_in_tmp_as_before` (plain and encrypted; it measures the files in `TMPDIR` after every call that returns in the restore's thread, and failed with 8,540,791 bytes against at most 4,257,827, half of the decompressed archive plus the files extracted from it), `test_a_database_cut_short_or_refused_after_it_leaves_nothing_in_tmp`, and the 507 test, whose `/tmp` now fails at `unos.db`.

## Consequences

- A restore needs about the size of the database in `/tmp`, and so in RAM, instead of about twice; `docs/INSTALL.md` says so. The decrypted copy of an encrypted backup is still held in memory while it is read.
- An archive with more than one fault may be refused for another of them than before, since its members are checked before the stream has been read to its end; it is refused all the same.
- A backup refused late, after its database, has cost the extraction of the database into `/tmp`, removed with the folder.
