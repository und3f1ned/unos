---
status: accepted
date: 2026-11-08
area: shares
decided-by: agent
supersedes: [ADR-0094]
---

# ADR-0261: Samba logs failed sign-ins through auth_audit to syslog and keeps its own log file in /run/samba

## Context

ADR-0094 moved Samba's cache to `/run/samba` and its messages to syslog at level 1. smbd and nmbd still kept their own log files, `/var/log/samba/log.smbd` and `log.nmbd`, which get what the daemons write to their standard error, on the system disk wherever `/var/log` is on it (ADR-0194). And nothing logged a failed SMB sign-in, so an administrator could not tell which client keeps failing (ADR-0164, `shares.smb-auth-audit-logging`). Samba logs each authentication in its debug class `auth_audit` (`auth/auth_log.c` of Samba): a failure at level 2, a success at level 3.

## Decision

The rendered `[global]` (`global_settings` in `supervisor/src/shares/smbconf.py`; `deploy/install.sh` writes the same file) has these lines, in this order:

- `cache directory = /run/samba`, as before. Before every testparm, `_ensure_cache_dir` in `supervisor/src/shares/smb.py` runs `mkdir -p -m 0755 /run/samba` on the host (a tmpfs: no disk write); smbd takes a new cache directory only when it restarts, and `deploy/install.sh` restarts smbd and nmbd after it writes the file.
- `log file = /run/samba/log.samba` and `max log size = 100`: one log file in RAM for smbd, nmbd and the smbd of each client, which Samba renames to `log.samba.old` past 100 KiB. On every host, not only where `/var/log` is on the system disk: whether a folder is in RAM cannot be told reliably (zram shows as a block device with ext4), and where `/var/log` is in RAM the file was in RAM already. No `%m` in the name: Samba puts the client's NetBIOS name or address there, and every client that connects would add a file to `/run`.
- `log level = 1 auth_audit:2`: every class at level 1 as before, and `auth_audit` at level 2, so that failed sign-ins are logged and successful ones are not.
- `logging = syslog@2`: Samba sends a message to syslog when its level is at most 2, and since the other classes stay at level 1, only the failures of `auth_audit` are added. No file backend: the journal holds Samba's messages, and System > Logs reads them there (ADR-0257).
- `debug timestamp = no`: without Samba's header line (`[date, level] file:line(function)`) before each message, a message is one journal entry, with the journal's time.

A failed sign-in is then one syslog notice of smbd.service, such as `Auth: [SMB2,(null)] user [WORKGROUP]\[alice] at [...] with [NTLMv2] status [NT_STATUS_WRONG_PASSWORD] workstation [LAPTOP-7] remote host [ipv4:192.168.1.50:50122] mapped to [WORKGROUP]\[alice]. local host [ipv4:192.168.1.10:445]`, which the Samba source of System > Logs lists and its search finds, by `Auth:` or by a client's address.

Tests: `supervisor/tests/test_smbconf.py` (the lines, the log file in RAM, the levels that reach syslog), `supervisor/tests/test_smbconf_testparm.py` (Samba's testparm takes them, on the device), `supervisor/tests/samba_fake.py` (the fake testparm's list of parameters), `supervisor/tests/test_deploy_scripts.py` (the copy in install.sh), `supervisor/tests/test_logs_sources.py` (TestFailedSmbSignIns) and `webui/tests/logs.spec.ts` (failed SMB sign-ins in the Samba source).

## Consequences

- Each failed SMB sign-in adds one journal entry that names the account the client gave and the client's address. Where the journal, or rsyslog's files, are on the system disk, that is a small write, which journald batches, beside the passdb write of the same failure (ADR-0159, ADR-0262).
- Samba's messages have no header line any more, in syslog too; the journal gives each its time and program.
- What smbd and nmbd print before they have read smb.conf still goes to Samba's default files in `/var/log/samba`, and the file in `/run/samba` is gone after a reboot.
- A running smbd and nmbd take the new lines at the reload of the next apply that writes smb.conf (ADR-0090); the cache starts empty after every boot, as before.
