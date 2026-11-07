---
status: accepted
date: 2026-11-07
area: security
decided-by: agent
supersedes: [ADR-0005, ADR-0064]
---

# ADR-0212: Device settings and secrets live in the git-ignored dev.env, never on command lines

## Context

The device's root password and its UNOS administrator login were published in the repository's CLAUDE.md, and the old shorthand put the root password on the command line with sshpass (ADR-0005); slice 2's device plan kept test passwords in files of a temporary session folder (ADR-0064). Command lines show up in `ps`, in shell history and in logs, and sshpass also lost the race for its terminal and sent empty passwords (4 of about 580 logins on 2026-11-06).

## Decision

- The device's access settings and secrets live in `dev.env` at the repository root, which git ignores (`*.env` in `.gitignore`). `dev.env.example` lists the names with empty secrets, and `make setup` copies it when `dev.env` is missing, except in a linked worktree whose main checkout has one (ADR-0217). A linked git worktree without a `dev.env` of its own uses the main checkout's (ADR-0210).
- `scripts/dev/env.sh` reads `dev.env` and refuses it unless it belongs to the current user and has mode 600. Settings whose names contain PASS, TOKEN, SECRET, API_KEY or PRIVATE_KEY are secrets: they are never loaded into variables or the environment; a script reads one from the file into a pipe only when it needs it, and never prints it.
- Passwords reach tools only through `SSH_ASKPASS` (`scripts/dev/askpass.sh`, which answers only the device's own password prompt) or on standard input (the API relay of `scripts/dev/api.py`, ADR-0198), never on a command line. Output that may carry a secret is redacted (`scripts/dev/api.py --redact`), and device checks print only what they check: the device-checks skill tests the connection with `scripts/dev/ssh.sh true`.
- SSH keys are preferred: `scripts/dev/ssh.sh` tries keys first and uses the password only when one is set. Switching the device to key-only SSH and rotating the passwords once published in CLAUDE.md are owner actions (ADR-0024).
- `scripts/dev/ssh.sh` checks the device's host key (`StrictHostKeyChecking=accept-new`, with the user's `known_hosts` or the file named by `UNOS_DEV_KNOWN_HOSTS`): a new host is added once, and a changed key is refused. The earlier wrapper outside the repository had the check off.
- The device's root password and the UNOS administrator password, once published in CLAUDE.md, no longer appear in the repository's files. The E2E specs' test administrator, which used the published administrator password, now has a test password of its own (`E2E_ADMIN` in `webui/tests/fixtures.ts`). Both passwords are still in git history and must be rotated on the device, an owner action (ADR-0024).
- Agents never read or show `dev.env`, also not the main checkout's from a linked worktree: the deny rules of `.claude/settings.json` cover every `dev.env` by its absolute path (`Read(//**/dev.env)`), the guard treats the main checkout of a linked worktree as holding one, and it blocks secrets on command lines (ADR-0211). Tests never depend on `dev.env`; they use a file of their own (`UNOS_DEV_ENV_FILE`).
- The deploy's backups in `tmp/dev/backup` (ADR-0223) hold the device's JWT secret, its password hashes and its database. They are never shown or extracted, except by `scripts/dev/deploy.sh rollback`. The deny rules cover them by absolute path (`Read(//**/tmp/dev/backup/**)`), the main checkout's seen from a linked worktree included, and so does the guard (ADR-0211).
- Tests: `supervisor/tests/test_dev_scripts.py` (the mode check, secrets kept out of the environment and off command lines, no device password in the repository's tooling, git ignoring `dev.env` but not the example), and the secret scan of `make test-fast` (`supervisor/tests/test_repo_tooling.py`, ADR-0216): git lists no env file, no text file of the working tree holds a secret-shaped literal, and none holds a token whose SHA-256 is that of a password once published in CLAUDE.md.
- The product follows the same rule: SMB passwords reach Samba only on standard input, 422 answers never echo the submitted values (ADR-0125), and the JWT secret file has mode 0600 (ADR-0028).

## Consequences

- A new clone needs its own `dev.env`, filled in by the owner, while its worktrees share it; nothing secret travels with the repository.
- A script that needs a secret reads it from `dev.env` itself; an agent never handles the value.
