---
status: superseded
date: 2026-11-13
area: deploy
decided-by: agent
supersedes: [ADR-0371]
superseded-by: ADR-0450
---

# ADR-0373: The web UI's image of a SpacemiT K1 is built on another machine, and install.sh and update.sh build only the supervisor there

## Context

Superseded by ADR-0450: the web UI's builder runs on the platform that builds, and every riscv64 and ARMv7 host, not only a K1, takes its web UI image from another machine, since the web UI's build tools publish no binaries for them.

ADR-0371 ran vite without Node.js's Maglev compiler on riscv64, after Alpine's Node.js 22.23.5
had stopped the web UI's build on a Banana Pi BPI-F3 (SpacemiT K1) with "Fatal error ...
unreachable code" twice. With `--no-maglev` the build passed once and then ended with SIGSEGV
on a pointer shifted by one byte. Node.js has an open report of such crashes on the K1
(nodejs/node#66845), with no fix and no known flag; the same build passes under QEMU's riscv64
emulation on another machine, where the static files are the same.

## Decision

- `webui/Dockerfile` builds with `npm run build` on every platform again.
- On a SpacemiT K1 (`is_spacemit_k1`, as in ADR-0370), `deploy/install.sh` builds only the
  supervisor and starts the web UI from the image `deploy-webui:latest` that is there; without one
  it stops before it builds anything and says how to make it on another machine
  (`docker buildx build --platform linux/riscv64 -t deploy-webui:latest --load webui`) and load it
  (`docker save`, `scp`, `docker load -i`). `deploy/update.sh` builds only the supervisor there,
  says that the web UI's image of the new version is loaded beforehand, and after a rollback
  that it is loaded again, since the rollback gives the tag back to the previous image.
- The dev device's way (ADR-0184), the static files built off the device and only the image's
  final stage on it, makes the same image.
- Tests: `TestInstallScript` and `TestUpdateScript` of `supervisor/tests/test_deploy_scripts.py`.

## Consequences

- A K1 cannot install or update UNOS alone: another machine with Docker builds its web UI image
  for each version. Release images for riscv64 (`deploy.riscv64-release-images`, ADR-0050) end
  that, and the supervisor's compile of about 35 minutes on the board with it.
- An update that runs without the new web UI image starts the new supervisor beside the old web
  UI; `update.sh` warns about it, and nothing checks the versions against each other.
- On a fresh K1, Docker comes with `install.sh`, so its first run sets up the host and stops at
  the build; it runs again once the image is loaded.
- A Node.js release that fixes the crash brings the K1 back to `npm run build` on the board.

## Considered options

- `node --no-maglev` (ADR-0371): it crashed as well.
- `--jitless`: undici needs WebAssembly, which jitless mode has not.
- Retrying the build until it passes: about nine minutes per try, and no bound on the tries.
