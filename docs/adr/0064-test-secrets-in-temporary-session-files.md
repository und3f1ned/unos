---
status: superseded
date: 2026-11-06
area: security
decided-by: agent
superseded-by: ADR-0212
---

# ADR-0064: Test secrets live in 0600 files in a temporary session folder

## Context

Superseded by ADR-0212: device settings and secrets live in the git-ignored `dev.env`, and passwords reach tools only through SSH_ASKPASS or standard input.

The device verification of slice 2 needed the passwords of a test account, for API calls and for smbclient. A password on a command line is visible to every process of the machine and can end up in shell history and logs.

## Decision

The verification plan generated the test account's passwords on the workstation and kept them in files with mode 0600 in the session's temporary folder: the JSON bodies for curl, sent with `--data @file`, and an smbclient authentication file (`-A`).

## Consequences

- The passwords stayed off command lines.
- The check script that finally ran went further: it kept the secrets only in its own process and wrote the smbclient authentication file to the device's RAM (`/dev/shm`, mode 0600), deleted at the end.
- The planned files lived in a folder that disappears with the session and that the repository's scripts cannot rely on, and every script had to know where they were; ADR-0212 gives them one home in `dev.env`.
