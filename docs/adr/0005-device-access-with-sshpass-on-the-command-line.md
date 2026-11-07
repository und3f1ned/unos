---
status: superseded
date: 2026-01-24
area: security
decided-by: owner
superseded-by: ADR-0212
---

# ADR-0005: Device access uses sshpass with the password on the command line

## Context

Superseded by ADR-0212: device settings and secrets live in the git-ignored `dev.env`, and passwords reach tools only through SSH_ASKPASS or standard input, never on a command line.

From January 2026 the device was reached as root with a password, and both CLAUDE.md files, the workspace one and the repository's, gave a shorthand for it.

## Decision

- Device commands ran as `sshpass -p '<password>' ssh root@<device>`, and files were copied with `sshpass -p '<password>' scp`, with the device's root password written into that shorthand in both CLAUDE.md files.
- The agents' first device wrapper, in November 2026, did the same.

## Consequences

- The root password stood in the repository's docs and so in git history, and command lines show up in process lists, shell history and logs. The conversion of 2026-11-07 removed it from the docs; changing it and moving the device to key-only SSH are owner actions (ADR-0024).
- sshpass sometimes lost the race for its pty, and ssh then sent an empty password: 4 of about 580 logins failed that way on 2026-11-06. The next wrapper passed the password through SSH_ASKPASS, as `scripts/dev/ssh.sh` and `scripts/dev/askpass.sh` do now, with SSH keys tried first.
