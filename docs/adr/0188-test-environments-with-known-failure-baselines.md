---
status: superseded
date: 2026-11-06
area: testing
decided-by: agent
superseded-by: ADR-0215
---

# ADR-0188: Tests run in fixed environments against lists of known failures

## Context

Superseded by ADR-0215, which also keeps the compose test stack's rules: E2E specs run only against local targets, and what the stand-in cannot provide is an explicit skip with a reason, not a known failure. One backend test run per machine at a time is ADR-0210's rule, and `make setup` (ADR-0217) builds the environments.

Several agents ran the tests at the same time on one workstation. The backend suite writes fixed paths under `/tmp`, macOS lacks some Linux features, and the local stand-in of the E2E specs has no lsblk, btrfs, Docker, Samba or host network. Without a reference, nobody could tell a new failure from an old one.

## Decision

- Backend tests ran on the workstation with a pinned Python 3.11 environment, `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`, only through a serialized wrapper script outside the repository (a 30-minute lock wait, a 20-minute run limit, at most 3 full-suite runs per agent).
- The known macOS baseline was 4 failures of `TestTemperatures` in `supervisor/tests/test_system.py` (macOS has no `psutil.sensors_temperatures`); any other failure was new.
- The E2E specs on the local stand-in had a known list of failures (18 in Phase 0, 17 in slice 1, 16 in slice 2), and only other failures counted as new.
- The deviceless compose test stack runs over HTTPS (`localhost:3443`) in its own project, `unos-test`, with ports on 127.0.0.1 only, no fixed JWT secret and a Playwright runner pinned to 1.57.0 (`deploy/docker-compose.test.yml`).
- Frontend worktrees used a symlinked `node_modules`, never copied or installed.

## Consequences

- Runs were reproducible and did not collide, and a regression was visible against the baseline.
- A list of known failures had to be kept by hand, and a real failure could hide among the known ones.
