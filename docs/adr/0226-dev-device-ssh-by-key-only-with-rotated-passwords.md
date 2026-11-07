---
status: accepted
date: 2026-11-07
area: device
decided-by: owner
---

# ADR-0226: The dev device takes SSH by key only, and the passwords once published are rotated

## Context

The old `CLAUDE.md` published the dev device's root password and the UNOS administrator's password; both are still in the repository's history (ADR-0024). Key-only SSH and rotating both passwords were owner actions (ADR-0024, ADR-0212). On 2026-11-07 the owner handed these actions to the agent ("for the actions you asked me to do, I also grant permission, do them yourself").

## Decision

- A dedicated key of the workstation (`~/.ssh/unos_dev_ed25519`, named by `UNOS_DEV_SSH_KEY` in `dev.env`) is root's only entry in the device's `/root/.ssh/authorized_keys`.
- `/etc/ssh/sshd_config.d/10-unos-keys-only.conf` turns password and keyboard-interactive logins off and allows root by key only (`PermitRootLogin prohibit-password`). It was checked with `sshd -t` and a reload, with a key login before and after, and a password login is refused.
- Root's password is a new random one, kept only in `dev.env` (`UNOS_DEV_ROOT_PASSWORD`) for the console. The UNOS administrator's password is a new random one, set through the API and kept only in `dev.env`. `UNOS_DEV_SSH_PASSWORD` is empty.
- The once-published passwords stay in the repository's history: they no longer open anything, and published history is not rewritten (ADR-0208). The repository is private (an anonymous request for it answers 404).

## Consequences

- `scripts/dev/ssh.sh` uses the key in batch mode; a lost key is replaced from the device's console with root's password from `dev.env`.
- The regular UNOS account `test` on the device keeps its password; it is not one of the published ones.
- The feature-list entry `device.key-only-ssh` passes.
