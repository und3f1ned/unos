---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0261
---

# ADR-0094: Samba keeps its cache in /run/samba and logs to syslog

## Context

Superseded by ADR-0261: Samba keeps its cache and its own log file in /run/samba, and logs failed sign-ins through auth_audit to syslog.

With Debian's defaults, nmbd rewrites `browse.dat` about every 12 minutes and smbd writes `smbprofile.tdb` at the end of every connection, both in `/var/cache/samba` on the system disk, and `log file = /var/log/samba/log.%m` writes one log file per client. The system disk must be written as little as possible (ADR-0214). testparm exits with an error when the cache directory does not exist, for example after a boot before smbd has run.

## Decision

- The rendered `[global]` (ADR-0087) sets `cache directory = /run/samba` (a tmpfs on the host), `log level = 1` and `logging = syslog@1`, instead of a log file per client and its size limit.
- Before every testparm, `_ensure_cache_dir` in `supervisor/src/shares/smb.py` runs `mkdir -p -m 0755 /run/samba` on the host (a tmpfs: no disk write); a failure is logged and testparm decides.
- smbd keeps an `smbprofile.tdb` that it has already opened until it restarts, so Samba uses the new cache directory only after a restart; `deploy/install.sh` restarts smbd and nmbd after it writes the file.
- Tests: `supervisor/tests/test_smbconf.py`, `supervisor/tests/test_share_apply.py` (the cache directory before testparm), `supervisor/tests/samba_fake.py` emulates the mkdir.

## Consequences

- Samba's periodic cache writes stay off the system disk; the cache starts empty after every boot.
- Samba's daemons still append their own messages to `log.smbd` and `log.nmbd`, whatever `logging` says, and passdb stays on disk (ADR-0159); ADR-0194 proposes how to reduce these writes.
