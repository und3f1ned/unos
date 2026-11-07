---
status: superseded
date: 2026-01-24
area: process
decided-by: owner
superseded-by: ADR-0208
---

# ADR-0004: Commits are made on the device and pushed straight to main

## Context

Superseded by ADR-0208: commits and pushes happen only when the owner asks, on a branch per unit of work and never to main, and the owner opens the pull requests.

The workspace CLAUDE.md of 2026-01-24 described a single-developer workflow on the device itself (ADR-0003), without branches or review.

## Decision

- The developer committed in the device's checkout (`cd /opt/unos && git add -A && git commit -m "message"`) and pushed directly to main (`git push origin main`).

## Consequences

- Everything in the device's working tree went into main, without a branch, a pull request or a review.
- The device needed write access to the owner's GitHub account: it held a classic GitHub token until 2026-11-05, when the token was removed from the device and its checkout was switched to SSH with a dedicated key (ADR-0053), so the device can no longer push. Adding that key to the repository as a read-only deploy key and revoking the token are the owner's open actions (ADR-0024).
