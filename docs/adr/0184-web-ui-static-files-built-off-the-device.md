---
status: accepted
date: 2026-11-06
area: device
decided-by: agent
---

# ADR-0184: The web UI's static files are built on the workstation, not on the device

## Context

The builder stage of `webui/Dockerfile` pulls `node:22-alpine` and runs `npm ci`: each time `package*.json` or the Node image changes, a build on the device writes several hundred MB to its system disk, which cannot be replaced (ADR-0214), and `npm ci` also leaves about 120 MB of `node_modules` in the build cache. The old instructions built both images on the device with `docker compose build`.

## Decision

- The device never runs the web UI's builder stage. `scripts/dev/deploy.sh build-webui` runs `npm run build` on the workstation, with the `webui/node_modules` that `make setup` installs from the same `webui/package-lock.json`, uploads the static files and the Caddyfile to the device's RAM (`/dev/shm`) and builds there only the final Caddy stage of `webui/Dockerfile`, with the uploaded files in place of the builder stage.
- The step refuses to build when the final stage's base image is not on the device (the build would pull it: an owner action), tags the previous image `:rollback` first and reports the MiB written to the system disk (ADR-0223).
- The supervisor image is still built on the device (`deploy.sh build-supervisor`).
- `webui/Dockerfile` is unchanged, so `deploy/install.sh` and `deploy/update.sh` still build both stages on an end user's machine.

## Consequences

- A web UI deploy writes only the final image's layers to the device.
- The workstation needs the web UI's toolchain, which `make setup` installs (ADR-0217).
- Prebuilt multi-arch images (ADR-0050) or Docker's data root on the hard disk would take image builds off the system disk entirely; both are open.

## Considered options

- Building both stages on the device, as the old instructions did: hundreds of MB of system-disk writes per dependency change.
- A BuildKit tmpfs for `node_modules`: keeps them in RAM, but downloads them again at every build.
- Prebuilt images from a registry, or Docker's data root on the hard disk: possible later; the first needs a release process, the second is a host change.
