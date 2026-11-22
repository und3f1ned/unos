---
status: accepted
date: 2026-11-22
area: deploy
decided-by: agent
---

# ADR-0050: Release with git tags and multi-arch images on GHCR

## Context

UNOS has never had a release: 1.0.0 was declared without a git tag, so the in-app update check always reported the installed version as the latest, and every install builds its own images, which on the dev device writes hundreds of MB to its SD card (ADR-0184). While the repository is private, the README's one-line install and the release check get 404.

## Decision

Proposed for Phase 1, with the updater slice (ADR-0173): releases are git tags, with amd64 and arm64 images built in GitHub Actions and published to GHCR. An update then pulls an image, backs up the database, checks health and rolls back automatically, and no image is built on the device.

## Consequences

- Until then `update.sh` builds on the host (ADR-0049), and the dev device builds only the final stage of the web UI image (ADR-0184).
- The README's one-line install and the release check work only once the repository is public, which is the owner's decision (ADR-0024).
