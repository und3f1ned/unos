---
status: superseded
date: 2026-11-07
area: ci
decided-by: agent
superseded-by: ADR-0422
---

# ADR-0218: Dependencies are audited weekly, and maintenance passes run monthly

## Context

Superseded by ADR-0422: only in part, the Python audit of `.github/workflows/audit.yml`, now `scripts/audit-python.sh` in place of `pip-audit -r supervisor/requirements.txt`; every other rule here stays in force.

CI audits the dependencies only when it runs for a push or a pull request (ADR-0190), so a new advisory against an unchanged dependency stays unnoticed between changes, and nothing proposes updates. Drift between the code and what agents rely on (the AGENTS.md files, the skills, ADR statuses, the glossary, the feature ledger, the device's state) is not visible to a per-change review either (the book's Scheduled Maintenance pattern).

## Decision

- `.github/workflows/audit.yml` runs every week and on demand (`workflow_dispatch`): `pip-audit -r supervisor/requirements.txt` and, in `webui/`, `npm audit --omit=dev --audit-level=high`.
- `.github/dependabot.yml` proposes updates weekly for pip, npm, GitHub Actions and Docker, grouped, with a cooldown and Conventional Commit prefixes (ADR-0209). Its pull requests are never merged automatically, because the supervisor runs privileged with the Docker socket; the owner reviews and merges them after `make check` passes.
- `docs/maintenance.md` lists the monthly local passes: the AGENTS.md files and the skills against the code and the scripts, ADR statuses, GLOSSARY.md, `feature-list.json` against the tests, the dev device's system-disk writes and wear, the agent setup, starting from the guard's denials in `tmp/guard/denials.log` (ADR-0211), and the device backups in `tmp/dev/backup`, checked by their names and modes only, never their contents (ADR-0223). Every pass ends in a change or a feature-list entry, not in a report.

## Consequences

- Weekly pull requests and audit failures need the owner's time.
- A dependency update reaches main only through review, which delays security fixes by the review time.
- The device passes run locally, because nothing in the cloud can reach the device.

## Considered options

- Merging Dependabot's pull requests automatically, as the book's repository does: too risky for a supervisor that runs privileged with the Docker socket.
