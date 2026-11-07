---
status: accepted
date: 2026-11-05
area: process
decided-by: agent
supersedes: [ADR-0003]
---

# ADR-0016: Code and tests run on the workstation; the device gets only scripted deploys and checks

## Context

Developing on the device (ADR-0003) put every edit, build and E2E run on its system disk, an SD card that cannot be replaced (ADR-0214), let the E2E tests change the device, and left no way for several agents to work at once. For Phase 0 the lead moved the work to the owner's Mac.

## Decision

- The repository is edited and tested on the workstation (the owner's Mac): the backend tests with the pinned tools of `supervisor/requirements-dev.txt`, the web UI's check and build, and the E2E tests against the local stand-in (`scripts/standin/e2e.sh`, ADR-0215); `make check` is the gate (ADR-0216).
- Agents do not run ssh or scp to the device themselves; they reach it only through the scripts in `scripts/dev/` (`ssh.sh`, `deploy.sh`, `check.sh`, `api.py`, `sd.sh`, `linux-tests.sh`). `deploy.sh` and `linux-tests.sh` take the device lock (ADR-0210); `deploy.sh`, `linux-tests.sh`, `check.sh` and `sd.sh` report the writes to its system disk (ADR-0214); `ssh.sh` and `api.py` do neither, so a command run through them is measured with `sd.sh measure`.
- The device gets read-only checks, the Linux-only backend tests, which `scripts/dev/linux-tests.sh` runs entirely in the device's RAM (ADR-0187), and, when the owner asks for them, scripted and measured deploys (ADR-0223) and checks that change state and undo what they create. Any other change on the device needs the owner's permission, as for ADR-0219 and ADR-0220; container runs, image pulls, prunes, package installs and reboots there are owner actions (ADR-0211).
- A host-level change (GLOSSARY.md) is deployed and checked on the device before its feature passes (ADR-0018). Phase 0 and slice 2 were deployed and checked as uncommitted changes; their commits come after the checks.

## Consequences

- The edit-and-test loop never touches the device or its SD card, and agents can work in parallel in separate worktrees (ADR-0210).
- Tests that need Linux (`/proc`, O_TMPFILE, O_PATH, POSIX ACLs) skip on macOS and run in CI and in `scripts/dev/linux-tests.sh`. The testparm test runs only where Samba's testparm is installed, which neither CI nor the Linux runner has: from the workstation with the absolute path of `scripts/dev/bin` first on `PATH`, so that the device's testparm answers, under `scripts/dev/sd.sh measure` (ADR-0187, `docs/TESTING.md`).
- While a slice is deployed but not yet committed, the device's checkout carries uncommitted changes.

## Considered options

- Developing directly on the device over SSH, as the workspace CLAUDE.md said (ADR-0003): replaced by this decision.
