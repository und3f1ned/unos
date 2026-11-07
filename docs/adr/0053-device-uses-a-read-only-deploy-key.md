---
status: accepted
date: 2026-11-05
area: device
decided-by: agent
---

# ADR-0053: The device has no GitHub token, only a read-only deploy key

## Context

The device's checkout talked to GitHub with a classic personal access token, which gave the device, and anyone who controlled it, write access to the owner's GitHub account.

## Decision

- The token was removed from the device on 2026-11-05. `origin` of `/opt/unos` is now `git@github.com:und3f1ned/unos.git` over SSH, with a dedicated key (`/root/.ssh/unos_deploy_ed25519`, selected in `/root/.ssh/config`), and GitHub's host key was checked against its published fingerprint.
- The owner revokes the classic token and adds the key's public half to the repository as a read-only deploy key (ADR-0024).

## Consequences

- With the deploy key the device can fetch but never push, unlike before (ADR-0004): pushes come from the workstation when the owner asks (ADR-0208), and the guard blocks a git push on the device (ADR-0211).
- Until the owner has added the deploy key, `git fetch` and `deploy/update.sh` cannot reach GitHub from the device; deploys from the workstation (ADR-0223) do not need it.
