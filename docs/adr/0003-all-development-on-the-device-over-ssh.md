---
status: superseded
date: 2026-01-24
area: process
decided-by: owner
superseded-by: ADR-0016
---

# ADR-0003: All development happens on the device over SSH

## Context

Superseded by ADR-0016: code and tests run on the workstation, and the device gets only scripted deploys and checks.

In January 2026 one developer built UNOS directly on the ArmSoM board that runs it. The workspace CLAUDE.md outside the repository (2026-01-24) gave the agents the rule "All development happens on the ArmSoM device via SSH".

## Decision

- Files were written on the device under `/opt/unos` with shell heredocs over SSH (`cat > /opt/unos/<file> << "EOF"`) or copied there with scp.
- Changes were deployed by rebuilding the containers on the device (`docker compose build` and `docker compose up -d` in `/opt/unos/deploy`), then checked with `docker ps`, a hard refresh of the browser and `docker logs unos-supervisor`.
- The Playwright E2E tests ran from a separate folder on the workstation, against the device.

## Consequences

- Every edit and every build wrote to the device's system disk, an SD card that cannot be replaced (ADR-0214); by November 2026 Docker's storage, mostly build cache and images, took 12 GB of it.
- The E2E tests ran against the live device, where they create and delete pools, shares and users (ADR-0215), and they signed in far more often than the login rate limit allows (ADR-0010), so the suite could not pass.
