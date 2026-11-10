---
status: accepted
date: 2026-11-10
area: security
decided-by: agent
---

# ADR-0328: Each audit entry carries the hash of the one before, and the listing says where the chain breaks

## Context

The audit log is append-only JSON lines with size-based rotation (ADR-0224). Its reader skips a line that is not a valid entry, so an entry edited or removed in the file left no trace: the log showed what the file held (`security.tamper-evident-audit-log`). The agent decided this under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation).

## Decision

- Each entry stores `prev_hash`, the hash of the entry before it in the log, and `hash`, SHA-256 over the fields that its line holds but `hash` (`entry_hash` in `supervisor/src/audit/router.py`). The fields are canonical JSON: keys sorted, no spaces, not ASCII-escaped, as the reader takes them. So the layout of the line does not count, and a field that `AuditEntry` gets later, with a default, leaves older hashes as they are. A clearing that rewrites a file keeps each line's fields.
- `_append_line` chains every entry under the log's lock, which holds across threads and processes (ADR-0286).
  - The previous entry is the last valid entry of the newest file that has one, read from the file's end (16 KiB, the whole file only when no valid entry is there). So the chain goes on across rotated files, and the entries of the supervisor and of the emergency command line make one chain.
  - The line keeps the fields as the caller wrote them, with the two of the chain added.
- `chained` takes the entries of the log, oldest first, and gives each its place in the chain:
  - `ok` while its hash is the hash of its fields and its `prev_hash` is the hash of the entry before it.
  - `broken` where either does not hold: an entry edited in the file, the entry after one that was removed, put in or damaged, and the entry after one whose hash was made anew. So is an entry without a hash after the first entry with one, taken out of the chain or put in without one.
  - None for an entry without a hash before the first entry with one: written before the chain.

  The oldest entry is where the chain starts: its link is not checked, since the entries before it were rotated out or cleared.
- The newest entries have no entry after them that could break. So the supervisor keeps, in memory and by log file, the entry it wrote or read at the log's end last: its id and hash. When a read or an append finds that entry gone or changed, an entry says so before anything else is appended. That entry has the action `other`, the resource type `audit_log`, the severity `critical`, the user `system`, and "The end of the audit log was changed in its file: the entry <id>, which the supervisor had written or read last, and the entries after it are gone or changed". This covers a removed or rewritten end, a removed current file and a chain written anew from an entry on.
  - Its own clearing, and the entries that another process chains after it, move that entry as they should.
  - A process that starts takes the log's end as it finds it.
- `GET /api/v1/audit/logs` gives each entry its `chain` (`AuditEntryView`), derived for the answer and never stored; the JSON export holds `prev_hash`, `hash` and `chain`. `GET /api/v1/audit/logs/count` does not compute the chain. The CSV export keeps its columns.
- The Audit page marks an entry whose chain is `broken` with "Chain broken", and says that the log file was changed there.
- Tests:
  - `TestHashChain` of `supervisor/tests/test_audit_log.py`: the stored hashes, an edited entry, an edit with a new hash, a removed entry, the chain across the rotated files, a clearing, an entry from before the chain.
  - `TestTheEndOfTheChain`: a removed, unreadable or rewritten last entry, a removed current file, an append after a cut end, another process's entry and a clearing that are no cut, an entry without hashes after the chain, a line without some fields, a cut line and a full clearing.
  - One chain from eight threads at once.
  - Four tests that size their files by entries now leave room for the two hashes.
  - "an entry where the chain breaks is marked, with why" of `webui/tests/audit.spec.ts`, on a route fake.

## Consequences

- An entry is about 150 bytes longer: about 420 bytes for a sign-in, where ADR-0224 measured 270. The 1 MiB files of ADR-0224 take fewer entries each.
- An append reads the last 16 KiB of the log before it writes. When the end is not the entry kept in memory, as after another process's entry, it reads the whole log. The listing hashes every entry of the log once.
- The SD card gets no more writes than the longer lines, and the entry that reports a changed end when there is one (ADR-0214).
- The hashes need no key: a key would sit in the same data volume as the log. Some changes are not shown:
  - Removing the oldest entries: the oldest entry kept is where the chain starts, as after a rotation or a clearing.
  - A change made while the supervisor was stopped that writes the chain anew: a supervisor that starts trusts the end that it finds. Whoever can restart it can make that happen.
- A line that a power cut left cut at the end of the log is no break: the next entry links to the last valid one.
- An entry takes its time before it takes the log's lock, so two entries can stand in the file out of the order of their times. A clearing of the entries before a date can then leave a break where it cut, as can a clock set back.
- An action that `AuditAction` no longer has makes the reader skip its entries, which breaks the chain at the next entry.
- The entries written before this change, as on the dev device, have no place in the chain; it starts with the first entry after them.

## Considered options

- An HMAC with a key of the supervisor: the key would be in `/data` beside the log, where whoever edits the log can read it.
- Keeping the newest hash in the database or a file of its own: a write to the system disk for every entry (ADR-0214), which whoever edits the log can make agree.
