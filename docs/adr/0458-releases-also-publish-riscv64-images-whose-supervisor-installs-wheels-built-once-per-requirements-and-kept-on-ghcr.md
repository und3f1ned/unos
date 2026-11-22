---
status: accepted
date: 2026-11-22
area: deploy
decided-by: agent
supersedes: [ADR-0455, ADR-0456]
---

# ADR-0458: Releases also publish linux/riscv64 images, whose supervisor installs wheels built once per requirements.txt and kept on GHCR, and ARMv7 keeps building on the host

## Context

ADR-0455 published release images for amd64 and arm64 only. A riscv64 host built the supervisor
itself, compiling cryptography, bcrypt, cffi, psutil, uvloop and PyYAML, which publish no riscv64
wheel, for about 35 minutes on a SpacemiT K1 at every update, and took the web UI's image from another
machine (ADR-0456; Node.js crashes on the K1, ADR-0373). The ledger entry
`deploy.riscv64-release-images` asks for riscv64 images in each release, built under QEMU or on a
native riscv64 runner, with the compiled wheels built once per change of `supervisor/requirements.txt`
and reused, and for riscv64 hosts that build nothing. GitHub has no hosted riscv64 runner, a hosted job
ends after 6 hours, and a cold compile of those packages under QEMU is estimated at one to three hours
(docs/research/riscv64-support-2026-11-07.md, area 7). The owner delegated the decisions, above all safe.

## Decision

- **The bases are there.** `docker buildx imagetools inspect --raw` of the pinned digests, on
  2026-11-22: `python:3.11-slim@sha256:65bd3d9f...`, `caddy:2-alpine@sha256:003a9780...` and
  `alpine:3.22@sha256:49340b34...` (the web UI's builder, which runs on the building platform) each
  list linux/riscv64, and linux/arm/v7 too. No pin moves.
- **QEMU, not a native runner.** RISE's free runners serve open-source projects and this repository is
  private; a self-hosted runner would be a machine of UNOS's own to keep up and to trust with
  `packages: write`. Both building jobs of `.github/workflows/release.yml` run the step "Set up QEMU":
  `docker run --privileged --rm tonistiigi/binfmt:qemu-v10.2.3@sha256:400a4873... --install
  arm64,riscv64` (QEMU 10.2.3, published 2026-06-08), before the login so that the privileged container
  never sees the token, then the check that the `qemu-aarch64` and `qemu-riscv64` handlers are this
  image's (`interpreter /usr/bin/qemu-<arch>`: binfmt skips a handler registered before) and have the
  fix-binary flag. It replaces Ubuntu's qemu-user-static of ADR-0455 for arm64 as well: one QEMU of a
  known version, where Ubuntu's moves with the runner's image under a compile of hours. The monthly pass
  of `docs/maintenance.md` moves its tag and digest, as the BuildKit image's.
- **The riscv64 wheels are built once per key and kept on GHCR.** The job `wheels` (first, with the tag
  check) computes the key, the sha256 of `supervisor/Dockerfile.riscv64-wheels` and
  `supervisor/requirements.txt`. When `ghcr.io/<owner>/unos-riscv64-wheels:<key>` exists it only reads
  its digest; otherwise it builds that file for linux/riscv64, the supervisor's base (the same pin,
  `TestRiscv64ReleaseImage`) with the riscv64 toolchain of `supervisor/Dockerfile` (gcc, python3-dev,
  cargo, rustc, pkg-config, libssl-dev, libffi-dev, make) and `pip wheel` of the pip and setuptools of
  the supervisor's pip layer and of `requirements.txt`, and pushes the result, a scratch image of
  `/wheels`, without provenance, so that each key is one tagged manifest. Its output is the image by
  digest, which the job `images` takes as it is, after checking
  that it names `unos-riscv64-wheels` of the owner's registry. GHCR is the cache because it keeps what it
  holds: GitHub's Actions cache drops an entry unused for 7 days, sooner than the next release. The
  package is private, as GHCR makes a new one, and only the workflow reads it. No new action.
- **The key** covers what the wheels are built from: the requirements and the recipe with its base,
  whose Python and libraries (OpenSSL, libffi) the compiled wheels link. A new base digest, which
  Dependabot's docker group and the monthly pass move in both Dockerfiles together, builds them again.
- **Within the 6 hours.** Only the job `wheels` compiles, only for a new key, and its `timeout-minutes`
  is 330, under the 360 of a hosted job; the job `images` (180) installs the wheels and never compiles.
  A key that needs longer fails that job visibly, publishes nothing and is the time to split the build
  into a matrix of one job per package, which no measurement asks for yet. QEMU's open report of GCC's
  cc1 crashing on one source under qemu-riscv64 10.1 (qemu-project/qemu#3094) is not known to hit these
  packages; a crash fails the job the same way, and the pin is where another QEMU goes.
- **The riscv64 supervisor installs the wheels without the index.** The job `images` builds amd64 and
  arm64 from `supervisor/Dockerfile` as before, and riscv64 from the output of
  `scripts/riscv64-release-dockerfile.sh supervisor/Dockerfile`: the same file with its one pip layer
  replaced by `RUN --mount=type=bind,from=riscv64-wheels,...` `pip install --no-cache-dir --no-index
  --find-links /wheels` of the same pip and setuptools, then of `-r requirements.txt`; the build context
  `riscv64-wheels=docker-image://<the wheels by digest>` provides them. The script refuses a Dockerfile
  without exactly one pip layer. `supervisor/Dockerfile` itself does not change, so
  `scripts/dev/deploy.sh` builds it on the dev devices, the BPI-F3 included, with the layers they have
  cached.
- **The riscv64 image is scanned before it is pushed.** It takes the versions that `requirements.txt`
  does not pin from the wheel build of its key, which neither the audit's pip-audit nor the weekly
  scan of the native images sees: the job `images` first builds it with `--load` and runs
  `scripts/image-scan.sh` on it, the trivy policy of ADR-0446, and a fixable HIGH or CRITICAL
  vulnerability stops the release before any push.
- **Hashes.** `requirements.txt` pins versions without hashes (ADR-0423 left it as it was), so neither
  pip runs in hash-checking mode: nothing demands `--require-hashes`. The wheel set is fixed by its image
  digest. If `requirements.txt` gets hashes, `pip wheel` checks every download against them, and the
  install of wheels built in the job needs hashes of its own: that change makes them.
- **One index per image.** The supervisor's two builds push by digest alone (`push-by-digest=true`,
  `--metadata-file`), and `docker buildx imagetools create` puts both indexes under
  `unos-supervisor:<x.y.z>`; a build that names no digest fails the step before any tag. The web UI is
  one build for linux/amd64, linux/arm64 and linux/riscv64; its builder runs on the runner's platform
  (ADR-0456), so riscv64 costs Caddy's stage.
- **riscv64 hosts are release hosts.** `release_images_here` of `deploy/install.sh` and
  `deploy/update.sh` accepts Docker's `riscv64` beside `amd64` and `arm64`: a riscv64 host, a K1
  included, pulls both images and builds nothing, needs no web UI image from another machine, and the
  in-app modes of update.sh (`--fetch`, `--in-app`) run there. `RELEASE_MACHINES` of
  `supervisor/src/updates/installer.py` adds `riscv64`, so `unavailable_reason` lets System > Updates
  install there. `webui_elsewhere_platform` keeps `armv7l` alone.
- **ARMv7 gets no release images.** On linux/arm/v7, cffi, psutil, PyYAML, uvloop, greenlet and
  httptools publish no wheel for CPython 3.11 (PyPI, 2026-11-22), and greenlet needs a C++ compiler that
  neither the image nor the riscv64 recipe has: a second wheel recipe for a platform that no device of
  the project can check, whose failed build would hold back the release of all three platforms, since a
  release is listed only once all its images are there. That is not little. A web UI image alone for
  linux/arm/v7 would cost only Caddy's stage, but install.sh and update.sh would then need a third way,
  pulling one image and building the other, for a host whose supervisor build is in doubt (below). An
  ARMv7 host keeps building the supervisor and loading the web UI's image (ADR-0456, `docs/INSTALL.md`,
  ARMv7).
- Tests: `TestReleaseWorkflow` and `TestRiscv64ReleaseImage` of `supervisor/tests/test_deploy_scripts.py`
  run the steps of the jobs `wheels` and `images` with a stub docker and the script on
  `supervisor/Dockerfile`; `TestUpdateScript`, `TestUpdateScriptInApp` and `TestInstallScript` run
  update.sh and install.sh on riscv64 (install.sh also with a K1's device tree) and armv7l;
  `test_update_install.py` the reasons.

## Consequences

- ADR-0455 is superseded only where it built amd64 and arm64 alone, took QEMU from Ubuntu's archive and
  left riscv64 without images; ADR-0456 only where riscv64 hosts took the web UI's image from another
  machine. Every other rule of both stays.
- A release run takes up to three hours, and up to about eight and a half when the key is new, which
  a new requirement or base digest makes likely at many releases; until it ends, install.sh and
  update.sh find no images for the tag and stop without a change. The compile counts against the
  private repository's Actions minutes once per key, and only a tag runs it, so Dependabot's weekly
  updates cost one compile per release at most. The
  package `unos-riscv64-wheels` keeps one version per key; old ones may be deleted.
- The riscv64 image takes the versions that `requirements.txt` does not pin (uvloop, PyYAML, certifi
  and the like) from the wheel build of its key, while amd64 and arm64 resolve them at each release:
  they can differ between the platforms until `requirements.txt` changes. Its compiled cryptography
  links the base's OpenSSL, where PyPI's wheels bundle their own, as the builds on the board did.
- A K1 downloads the release's layers instead of compiling: no Rust toolchain and no build layers on
  its system disk (ADR-0214). The BPI-F3 dev device still compiles in `deploy.sh build-supervisor`.
- Checked before the commit with the pinned BuildKit, Docker Desktop's QEMU and a local registry in
  place of GHCR, on small stand-in Dockerfiles: both steps as written (the wheels pushed, then found by
  their key with the same digest), the wheels mounted from the `docker-image://` context and installed
  by the riscv64 image alone, its `--load` and trivy's scan by `scripts/image-scan.sh`, the merged index
  of linux/amd64, linux/arm64 and linux/riscv64 with the labels and `UNOS_RELEASE`. Unverified until the first tag runs: the real wheels' build under QEMU and
  its time, the real riscv64 image, and a pull on the BPI-F3 (the owner's device check, in the ledger
  entry). A riscv64 failure fails the release; "Re-run failed jobs" reuses wheels that were pushed.
- ARMv7 still cannot install or update without another machine, and its host build of the supervisor
  probably fails: cffi's source needs libffi's headers and greenlet's a C++ compiler, which
  `supervisor/Dockerfile` installs only on riscv64 (libffi-dev) or not at all (g++). Not checked; the
  ledger entry names it.

## Considered options

- A native riscv64 runner (RISE, self-hosted): above.
- `actions/cache` for the wheels: an entry unused for 7 days is gone, and a new action to pin.
- BuildKit's registry cache of the supervisor's layers: it would keep the apt layer's Debian packages of
  the riscv64 image as old as the cache, and compile again at every new base digest.
- Stages in `supervisor/Dockerfile` that pick the install by a build argument: BuildKit kept every
  cached layer and the same image ID in a trial, but `deploy.sh build-supervisor` refuses every `FROM`
  without a digest, a stage's included.
- A wheel mount in `supervisor/Dockerfile`'s pip layer: it builds that layer again on both dev devices,
  a 35-minute compile on the BPI-F3.
- Wheels for all three platforms, so that one build makes the supervisor: amd64 and arm64 would freeze
  their unpinned versions too.
