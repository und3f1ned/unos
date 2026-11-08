---
status: accepted
date: 2026-11-08
area: deploy
decided-by: agent
---

# ADR-0304: The Dockerfiles pin each base image by tag and digest, and Dependabot moves the digest

## Context

The Dockerfiles named their base images by tag only (ADR-0015), so a build started from whatever the tag named that day. A tag moves whenever its image is rebuilt for any platform: `python:3.11-slim` moved on 2026-11-05 and again on 2026-11-08, when it named `sha256:07d48240...` instead of the `sha256:65bd3d9f...` in the dev device's build cache. BuildKit reuses a cached base whose image for the device's platform, linux/arm64, did not change, as on 2026-11-08, when only riscv64 had been rebuilt. When the arm64 image changes, the next supervisor build pulls the new base and runs apt-get and pip again: hundreds of MB on the SD card (ADR-0214).

## Decision

- Every `FROM` line of `supervisor/Dockerfile`, `supervisor/Dockerfile.test`, `webui/Dockerfile` and `webui/Dockerfile.test` names its image as `name:tag@sha256:<digest>`, the digest of the tag's multi-platform index. The digest decides what a build starts from; the tag stays for readers and for Dependabot, which reads it from the `FROM` line and proposes the tag's newer digest (`.github/dependabot.yml`, ADR-0218).
- The pins of 2026-11-08 are the digests the dev device holds: `python:3.11-slim@sha256:65bd3d9f1d7fa268dd1909973ba7eb4bdf41f6dcf2cfbe76ba9528b170f3fd79` (Python 3.11.18, in its build cache) and `caddy:2-alpine@sha256:f2b257f20955d6be2229bed86bad24193eeb8c4dc962a4031a6eb42344ffa457` (Caddy 2.10.2, an image there); the first layers of `deploy-supervisor:latest` and `deploy-webui:latest` are theirs. The two images the device never builds take the registry's digests of that day: `node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202` and `mcr.microsoft.com/playwright:v1.57.0-noble@sha256:3bed4b1a12f2338642f3d8cba28e291deef3c66bd4a964bbeb3e57bbff511dbd`.
- `supervisor/Dockerfile.test` starts from the base of `supervisor/Dockerfile`. `TestBaseImages` in `supervisor/tests/test_deploy_scripts.py` checks every `FROM` line that pulls an image (not `scratch` or an earlier stage) and the shared base, not the digests, so that Dependabot's pull requests pass `make check`.
- `scripts/dev/deploy.sh build-supervisor` refuses a base without a whole digest, and says before the build whether the device has the pinned base: as an image, or as layers that `docker buildx du --verbose` lists as pulled from that digest. Otherwise its note says that the build pulls the base and runs apt-get and pip again, unless the digest's arm64 image is one that the cache holds under another digest: BuildKit then reuses it by its layers, and no cached layer names the new digest.
- The Playwright image keeps moving by hand with `@playwright/test` (ADR-0231): tag and digest together, in the monthly pass of `docs/maintenance.md`.

## Consequences

- A build starts from the same base until a commit changes its digest: whatever the tags do upstream, the device's supervisor build reuses its cached base and its apt-get and pip layers.
- Bases age between updates. Newer digests carry the security fixes of Debian, Alpine or Caddy, and the owner merges each; a new arm64 image of Python costs the device's next supervisor build the pull and the reruns (the deploy-device skill: tell the owner first). The pinned Caddy is from January 2026, older than the registry's `caddy:2-alpine` of 2026-11-08.
- Dependabot proposes a newer digest only once the tag has not moved for the 7 days of the cooldown, counted from the tag's last push, and a tag such as `python:3.11-slim` is often pushed again sooner. dependabot-core can also suppress digest-only updates of an unchanged tag (its experiment `docker_digest_only_update_suppression`, 2026-05). So the monthly pass of `docs/maintenance.md` compares each pin with its tag's digest and moves a stale one by hand.
- Without `--platform` in the `FROM` line, Dependabot proposes a new index digest even when only another platform's image changed. Such a pull request costs the device nothing, but build-supervisor's note cannot tell that case from a new arm64 image.
- The ledger entry `deploy.digest-pinned-base-images` changes: its first step said "with the tag in a comment" and now says by tag and digest, and a step for the device's build is added.

## Considered options

- The digest without the tag, the tag in a comment (the entry's first wording): Dependabot reads only the `FROM` line, and for a digest without a tag it proposes the digest of `latest`.
- The registry's newest digests for every image: for Python the same arm64 image under a digest that no cached layer names, so build-supervisor could not confirm the base before the build; for Caddy a newer image, which build-webui refuses until it is pulled onto the device.
