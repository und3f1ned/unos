---
status: accepted
date: 2026-11-24
area: deploy
decided-by: agent
---

# ADR-0499: The supervisor's image builds for linux/arm/v7 with its compilers in a build stage, and an ARMv7 host keeps building it

## Context

An ARMv7 host builds the supervisor from the release's checkout and loads the web UI's image made on
another machine (ADR-0458, ADR-0456). Six of the supervisor's packages, cffi, psutil, PyYAML, uvloop,
greenlet and httptools, publish no armv7l wheel for CPython 3.11, and `supervisor/Dockerfile` had no
libffi headers and no C++ compiler: built for linux/arm/v7 under QEMU on 2026-11-24 it stopped at cffi
(`ffi.h: No such file or directory`) and greenlet (`No such file or directory: 'g++'`). The ledger
entry `deploy.armv7-supervisor-build` asks for the build, with the build tools out of the images of
the other platforms, or for install.sh to refuse ARMv7 with the reason. The owner delegated the
decisions, above all safe.

## Decision

- **A build stage compiles the wheels.** `supervisor/Dockerfile` gets the stage `arm-wheels`, from the
  pinned base of the image (ADR-0305): `gcc g++ libffi-dev make`, then `pip wheel` of the pip and
  setuptools of the pip layer and of `requirements.txt` into `/wheels`. Its stage `packages-arm`, on
  top of `system` (the base, the apt-get layer and `requirements.txt`), installs them in one RUN
  through `--mount=type=bind,from=arm-wheels`, `pip install --no-index --find-links /wheels`, as the
  release's riscv64 image installs its wheels (ADR-0458). No image starts from `arm-wheels`, so g++,
  libffi-dev, make and the wheel files stay out of every image (gcc and python3-dev of the apt-get
  layer were in every image before and stay), and no runtime package is added.
- **Each architecture picks its packages stage.** The last stage is `FROM packages-${TARGETARCH}`:
  `packages-amd64`, `packages-arm64` and `packages-riscv64` are `packages`, which holds the pip layer
  as it was, and `arm` (BuildKit's TARGETARCH of linux/arm/v7) is `packages-arm`. BuildKit builds only
  the stages the target needs, so amd64, arm64 and riscv64 run the instructions of the one-stage file
  in the same order: the images built before and after the change on 2026-11-24 have the same ID and
  size, linux/amd64 `sha256:4158048a...` of 591,843,220 bytes and linux/arm64 `sha256:116bf3f1...` of
  622,492,482 bytes, every layer from the cache. The dev devices keep their cached layers, and the
  BPI-F3 does not compile again. riscv64 follows the same rule; it was not built here.
  `.github/workflows/release.yml` builds as before, and `scripts/riscv64-release-dockerfile.sh` still
  finds one pip layer, whose pins the stage `arm-wheels` shares (`TestArmv7Supervisor`).
- **The stage references pass the base checks.** ADR-0458 set such stages aside only because
  `scripts/dev/deploy.sh build-supervisor` refused every `FROM` without a digest. It now reads a
  `FROM` of an earlier stage, or of `<name>${TARGETARCH}` where amd64, arm64, riscv64 and arm each
  have a stage, as pulling nothing, names each pinned base once and refuses anything else as before;
  `TestBaseImages` of `supervisor/tests/test_deploy_scripts.py` accepts the same lines.
- **An ARMv7 host keeps building the supervisor; it is not refused.** Built for linux/arm/v7 under
  QEMU on 2026-11-24 without the cache, as update.sh builds, on an Apple M1 Max (10 cores) at load
  averages of 17 to 50, it took 22 minutes (1,291 s): 19 of them the build stage's `pip wheel`, 13 of
  those uvloop's, which compiles libuv. The image is 475 MB (128 MB of it the base, 21 MB the wheels
  it installed), and the build stage keeps about 220 MB more in Docker's build cache: about 0.7 GB in
  all. Started under QEMU with the environment of `deploy/docker-compose.test.yml`, it answered GET
  /api/v1/health with 200 after 25 s, passed the healthcheck of `deploy/docker-compose.yml`, imported
  the compiled modules (cffi, greenlet, uvloop, httptools, psutil, PyYAML, cryptography, bcrypt,
  pydantic-core) and held no g++, libffi-dev or `/wheels`. A typical ARMv7 board (a Cortex-A7 or A53
  at about 1 GHz with 1 GB of RAM and a 32-bit system on an SD card) was not measured; its compile is
  expected to take as long or longer. That costs time at each install and update, not safety: a failed
  build changes nothing, since install.sh stops before `docker compose up` and update.sh keeps the
  running version (`test_a_failed_build_on_armv7_leaves_the_running_version_alone`). The web UI's
  image has no release for linux/arm/v7 (ADR-0458), but the commands of `docs/INSTALL.md` (ARMv7) make
  it on another machine: on 2026-11-24 `docker buildx build --platform linux/arm/v7 webui` made a
  linux/arm/v7 image of 60 MB here in 26 s (npm ci from the cache), its static files built on the
  workstation's own platform, and its Caddy 2.11.9 adapted the Caddyfile under QEMU; the host loads it
  as before. Refusing would end the ARMv7 installs that README.md and `docs/INSTALL.md` offer, for a
  build that passes. `docs/INSTALL.md` (ARMv7) gives the time and the space, 2 GB free in Docker's
  data root with the updates' copies.
- **Docker's architecture names the web UI's platform.** `webui_elsewhere_platform` of
  `deploy/install.sh` and `deploy/update.sh` reads `docker version --format '{{.Server.Arch}}'`, as
  `release_images_here` does, instead of `uname -m`: a 32-bit system on a 64-bit Arm kernel (32-bit
  Raspberry Pi OS on a Raspberry Pi 4, uname aarch64) runs Docker's `arm`, built the supervisor
  already and tried to build the web UI, whose tools have no build for it; it now loads the web UI's
  image as every ARMv7 host does. Only 32-bit ARM takes the web UI's image from elsewhere, as ADR-0458
  kept it; `arm` names the hosts of its `armv7l` and those with a 64-bit kernel.
- Tests: `TestArmv7Supervisor` reads `supervisor/Dockerfile` (the stages of each architecture, the
  instructions of amd64, arm64 and riscv64, the build stage and the one differing layer of arm), runs
  install.sh's start for each machine (only Docker's `arm` builds, armv7l and a 32-bit userland on a
  64-bit kernel, the one architecture whose stage mounts compiled wheels) and reads the ARMv7 section
  of `docs/INSTALL.md`; `TestUpdateScript` runs update.sh for a 32-bit userland on a 64-bit kernel,
  and `test_dev_scripts.py` build-supervisor on a staged Dockerfile.

## Consequences

- update.sh builds with `--no-cache`, so every update of an ARMv7 host compiles the six packages
  again, and Docker's build cache keeps each update's build stage until its garbage collection or a
  `docker builder prune`.
- A new base digest from Dependabot or the monthly pass must move both pinned `FROM` lines of
  `supervisor/Dockerfile`; `test_the_supervisor_images_start_from_the_same_base` fails while they
  differ.
- The ARMv7 image takes the versions that `requirements.txt` does not pin at its build, as before.
- Checked under QEMU only: no device of the project runs ARMv7, and no release builds linux/arm/v7, so
  no image scan (ADR-0446) sees the ARMv7 image, as before.
