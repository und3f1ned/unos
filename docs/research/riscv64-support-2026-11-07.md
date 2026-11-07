# What riscv64 support would cost UNOS, with the SpacemiT K1 in mind

Checked on 2026-11-07 against:
- PyPI's JSON API and the projects' own repositories;
- the Docker Hub tag API, the docker-library definition files, explore.ggcr.dev reads of GHCR image indexes and the LinuxServer.io fleet API;
- the Debian and Ubuntu archives (madison, packages.debian.org, packages.ubuntu.com) and download.docker.com;
- kernel.org, SpacemiT's upstream status page, archive.spacemit.com and Armbian's build configuration;
- board vendors' and reviewers' pages, and the Banana Pi, Milk-V and SpacemiT forums;
- the documentation of GitHub Actions, RISE, QEMU, cibuildwheel, Debian's cloud images and Lima;
- the UNOS tree at commit 9a28743 (`origin/main`).

This is research, not a decision. It estimates what supporting linux/riscv64 hosts would cost. The first target is the owner's board: a SpacemiT K1 SoC marked "RISC-V K1 6370", with a 16 GB Samsung KLMAG1JETD eMMC. Nothing in UNOS changes here. The ledger entries and the ADR at the end are proposals; the lead adds them once the owner has decided (ADR-0001, ADR-0203).

How to read it:
- In each area, **Facts** carry their source. Every source was read on 2026-11-07, and the [Sources](#sources) list repeats the date. "(snippet)" marks a fact taken only from a search-result summary.
- **Work**, **Size** and **Risks** are this report's judgment. Estimates are labelled as such.
- Sizes count focused work by one developer or agent following `AGENTS.md`: S is under a day, M is one to five days, L is more than a week.
- Web pages were read through a fetch tool that summarises them. Lists (architectures, wheel files) were requested verbatim and cross-checked where a second source existed.
- No command used the network from the shell, nothing was installed, and the dev device was not contacted.

## Summary

| # | Area | Verdict | Size | Risk |
| --- | --- | --- | --- | --- |
| 1 | Base images | `python:3.11-slim` (now Debian trixie) and `caddy:2-alpine` publish linux/riscv64. No `node` image does, so the web UI's builder stage must run on the build platform | S | Low |
| 2 | Python dependencies | 9 of the 15 compiled packages have riscv64 wheels, but the image's pip 24.0 ignores them; pip 24.1 or later is needed. cryptography, cffi, bcrypt, uvloop, PyYAML and psutil must be built from source (Rust and C) | M | Medium |
| 3 | Docker Engine and Compose | Docker Inc. publishes no riscv64 Engine packages; only Compose's release assets include a riscv64 binary. The distributions do: Debian 13 has Engine 26.1.5 with Compose 2.26.1; Ubuntu 24.04 has Engine 29.1.3 with Compose 2.40.3 | M | Medium |
| 4 | Host tools | Every package `install.sh` installs, and every program in `HOST_TOOLS`, is built for riscv64 in Debian 13 and Ubuntu 24.04 | S | Low |
| 5 | SpacemiT K1 platform | Of the systems UNOS targets, it runs Debian 13 and Ubuntu 24.04: the K1 is RVA22, and Ubuntu 25.10 and later need RVA23. The mainline drivers a headless NAS needs landed between 6.16 and 7.2. NVMe runs on PCIe Gen2 x2; there is no SATA and no AES acceleration. The board is most likely a Banana Pi BPI-F3 | M (bring-up) | High |
| 6 | App catalog | 6 of the 70 catalog images publish linux/riscv64. The catalog must know each image's platforms and refuse the others before a pull | M | High (product value) |
| 7 | CI and releases | GitHub has no hosted riscv64 runners. buildx with QEMU works but takes hours for a cold build with Rust extensions. RISE offers free native runners for open-source projects | M (after `deploy.release-images`) | Medium |
| 8 | Code assumptions | `install.sh` refuses riscv64 and takes Docker from Docker's repository; the catalog has no platform field. Little else is architecture-specific | S | Low |
| 9 | Testing | A QEMU riscv64 VM with virtio and NVMe disks covers the host flows without the board. The board joins as a second dev device under ADR-0214 | M | Medium |

**Overall estimate (judgment): L, about three to five weeks of focused work** for the seven features proposed below. Two to four of those days need the owner at the board.
- It excludes `deploy.release-images` (ADR-0050). End users' riscv64 hosts need it: `install.sh` builds both images on the host (`deploy/install.sh:356`), and the web UI's builder stage cannot run on riscv64, since no `node` image exists for it.
- A first experiment needs no release images and takes about one and a half to two weeks: UNOS running on the K1 board as a dev device. It needs the entries `deploy.riscv64-supervisor-image` and `device.riscv64-dev-board`.
  - `scripts/dev/deploy.sh` already builds the web UI's static files on the workstation and only the Caddy stage on the device (ADR-0184), and it starts the stack with `docker compose up --no-build`.
  - The supervisor is built on the board, with Docker's data root off the eMMC.
- The estimate assumes UNOS does not build riscv64 images of third-party apps. On riscv64 the App Store would then offer the 6 apps whose publishers ship riscv64 images.

**The three biggest blockers and risks (judgment):**
1. **The app catalog is nearly empty on riscv64.** Only Navidrome, Prometheus, Zigbee2MQTT, Pi-hole, Traefik and Gitea publish riscv64 images. LinuxServer.io builds amd64 and arm64 only. Home Assistant has no plans for riscv64. The .NET apps have no official riscv64 runtime, and the official `node` image has no riscv64 variant. UNOS cannot close this gap at a reasonable cost.
2. **The K1 platform is young.**
   - Kernels: mainline support for storage and network arrived in 6.16 to 7.2, and USB 2.0 is still in review. The vendor kernel is 6.6.63, while the 6.6 LTS it is based on ends in December 2026.
   - Hardware reports: NVMe detection fails on some images, and USB-SATA adapters dropped disks once more than two were attached.
   - Other limits: the K1 has no SATA, and Armbian's support rests on one maintainer.
   - Operating systems: of the systems UNOS targets, Ubuntu stops at 24.04 on the K1. Debian's riscv64 port is expected to keep running on it (judgment: its baseline is believed to be RV64GC, unverified here).
   - Encryption: AES runs in software at about 30 MB/s. UNOS requires SMB signing whenever no guest share exists, and SMB 3 signing is AES-based. Whether that caps SMB throughput is untested.
3. **UNOS would own a riscv64 build chain.**
   - Docker comes from the distributions: Engine 26.1 and Compose 2.26 on Debian, with Debian's limited security support for Go packages.
   - cryptography and bcrypt have no riscv64 wheels. cryptography's maintainers closed the request until faster riscv64 builders exist; the infrastructure work for both is still an open pull request. Until wheels exist, UNOS rebuilds them for every security release.
   - CI: GitHub has no hosted riscv64 runner. RISE's free runners are documented for open-source projects, and whether they serve private repositories is unknown. A self-hosted runner would be UNOS's own to maintain, and QEMU builds take hours cold.
   - End users' installs wait on the release images, which do not exist yet.

## Recommended path (judgment)

1. **The owner decides the scope** with the proposed ADR below. The recommended scope:
   - riscv64 is an experimental third architecture, on Debian 13 and Ubuntu 24.04;
   - Docker comes from the distribution;
   - riscv64 hosts get prebuilt images, and the catalog knows each app's platforms;
   - the K1 board is a second dev device.

   Not recommended: builds on end users' riscv64 hosts, emulation of amd64 apps, and building third-party app images.
2. **The board comes first, before any UNOS change, without writing to the eMMC.** Package installs and reboots on a dev device are owner actions (ADR-0211). In this order:
   1. Identify the board. On a BPI-F3, `cat /sys/block/mmcblk*/device/name` prints `AJTD4R`, and the vendor kernel's `/proc/device-tree/model` says "spacemit k1-x deb1 board". The physical features are listed in area 5.
   2. Boot Armbian's Ubuntu 24.04 Minimal image for the BPI-F3, with the "current" 6.18 kernel, from a microSD card. The BPI-F3 ships with its boot switch set to microSD, so the eMMC stays untouched.
   3. Install `btrfs-progs`, `samba` and `smartmontools` from Ubuntu 24.04's archive, and `docker.io` with `docker-compose-v2` from its noble-updates pocket. Then run moby's `contrib/check-config.sh`.
   4. Check that an NVMe drive is detected and that its link trains at x2 (`lspci -vv`).
   5. Make a btrfs RAID1 pool on NVMe or USB disks and run a scrub, while reading `/sys/class/thermal/*/temp`.
   6. Measure AES speed with `openssl speed -evp aes-128-gcm`, then copy files over SMB with UNOS's settings. `supervisor/src/shares/smbconf.py:129` renders `server signing = mandatory` unless a guest share exists.
   7. Read the eMMC's life-time estimate (`mmc extcsd read`, from mmc-utils) and its sector counter (`/sys/block/mmcblkN/stat`), before and after all of the above.

   Each result decides one risk in this report.
3. **Then the features**, in this order (the order of the proposed ledger entries below):
   1. `deploy.riscv64-supervisor-image`: pip, wheels and the builder stage. Without it nothing builds well on riscv64.
   2. `device.riscv64-dev-board`: UNOS on the board through `scripts/dev/deploy.sh`, the first experiment.
   3. `apps.catalog-platforms`. It is useful on every architecture: `readarr:develop` publishes no image for any platform today.
   4. `deploy.release-images` (already in the ledger, ADR-0050), then `deploy.riscv64-release-images` and `ci.riscv64-backend-tests`.
   5. `deploy.riscv64-install`: `install.sh` on riscv64 pulls the release images.
   6. `testing.riscv64-vm`: the host flows in a riscv64 VM, from the same release images.

## 1. Base images

**Facts.**
- `python:3.11-slim` is the stanza `3.11.18-slim-trixie` (Debian 13) and lists riscv64. The `-slim-bookworm` variants do not. 3.12, 3.13 and 3.14 slim (trixie) and `3.11-alpine` (Alpine 3.24) list riscv64 too. Sources: the [docker-library python definition file](https://raw.githubusercontent.com/docker-library/official-images/master/library/python) and the [Docker Hub tag API](https://hub.docker.com/v2/repositories/library/python/tags/3.11-slim).
- The riscv64 image of `python:3.11-slim` was last pushed on 2026-11-02, the other architectures on 2026-11-05. The riscv64 variant lags by days ([Docker Hub tag API](https://hub.docker.com/v2/repositories/library/python/tags/3.11-slim)).
- On riscv64 the image builds CPython without LTO (`--with-lto` is skipped for riscv64). Its pip is CPython 3.11.18's bundled pip 24.0 ([python 3.11 slim-trixie Dockerfile](https://raw.githubusercontent.com/docker-library/python/master/3.11/slim-trixie/Dockerfile), [cpython v3.11.18 ensurepip](https://raw.githubusercontent.com/python/cpython/v3.11.18/Lib/ensurepip/__init__.py)). See area 2 for why the pip version matters.
- `caddy:2-alpine` is the `2.11/alpine` stanza, with amd64, arm64v8, arm32v6, arm32v7, ppc64le, riscv64 and s390x. On Linux, `caddy:2` is the same Alpine image ([caddy definition file](https://raw.githubusercontent.com/docker-library/official-images/master/library/caddy), [Docker Hub tag API](https://hub.docker.com/v2/repositories/library/caddy/tags/2-alpine)).
- No `node` tag has riscv64. That covers 22, 24 and 26, in their alpine, bookworm and trixie variants; `node:22-alpine` has amd64, arm64, arm/v6, arm/v7 and s390x ([node definition file](https://raw.githubusercontent.com/docker-library/official-images/master/library/node), [Docker Hub tag API](https://hub.docker.com/v2/repositories/library/node/tags/22-alpine)).
- Node.js itself:
  - Upstream rates linux riscv64 "Experimental", and the core team publishes no releases for it ([BUILDING.md](https://raw.githubusercontent.com/nodejs/node/main/BUILDING.md)).
  - The unofficial builds have linux-riscv64 binaries for v22.23.4 and v24.22.2 ([index.json](https://unofficial-builds.nodejs.org/download/release/index.json)).
  - Alpine 3.24 ships nodejs 24.22.1 (main) and npm 11.12.1 (community) for riscv64 ([pkgs.alpinelinux.org](https://pkgs.alpinelinux.org/packages?name=nodejs*&branch=v3.24&repo=&arch=riscv64)).
  - Debian trixie's nodejs 20.19.2 is built for riscv64 ([madison](https://qa.debian.org/madison.php?package=docker.io+docker-cli+docker-compose+docker-buildx+containerd+runc+tini+nodejs+npm&s=bookworm-backports,trixie,trixie-backports,forky,sid&text=on)).
- `alpine:latest` is 3.24.4 and lists riscv64 ([alpine definition file](https://raw.githubusercontent.com/docker-library/official-images/master/library/alpine)). `deploy/uninstall.sh:82-85` runs it.
- `debian:trixie-slim` and `ubuntu:24.04` list riscv64; `debian:bookworm` does not ([debian](https://raw.githubusercontent.com/docker-library/official-images/master/library/debian) and [ubuntu](https://raw.githubusercontent.com/docker-library/official-images/master/library/ubuntu) definition files). `ubuntu:latest` is now 26.04, which targets RVA23 (area 5).
- In the UNOS tree:
  - `webui/package-lock.json` already lists the riscv64 builds of the web UI's native build tools: `@esbuild/linux-riscv64` 0.21.5, and `@rollup/rollup-linux-riscv64-gnu` and `-musl` 4.56.0 (lines 296-301 and 741-760).
  - `webui/Dockerfile:4` starts the builder `FROM node:22-alpine` with no `--platform`.
  - `supervisor/tests/test_deploy_scripts.py:2159` requires that line to read exactly `FROM node:<major>-alpine AS builder`.
  - `scripts/dev/deploy.sh:111-117` builds only the final Caddy stage on the device (ADR-0184).

**Work for UNOS.**
- `webui/Dockerfile`: `FROM --platform=$BUILDPLATFORM node:22-alpine AS builder`. Its output is static files, so the riscv64 web UI image then runs no Node.js and no RUN step for riscv64. Adjust the regex at `test_deploy_scripts.py:2159`. The final-stage checks (`:1885`) and `deploy.sh` are unaffected.
- `supervisor/Dockerfile` and `supervisor/Dockerfile.test`: name `python:3.11-slim-trixie` explicitly, because bookworm has no riscv64. Pin it by digest along with `deploy.digest-pinned-base-images`.
- No web UI builds on riscv64 hosts. `install.sh` and `update.sh` run `docker compose build` (`deploy/install.sh:356`), which on a riscv64 host would need a riscv64 Node for the builder; `--platform=$BUILDPLATFORM` does not help there, because the build platform is the host itself. riscv64 hosts should therefore pull the web UI image (ADR-0050), as the dev device already avoids the builder stage (ADR-0184). The alternative would be a builder on Alpine's `nodejs` package, which is Node 24. That would mean moving the image, CI and `engines` to Node 24 together, since a test requires them to share one major.

**Size: S. Risks:** low. The riscv64 variants of official images lag the other architectures by days. A digest pin per platform makes that explicit.

## 2. Python dependencies

**Facts.**
- **pip.** `python:3.11-slim` ships pip 24.0. In pip 24.0's vendored `packaging/_manylinux.py`, the architectures that get manylinux tags are x86_64, aarch64, ppc64, ppc64le and s390x, plus armv7l and i686 through checks of their own. riscv64 therefore gets no manylinux tag, and pip ignores every riscv64 wheel. pip 24.1 added riscv64 (and loongarch64). Sources: [pip 24.0 `_manylinux.py`](https://raw.githubusercontent.com/pypa/pip/24.0/src/pip/_vendor/packaging/_manylinux.py), [pip 24.1 `_manylinux.py`](https://raw.githubusercontent.com/pypa/pip/24.1/src/pip/_vendor/packaging/_manylinux.py), [RISE manylinux page](https://pypi.riseproject.dev/manylinux.html).
- **`supervisor/Dockerfile:5-17`** installs `gcc` and `python3-dev` with apt and then runs `pip install --no-cache-dir -r requirements.txt` with the stock pip. In trixie, `python3-dev` is Debian's Python 3.13 (3.13.5-1); the image's own Python is 3.11 ([madison](https://qa.debian.org/madison.php?package=gcc+python3-dev+procps+iproute2+ethtool+btrfs-progs+smartmontools+systemd&s=trixie&a=riscv64&text=on)).
- **Compiled packages at the pinned versions.** Riscv64 wheels for CPython 3.11, checked with the PyPI JSON API (`https://pypi.org/pypi/<name>/<version>/json`):

| Package | glibc riscv64 wheel | musl riscv64 wheel | Pure-Python wheel | Notes |
| --- | --- | --- | --- | --- |
| pydantic_core 2.50.4 | yes, `cp311-manylinux_2_31_riscv64` | no | no | Rust 1.88 to build from source |
| greenlet 3.5.6 | yes, `cp311-manylinux_2_39_riscv64` | no | no | riscv64 wheels since 3.4.0 (2026-04-08) |
| httptools 0.9.0 | yes | yes | no | |
| watchfiles 1.3.0 | yes, `cp310-abi3-manylinux_2_31_riscv64` | no | no | only for `--reload` |
| websockets 17.4 | yes | yes | yes | |
| SQLAlchemy 2.1.8 | yes | yes | yes | |
| charset-normalizer 3.5.2 | yes | yes | yes | |
| wrapt 2.5.2 | yes | yes | yes | |
| coverage 7.17.2 (tests) | yes | yes | yes | |
| cryptography 50.0.2 | **no** | no | no | latest release; Rust 1.83 and OpenSSL headers |
| cffi 2.1.1 | **no** | no | no | riscv64 CI merged 2026-09-09, after 2.1.1 |
| bcrypt 5.0.0 | **no** | no | no | latest release; Rust 1.74 |
| uvloop 0.23.0 | **no** | no | no | latest release; builds the bundled libuv |
| PyYAML 6.0.3 | **no** | no | no | the build falls back to pure Python without libyaml |
| psutil 5.9.8 | **no** | no | no | no Linux aarch64 wheel either; 7.2.2, the latest, has no riscv64 either |

  Sources: [pydantic_core](https://pypi.org/pypi/pydantic_core/2.50.4/json), [greenlet](https://pypi.org/pypi/greenlet/3.5.6/json), [httptools](https://pypi.org/pypi/httptools/0.9.0/json), [watchfiles](https://pypi.org/pypi/watchfiles/1.3.0/json), [websockets](https://pypi.org/pypi/websockets/17.4/json), [SQLAlchemy](https://pypi.org/pypi/SQLAlchemy/2.1.8/json), [charset-normalizer](https://pypi.org/pypi/charset-normalizer/3.5.2/json), [wrapt](https://pypi.org/pypi/wrapt/2.5.2/json), [coverage](https://pypi.org/pypi/coverage/7.17.2/json), [cryptography](https://pypi.org/pypi/cryptography/50.0.2/json), [cffi](https://pypi.org/pypi/cffi/2.1.1/json), [bcrypt](https://pypi.org/pypi/bcrypt/5.0.0/json), [uvloop](https://pypi.org/pypi/uvloop/0.23.0/json), [PyYAML](https://pypi.org/pypi/PyYAML/6.0.3/json), [psutil 5.9.8](https://pypi.org/pypi/psutil/5.9.8/json), [psutil 7.2.2](https://pypi.org/pypi/psutil/7.2.2/json); [greenlet changelog](https://greenlet.readthedocs.io/en/latest/changes.html), [cffi#227](https://github.com/python-cffi/cffi/pull/227), [PyYAML setup.py](https://raw.githubusercontent.com/yaml/pyyaml/6.0.3/setup.py).
- **Pure-Python packages.** Every other package of the tree ships a `py3-none-any` wheel. The exception is nut2 2.1.1, which is an sdist only but pure Python.
- **Upstream plans.**
  - cryptography closed its riscv64 wheel request on 2026-06-06 with "no action for us" until faster riscv64 builders exist ([pyca/cryptography#14460](https://github.com/pyca/cryptography/issues/14460)).
  - The pyca infrastructure PR for riscv64 images of cryptography and bcrypt was still open on 2026-11-04. On it, a maintainer judged bcrypt's build speed on RISE runners acceptable ("I think we're fine with that speed for bcrypt"), while cryptography's was the concern ([pyca/infra#749](https://github.com/pyca/infra/pull/749)).
  - uvloop's riscv64 request is open, with no maintainer reply ([MagicStack/uvloop#732](https://github.com/MagicStack/uvloop/issues/732)).
- **Toolchains.**
  - Debian trixie's rustc and cargo for riscv64 are 1.85.1, and trixie-backports has 1.96.1 ([madison rustc](https://qa.debian.org/madison.php?package=rustc&table=debian&a=riscv64)).
  - Rust's `riscv64gc-unknown-linux-gnu` is Tier 2 with host tools ([rustc platform support](https://doc.rust-lang.org/nightly/rustc/platform-support.html)).
  - cryptography documents `build-essential libssl-dev libffi-dev python3-dev cargo pkg-config` as its build packages ([installation](https://cryptography.io/en/latest/installation/)).
  - The minimum Rust versions: pydantic_core 2.50.4 needs 1.88 ([Cargo.toml](https://raw.githubusercontent.com/pydantic/pydantic/v2.14.3/pydantic-core/Cargo.toml)), bcrypt 5.0.0 needs 1.74 ([Cargo.toml](https://raw.githubusercontent.com/pyca/bcrypt/5.0.0/src/_bcrypt/Cargo.toml)), and watchfiles 1.3.0 needs 1.83 ([Cargo.toml](https://raw.githubusercontent.com/samuelcolvin/watchfiles/v1.3.0/Cargo.toml)).
- **Measured native build times.**
  - cryptography: about 8 min on a BananaPi F3, a SpacemiT K1 ([#14460](https://github.com/pyca/cryptography/issues/14460)), and about 10 min on a RISE runner ([pyca/infra#749](https://github.com/pyca/infra/pull/749)).
  - bcrypt: about 5 min 18 s for its whole test job, cold, on a RISE runner ([pyca/infra#749](https://github.com/pyca/infra/pull/749)).
  - cffi: about 2 min on a BananaPi F3 ([cffi#233](https://github.com/python-cffi/cffi/issues/233)).
  - uvloop: about 5 min ([uvloop#732](https://github.com/MagicStack/uvloop/issues/732)) and about 18 min ([DEV post](https://dev.to/gounthar/building-a-python-wheel-factory-for-risc-v-44ho)) on the same board, from the same author.
  - No measurement was found for pydantic_core, PyYAML, psutil, greenlet or watchfiles.
- **The packaging ecosystem.**
  - PyPI has accepted riscv64 manylinux and musllinux wheels since 2025-07-23 ([pypi/warehouse#18243](https://github.com/pypi/warehouse/issues/18243)).
  - The build images are `manylinux_2_39_riscv64` (labelled alpha) and `musllinux_1_2_riscv64` ([pypa/manylinux](https://github.com/pypa/manylinux)).
  - cibuildwheel has built riscv64 without an extra `enable` setting since 3.1.2, when a project requests it in `archs`; it is not in `auto` ([changelog](https://cibuildwheel.pypa.io/en/stable/changelog/), [options](https://cibuildwheel.pypa.io/en/stable/options/)).
- **Third-party wheels.** The RISE project's index (`https://pypi.riseproject.dev/simple/`) builds on native riscv64 hardware. It has cryptography 50.0.2, cffi, bcrypt, uvloop, PyYAML and psutil, and makes no signing claims ([RISE index](https://pypi.riseproject.dev/), [cryptography](https://pypi.riseproject.dev/project/cryptography/)).
- **Distribution packages.** Debian's `python3-*` packages are built for Debian's own Python (3.13 in trixie), and their versions trail the pins: python3-cryptography is 43.0.0 and python3-pydantic-core is 2.27.2 in trixie ([madison](https://qa.debian.org/madison.php?package=python3-cryptography+python3-pydantic-core+python3-bcrypt+python3-uvloop+python3-cffi-backend+python3-httptools+python3-watchfiles+python3-yaml+python3-psutil&table=debian&a=riscv64)).
- **uvicorn.** 0.54.0 needs only click and h11. uvloop, httptools, websockets, watchfiles, python-dotenv and PyYAML come from its `standard` extra, and it uses uvloop and httptools only "when installed" ([PyPI JSON](https://pypi.org/pypi/uvicorn/0.54.0/json), [docs](https://raw.githubusercontent.com/Kludex/uvicorn/main/docs/installation.md)). Nothing in `supervisor/src` imports `yaml`: PyYAML is in `requirements-dev.txt` for the tests.
- **Image sizes.** `rust:slim` for riscv64 is 395 MB compressed, against 44 MB for `python:3.11-slim` ([Docker Hub](https://hub.docker.com/v2/namespaces/library/repositories/rust/tags/slim), [Docker Hub](https://hub.docker.com/v2/namespaces/library/repositories/python/tags/3.11-slim)). BuildKit cache mounts and registry caches are documented ([Docker docs](https://docs.docker.com/build/cache/optimize/)).

**Work for UNOS.**
- `supervisor/Dockerfile`:
  - Install a pinned pip of 24.1 or later before the requirements. That one change brings 9 of the 15 compiled packages from wheels. It also means pydantic_core is never built from source, which would need Rust 1.88, newer than trixie's rustc 1.85.1.
  - Split the image. A builder stage on `python:3.11-slim-trixie` runs `pip wheel -r requirements.txt`, and the final stage installs only those wheels (`--no-index`).
    - The builder needs `gcc` on arm64 and riscv64, for psutil 5.9.8, which has no wheel for either.
    - Only when `TARGETARCH` is riscv64 does it add `cargo rustc pkg-config libssl-dev libffi-dev`. The Rust toolchain then never reaches the image layers that the arm64 dev device builds, and so never its SD card (`scripts/dev/deploy.sh:77-92` builds the supervisor there; ADR-0214).
    - Today the runtime image carries `gcc` and Debian's `python3-dev`, whose headers are for Debian's Python 3.13, not the image's 3.11. With the split it carries no compiler on any platform.
    - The first build of the changed Dockerfile reruns apt-get and pip on the dev device, which `deploy/AGENTS.md` says to tell the owner about first.
- `supervisor/requirements.txt`: replace `uvicorn[standard]` with plain `uvicorn`, plus explicit pins for `httptools`, `websockets` (already pinned) and `uvloop; platform_machine != "riscv64"`. watchfiles and PyYAML then drop out of the runtime; python-dotenv stays, because pydantic-settings needs it. The riscv64 source builds shrink to cryptography, cffi, bcrypt and psutil. amd64 and arm64 keep uvloop and httptools.
- Cache the builder stage. Use a registry cache keyed on `requirements.txt` in CI (area 7), or publish hash-pinned wheels as release assets. Treat the RISE index only as a cross-check, or pin its hashes: it is a separate trust root.
- `scripts/dev/tree.py` (`cmd_bundle`): evaluate requirement markers before comparing the rootfs with the pins. Otherwise a riscv64 rootfs without uvloop is reported as stale.
- Optional and separate: psutil 5.9.8 to 7.x. It adds aarch64 wheels, but no riscv64 ones.

**Size: M.** Estimate: two to four days.
- With pip upgraded and uvloop left out, a cold native build of cryptography, cffi, bcrypt and psutil on a K1 should take about 15 to 25 min (estimate, from the measurements above).
- Under QEMU it should take one to two hours or more (estimate; no published figure exists for this set).

**Risks.**
- UNOS becomes the builder of cryptography and bcrypt for riscv64: every security release needs a riscv64 rebuild.
- The rebuilt cryptography links Debian's OpenSSL dynamically, where PyPI's wheels bundle OpenSSL statically.
- Rust's minimum versions creep: cryptography needs 1.83 and trixie has 1.85.1, so keep trixie-backports or rustup at hand.
- Avoid Alpine (musl) for the supervisor: pydantic_core, greenlet and watchfiles would join the source builds.

## 3. Docker Engine and Compose

**Facts.**
- **Docker Inc.** publishes no riscv64 Engine packages or static binaries:
  - The trixie and noble Release files of download.docker.com list amd64, arm64, armhf, s390x and ppc64el ([trixie Release](https://download.docker.com/linux/debian/dists/trixie/Release), [noble Release](https://download.docker.com/linux/ubuntu/dists/noble/Release)).
  - The static binaries have no `riscv64/` directory ([static/stable](https://download.docker.com/linux/static/stable/)).
  - The install pages do not mention riscv64 ([Debian](https://docs.docker.com/engine/install/debian/), [Ubuntu](https://docs.docker.com/engine/install/ubuntu/)), and neither do the Engine 29 release notes, which run to 29.9.3 of 2026-11-03 ([release notes](https://docs.docker.com/engine/release-notes/29/)).
  - docker/cli added linux/riscv64 to its cross-build release target in March 2026 ([docker/cli#6857](https://github.com/docker/cli/issues/6857)). No riscv64 packages followed.
- **Debian 13 trixie** is the first Debian release with riscv64 as an official architecture ([release notes](https://www.debian.org/releases/trixie/release-notes/whats-new.en.html)).
  - On riscv64 it has `docker.io` 26.1.5, `docker-compose` 2.26.1 (Compose v2, which installs the CLI plugin as well as `/usr/bin/docker-compose`), `docker-buildx` 0.13.1, containerd 1.7.24 and runc 1.1.15. trixie-backports has none of them, and forky has Engine 28.5.2 with Compose 2.40.3 ([madison](https://qa.debian.org/madison.php?package=docker.io+docker-cli+docker-compose+docker-buildx+containerd+runc+tini+nodejs+npm&s=bookworm-backports,trixie,trixie-backports,forky,sid&text=on), [docker-compose filelist](https://packages.debian.org/trixie/riscv64/docker-compose/filelist)).
  - Its Go-based packages have limited security support, with fixes through point releases only ([release notes, issues](https://www.debian.org/releases/trixie/release-notes/issues.en.html)).
- **Ubuntu 24.04** has, in noble-updates on riscv64 (universe), `docker.io` 29.1.3, `docker-compose-v2` 2.40.3 (the plugin only) and `docker-buildx` 0.30.1. The release pocket has Compose 2.24.6 ([Ubuntu madison](https://ubuntu-archive-team.ubuntu.com/madison.cgi?package=docker.io+docker-compose-v2+docker-buildx+containerd+runc&a=riscv64&text=on)).
- **Bianbu**, SpacemiT's distribution, has its own `docker.io` 26.1.3 with `docker-compose-v2` 2.27.1 in its noble-based archive ([docker.io](https://archive.spacemit.com/bianbu/pool/universe/d/docker.io-app/), [docker-compose-v2](https://archive.spacemit.com/bianbu/pool/universe/d/docker-compose-v2/)).
- **Upstream Compose** v5.7.0 publishes `docker-compose-linux-riscv64` ([release assets](https://github.com/docker/compose/releases/expanded_assets/v5.7.0)). Since v5.0 it delegates `build` to Bake ([v5.0.0](https://github.com/docker/compose/releases/tag/v5.0.0)).
- **A community repository**, gounthar/docker-for-riscv64, builds Docker 29.x for trixie on a Banana Pi F3 and signs it with one maintainer's key ([README](https://github.com/gounthar/docker-for-riscv64/blob/main/README.md)).
- **In the UNOS tree:**
  - `deploy/install.sh:100-111` accepts the `/etc/os-release` IDs `debian`, `ubuntu` and `raspbian`, and asks before it continues on any other system. Bianbu's ID was not verified.
  - `deploy/install.sh:146-174` (`install_docker`) adds download.docker.com with `arch=$(dpkg --print-architecture)` and installs `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin` and `docker-compose-plugin`.
  - `deploy/install.sh:356` builds both images on the host (`docker compose build`).
  - `require_compose` (`:181-190`) already names the distribution packages.
  - `check_compose_wait` (`:204-219`) passes `--wait-timeout` where `up --help` lists it, and warns below Compose 2.30. `compose_before_2_30` (`:194-198`) treats any major version above 2, so Compose 5.x, as new enough.
  - `deploy/uninstall.sh:348` tells the user to purge `docker-ce docker-ce-cli containerd.io`.
  - The healthcheck comment of `deploy/docker-compose.yml` assumes Engine 25 or later (`start_interval`) and describes Engine 29's behaviour.

**Work for UNOS.**
- `deploy/install.sh`, when Docker's repository has no packages for the host's architecture (riscv64 today):
  - install `docker.io docker-compose` on Debian 13, or `docker.io docker-compose-v2` from Ubuntu 24.04's universe;
  - do not add download.docker.com.

  Debian's Compose 2.26.1 then gets the existing warning that it cannot limit the wait for the supervisor.
- On riscv64, `install.sh` pulls the release images instead of running `docker compose build`, because the web UI's builder cannot run there (area 1). This part waits for `deploy.release-images`.
- Tests in `supervisor/tests/test_deploy_scripts.py`, following the stubs at `:771-800` with `uname` printing riscv64 and `dpkg` printing riscv64, for Debian 13 and Ubuntu 24.04.
- The purge hint of `uninstall.sh` names the distribution's packages.
- `docs/INSTALL.md:39-43` and `:154-167` describe the riscv64 path.
- Check UNOS's Docker API use and the compose file against Engine 26.1 on the board or the VM. docker-py 7.2.0 negotiates the API version.

**Size: M.** Two to three days.

**Risks.**
- Patch latency: Debian 13's Go packages get limited security support through point releases, and Ubuntu's Docker packages are in universe.
- Engine skew: 26.1 (Debian) and 29.x (Ubuntu, Docker Inc.) run in the same fleet.
- A later switch to Docker Inc. packages, if they ever ship riscv64, means removing `docker.io`, which conflicts with `docker-ce`.
- Rejected options (judgment): side-loading the upstream Compose binary, which UNOS would then have to update, and the community repository, which rests on one key.

## 4. Host tools

**Facts.**
- Every package `deploy/install.sh:122-140` installs, and every program in `HOST_TOOLS` of `supervisor/src/host.py:35-57`, is built for riscv64 in Debian 13 and in Ubuntu 24.04. Nothing is missing ([Debian madison](https://qa.debian.org/madison.php?package=curl+wget+git+ca-certificates+gnupg+lsb-release+btrfs-progs+smartmontools+lm-sensors+samba+samba-common-bin+hdparm+parted+mdadm+nvme-cli+ethtool+net-tools+iproute2+util-linux+mount+e2fsprogs+udev+systemd+passwd+libc-bin&s=trixie&text=on), [Ubuntu madison](https://ubuntu-archive-team.ubuntu.com/madison.cgi?package=curl+wget+git+ca-certificates+gnupg+lsb-release+btrfs-progs+smartmontools+lm-sensors+samba+samba-common-bin+hdparm+parted+mdadm+nvme-cli+ethtool+net-tools+iproute2+util-linux+mount+e2fsprogs+udev+systemd+passwd+libc-bin+nodejs&s=noble,noble-updates,noble-security&a=riscv64&text=on)). Versions:

| Package | Debian 13 | Ubuntu 24.04 |
| --- | --- | --- |
| btrfs-progs | 6.14-1 | 6.6.3 |
| smartmontools | 7.4-3 | 7.4-2 |
| samba, samba-common-bin | 4.22.11 | 4.19.5 |
| lm-sensors | 3.6.2 | 3.6.0 (universe) |

- The supervisor image's apt packages (gcc 14.2, python3-dev, procps, iproute2, ethtool, systemd) are built for riscv64 in trixie ([madison](https://qa.debian.org/madison.php?package=gcc+python3-dev+procps+iproute2+ethtool+btrfs-progs+smartmontools+systemd&s=trixie&a=riscv64&text=on)).
- Not checked: `ufw` and `upsc` (from `nut-client`), which the security and power routers call outside `HOST_TOOLS` (`supervisor/src/security/router.py:126`, `supervisor/src/power/router.py:120`).
- lm-sensors shows a SoC temperature only if the kernel exposes one. The mainline K1 thermal driver arrived in Linux 7.2, according to [SpacemiT's upstream status page](https://github.com/spacemit-com/linux/wiki). Its patch registers hwmon devices ([patch v4](https://patchew.org/linux/20260410-k1-thermal-v1-0-12c87dd063c3@mailbox.org/20260410-k1-thermal-v1-2-12c87dd063c3@mailbox.org)). The vendor kernel has its own driver, and Armbian's edge config enables `SPACEMIT_K1_TSENSOR` ([config](https://raw.githubusercontent.com/armbian/build/main/config/kernel/linux-spacemit-edge.config)).
- UNOS reads temperatures with `psutil.sensors_temperatures()` (`supervisor/src/system/router.py:85`, `:373`, `supervisor/src/websocket/events.py:204`). psutil 5.9.8 falls back to `/sys/class/thermal/thermal_zone*` when no hwmon sensor exists (`psutil/_pslinux.py:1443-1444` of the 5.9.8 release, read in an installed copy). No sensor name is hard-coded.
- No riscv64-specific issue was found for smartmontools.

**Work for UNOS.** None in the code. The docs name the riscv64 distributions. On Ubuntu, universe must be enabled for lm-sensors and Docker.

**Size: S. Risks:** low. Temperatures may be missing on a K1 kernel without the thermal driver; UNOS already tolerates an empty sensor list.

## 5. The SpacemiT K1 platform

### 5.1 The owner's board

**Facts.**
- **eMMC.** The Banana Pi BPI-F3 is the only K1 board found with a 16 GB Samsung eMMC soldered to the main board.
  - A BPI-F3 owner's kernel log shows `mmcblk2: mmc2:0001 AJTD4R 14.6 GiB` with two 4 MiB boot partitions, and the vendor kernel names the machine "spacemit k1-x deb1 board" ([BPI forum](https://forum.banana-pi.org/t/bpi-f3-running-ubuntu-24-04/18478)).
  - Samsung's datasheet gives the KLMAG1JETD-B041 a user density of 15,634,268,160 bytes (14.56 GiB) and 4 MB boot partitions ([datasheet copy](https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/791/Samsung-eMMC_2D00_2611204514.pdf)).
  - The other 16 GB eMMC options sit on modules: the Banana Pi BPI-CM6 and the Milk-V Jupiter NX. The Milk-V Jupiter and the Orange Pi RV2 take eMMC as a plug-in module. The Muse Pi and Muse Pi Pro carry the M1 package, and the Orange Pi boards sell the chip as the "Ky X1", which kernel developers call a K1 rebrand ([CNX Muse Pi Pro](https://www.cnx-software.com/2025/04/27/muse-pi-pro-feature-packed-credit-card-sized-spacemit-m1-risc-v-sbc/), [patchew](https://patchew.org/linux/20250813092240.180333-1-hendrik.hamerlinck@hammernet.be) (snippet)).
- **The BPI-F3's features** ([Banana Pi wiki](https://wiki.banana-pi.org/Banana_Pi_BPI-F3), [CNX](https://www.cnx-software.com/2024/05/10/banana-pi-bpi-f3-sbc-spacemit-k1-octa-core-risc-v-ai-soc/)):
  - a 148 x 100 mm board with 2/4/8/16 GB RAM;
  - two Gigabit ports (RTL8211F);
  - four USB-A 3.0 ports behind one VL817 hub;
  - an M.2 M-key slot (PCIe 2.1 x2) and a mini-PCIe slot (x1) with a SIM holder;
  - a 12 V/3 A barrel jack and USB-C;
  - a boot DIP switch (SW1), shipped set to microSD ([BPI forum](https://forum.banana-pi.org/t/boot-selection-switch/18112)).
- **"6370".** No source explains it. It cannot be a year-and-week date code, since there is no week 70.

**Judgment.** The board is most likely a BPI-F3 with 4 GB RAM and 16 GB eMMC. The software check is `cat /sys/block/mmcblk*/device/name`, which prints `AJTD4R` on a BPI-F3.

### 5.2 The SoC

**Facts.**
- **CPU.** 8 X60 cores in two clusters, RVA22 with RVV 1.0 (VLEN 256), at 1.6 GHz (the M1 package at 1.8 GHz) ([Bit-Brick processor brief](https://bit-brick-docs.pages.dev/docs/k1/hardware/processor-brief), [Milk-V](https://milkv.io/docs/jupiter/overview)), made in a 22 nm process ([ITHome](https://www.ithome.com/0/765/223.htm)). SpacemiT claims single-core performance of 130% of a Cortex-A55 ([ITHome](https://www.ithome.com/0/765/714.htm)).
- **Memory.** Up to 16 GB of 32-bit LPDDR4/4X.
- **I/O.**
  - PCIe Gen2: port A x1, port B x2, port C x2. Port A shares its PHY with the only USB 3.0 controller.
  - Two RGMII Gigabit MACs; no SATA.
  - eMMC 5.1 and SD.
  - Boot only from SPI NOR, SPI NAND, eMMC or SD ([Bit-Brick processor brief](https://bit-brick-docs.pages.dev/docs/k1/hardware/processor-brief), [kernelconfig.io](https://www.kernelconfig.io/CONFIG_PCIE_SPACEMIT_K1) (snippet)).
- **AES** is not accelerated: OpenSSL AES ran at about 30 MB/s on a Milk-V Jupiter ([CNX review](https://www.cnx-software.com/2024/08/11/milk-v-jupiter-review-risc-v-mini-itx-motherboard-and-pc-ubuntu-based-bianbu-os/)).
- **Vectors.** The X60 cannot do misaligned vector memory access, which breaks some GCC 14 autovectorised builds ([Milk-V community](https://community.milkv.io/t/spacemit-k1-m1-is-not-quite-rva22-compliant/2870)).

### 5.3 Operating systems and kernels

**Facts.**
- **Ubuntu.** From 25.10, Ubuntu's RISC-V kernel supports only RVA23S64 hardware, and 24.04 LTS continues for older hardware ([25.10 release notes](https://documentation.ubuntu.com/release-notes/25.10/), [ubuntu.com/download/risc-v](https://ubuntu.com/download/risc-v)).
  - SpacemiT staff wrote on 2026-02-04 that Ubuntu 26.04 cannot run on the K1, which supports only RVA22, and that the K1 stays on its Ubuntu 24.04-based Bianbu long term ([SpacemiT forum](https://forum.spacemit.com/t/topic/939)).
  - Canonical's download page lists no K1 board.
- **Bianbu**, SpacemiT's Ubuntu 24.04-based distribution:
  - Its newest K1 image is 2.3.5 (2026-08-27), in Minimal, GNOME and LXQt variants, with no NAS or server variant ([archive.spacemit.com](https://archive.spacemit.com/image/k1/version/bianbu/v2.3.5/)).
  - Its kernel is the vendor 6.6.63. The 6.6 LTS is supported until December 2026 ([CNX](https://www.cnx-software.com/2026/01/08/muse-book-laptop-review-testing-an-octa-core-risc-v-linux-laptop-in-2026/)), and upstream 6.6 is at 6.6.162 ([kernel.org](https://www.kernel.org/)).
  - Bianbu 3.0 (Ubuntu 25.04) stopped at 3.0.1 in August 2025.
  - SpacemiT also publishes its own Debian 13 images for the K1, from September 2025 ([archive](https://archive.spacemit.com/image/k1/version/debian/)).
- **Armbian** supports the BPI-F3 at the "Standard" level ([armbian.com/bananapi-f3](https://www.armbian.com/bananapi-f3/)):
  - Ubuntu 24.04 and Debian 13 Minimal images;
  - a "current" kernel 6.18.45, a Fedora kernel-ark tree with K1 patches, and a "legacy" kernel 6.6.100, the vendor tree;
  - boot firmware from the vendor's U-Boot and OpenSBI ([spacemit.conf](https://raw.githubusercontent.com/armbian/build/main/config/sources/families/spacemit.conf));
  - one maintainer, who also maintains the MusePi Pro ([armbian.com/bananapi-f3](https://www.armbian.com/bananapi-f3/), [armbian.com/musepi-pro](https://www.armbian.com/musepi-pro/)).

  The MusePi Pro has "Platinum" support. No Armbian page exists for the Milk-V Jupiter.
- **Other systems** with K1 images or guides, such as Fedora ([Fedora wiki](https://fedoraproject.org/wiki/Architectures/RISC-V/SpacemiT-Fedora-Install) (snippet)) and OpenHarmony ([archive](https://archive.spacemit.com/image/k1/version/)), are outside UNOS's Debian and Ubuntu scope (ADR-0002).
- **Mainline**, per [SpacemiT's upstream status page](https://github.com/spacemit-com/linux/wiki) (edited 2026-11-05):

| First mainline release | K1 parts |
| --- | --- |
| 6.16 | eMMC (SDHCI), clocks, GPIO |
| 6.18 | Ethernet (EMAC), USB 3.0, DMA, PMIC |
| 6.19 | PCIe host; its PHY drivers were applied in December 2025 ([linux-riscv](http://lists.infradead.org/pipermail/linux-riscv/2025-December/082424.html) (snippet)) |
| 7.2 | SD card, thermal, SPI, cpufreq (Armbian still carried its own cpufreq patch on 7.2 ([armbian/build#10123](https://github.com/armbian/build/pull/10123) (snippet)), so possibly 7.3) |
| in review | USB 2.0, the timer |

  Mainline is at 7.4-rc1 and stable at 7.3.3 ([kernel.org](https://www.kernel.org/)). Armbian's 7.3 "edge" branch was boot-tested on the MusePi Pro only ([armbian/build#10965](https://github.com/armbian/build/pull/10965)).
- **btrfs** is a module (`CONFIG_BTRFS_FS=m`, with POSIX ACLs) in Bianbu's vendor `k1_defconfig` ([gitee](https://gitee.com/bianbu-linux/linux-6.6/raw/k1-bl-v2.2.y/arch/riscv/configs/k1_defconfig)) and in Armbian's legacy, current and edge configs ([current](https://raw.githubusercontent.com/armbian/build/main/config/kernel/linux-spacemit-current.config)).
  - btrfs-progs is in Bianbu's archive ([pool](https://archive.spacemit.com/bianbu/pool/main/b/btrfs-progs/)).
  - A Zbc-accelerated CRC32C path was benchmarked on a K1, at +125% for crc32c ([commit](https://lab.nexedi.cn/kirr/linux/-/commit/a43fe27d650375cd9e5ea915c538f6f9eabd185e) (snippet)).
  - No riscv64- or K1-specific btrfs bug, and no btrfs benchmark on a K1, was found.
- **Docker on K1 kernels.** Read statically, the K1 kernel configs contain the options Docker needs (overlayfs, cgroups, veth, bridge, br_netfilter, nf_tables). Armbian's 6.18 config does not list the legacy iptables filter table, so iptables-nft is the safe choice. moby's `check-config.sh` was not run on a real kernel.

### 5.4 Storage, network and power

**Facts.**
- **NVMe** reached 708/731 MB/s read/write on a Jupiter with the link at Gen2 x2 ([CNX review](https://www.cnx-software.com/2024/08/11/milk-v-jupiter-review-risc-v-mini-itx-motherboard-and-pc-ubuntu-based-bianbu-os/)), and about 800 MB/s on an Orange Pi RV2 ([AndroidPIMP](https://androidpimp.com/embedded/orange-pi-rv2/7) (snippet)). One Jupiter trained at x1 and reached about 315-346 MB/s ([Geerling](https://github.com/geerlingguy/sbc-reviews/issues/47)).
- **NVMe detection.**
  - On a BPI-F3, NVMe was not detected with Debian ("Phy link never came up"), while Bianbu images found it (December 2025) ([BPI forum](https://forum.banana-pi.org/t/bpi-f3-using-nvme-debian/26278)).
  - A Jupiter hung on mainline 7.1 and 7.2 with one add-in card; the cause was traced to the card's PERST# pull-down, and the fix was not yet merged ([linux-pci thread](https://ratatoskr.run/linux-pci/2026/08/17456851/t)).
- **SATA** needs a PCIe controller.
  - Banana Pi documents a JMB582 card in the M.2 slot ([wiki](https://wiki.banana-pi.org/Banana_Pi_BPI-F3)). On the BPI-F3 that card uses up the only NVMe slot.
  - One user reports an ASM1166 card working on a Jupiter with one disk ([Milk-V community](https://community.milkv.io/t/pcie-sata-controller-recommendation/3400)).
  - On a BPI-F3, USB-SATA adapters behind the VL817 hub dropped disks once more than two HDDs were attached, and UAS could not be turned off in Bianbu's NAS image ([BPI forum](https://forum.banana-pi.org/t/spacemit-k1-8-core-risc-v-chip-for-nas-design-with-openmediavault/17969)).
  - The BPI-F3 takes 12 V/3 A ([CNX](https://www.cnx-software.com/2024/05/10/banana-pi-bpi-f3-sbc-spacemit-k1-octa-core-risc-v-ai-soc/)). Judgment: that cannot spin up several 3.5-inch disks, so a multi-disk build needs its own disk supply.
- **Network.** Each Gigabit port reached about 940 Mbps in iperf3, and full duplex on one port dropped to about 670-730 Mbps each way ([CNX review](https://www.cnx-software.com/2024/08/11/milk-v-jupiter-review-risc-v-mini-itx-motherboard-and-pc-ubuntu-based-bianbu-os/)).
- **Power.** About 3.5-5 W idle and 8-11 W under CPU load for a whole Jupiter ([Geerling](https://github.com/geerlingguy/sbc-reviews/issues/47)).
- **Not found:** any SMB, btrfs or fio measurement for any K1 board.
- **The eMMC.** The KLMAG1JETD is eMMC 5.1, rated 330 MB/s read and 50 MB/s write. Samsung publishes no endurance figure; the part reports wear through the EXT_CSD life-time fields ([datasheet copy](https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/791/Samsung-eMMC_2D00_2611204514.pdf)).
  - The K1 cannot boot from NVMe or USB.
  - BPI-F3 users have kept U-Boot and `/boot` on the eMMC with the root filesystem on NVMe since 2024. Newer Bianbu releases need a changed U-Boot setting for this ([BPI forum](https://forum.banana-pi.org/t/how-to-boot-through-nvme/18316)).

**Work for UNOS.** No code for the platform itself. The work is a bring-up run by the owner and an agent: the steps of the recommended path, and docs naming the tested board, operating system and kernel. In the dev setup:
- Docker's data root, the image layers and the build cache go on NVMe, not on the eMMC, as `device.docker-data-root-on-hdd` proposes for the first device.
- The root filesystem stays on microSD or NVMe.

**Size: M.** Three to five days, the owner's time included. If NVMe or USB storage misbehaves on the chosen kernel, it becomes L.

**Risks:** high.
- The kernels are young or old: mainline storage and network support is from 6.16 to 7.2, and the vendor 6.6.63 kernel trails its LTS, which ends in December 2026.
- Storage detection depends on the image and the card.
- Several USB disks behind one hub dropped out.
- Support rests on one Armbian maintainer, and the boot firmware is vendor code.
- Ubuntu stops at 24.04 on the K1. Debian's riscv64 releases should keep running on it, but that rests on the port's RV64GC baseline, which was not verified here.
- Encryption runs in software at about 30 MB/s. UNOS's smb.conf requires signing unless a guest share exists (`supervisor/src/shares/smbconf.py:129`), and SMB 3 signing is AES-based (this report relies on Microsoft's MS-SMB2 specification here, not re-checked). Whether that caps SMB below Gigabit speed is untested, so it is the first measurement to make.

## 6. The app catalog

**Facts.**
- **Method.**
  - Every image and tag of `supervisor/src/apps/catalog.py`, all 70, was checked.
  - Docker Hub images were read with the tag API (`https://hub.docker.com/v2/repositories/<namespace>/<repository>/tags/<tag>`, `library` for official images). LinuxServer images were read as their Docker Hub mirrors `linuxserver/<name>`.
  - GHCR images were read as the exact tag's OCI index through [explore.ggcr.dev](https://explore.ggcr.dev/) (`?image=<reference>`), and cross-checked with Docker Hub mirrors where they exist.
  - LinuxServer's [fleet API](https://api.linuxserver.io/api/v1/images?include_config=false&include_deprecated=true) gave its stated architectures.
- **With linux/riscv64: 6 of 70.**

| Image and tag | Platforms |
| --- | --- |
| `deluan/navidrome:latest` | amd64, arm64, 386, arm/v7, arm/v6, riscv64 |
| `prom/prometheus:latest` | amd64, arm64, arm/v7, ppc64le, s390x, riscv64 |
| `koenkk/zigbee2mqtt:latest` | amd64, arm64, 386, arm/v7, arm/v6, riscv64 (built FROM `alpine:3.24` with Alpine's nodejs) |
| `pihole/pihole:latest` | amd64, arm64, 386, arm/v7, arm/v6, riscv64 |
| `traefik:v3.0` | amd64, arm64, arm/v6, ppc64le, s390x, riscv64 (last pushed 2024-07-03; `traefik:v3` has riscv64 too) |
| `gitea/gitea:latest` | amd64, arm64, riscv64 |

- **Without linux/riscv64: 64 of 70.**
  - LinuxServer.io, all 23. Each has amd64 and arm64 only: emby, calibre-web, sonarr, radarr, lidarr, prowlarr, bazarr, overseerr (deprecated), tautulli, transmission, qbittorrent, deluge, sabnzbd, nzbget, nextcloud, syncthing, bookstack, wikijs, heimdall, wireguard, duplicati, code-server. `readarr:develop` has no platform at all: its index is empty on Docker Hub and on GHCR, and the image has been deprecated since 2025-06-27.
  - GHCR, all 12. These have amd64 and arm64: immich-server (`release`), audiobookshelf, paperless-ngx, homepage, home-assistant (`stable`), frigate (`stable`), scrypted, open-webui (`main`), mealie, linkwarden. homarr (archived) adds arm/v7, and changedetection.io adds arm/v7 and arm/v8.
  - Docker Hub, 29 of 35:
    - amd64 and arm64 only: jellyfin, photoprism, frooodle/s-pdf, nicolargo/glances (`latest-full`), nodered/node-red, jc21/nginx-proxy-manager, ollama, localai (`latest-aio-cpu`), actualbudget/actual-server, n8nio/n8n.
    - plus arm/v7: plexinc/pms-docker, jvmilazz0/kavita, filebrowser, louislam/uptime-kuma, grafana, netdata, authelia.
    - other sets:
      - jlesage/jdownloader-2: plus 386.
      - p3terx/aria2-pro: plus 386, arm/v7 and arm/v6; unchanged since 2022-09-06.
      - zadam/trilium: plus arm/v7 and arm/v8.
      - portainer-ce: plus arm/v7, ppc64le and Windows.
      - amir20/dozzle: plus arm/v7 and arm/v6.
      - eclipse-mosquitto: plus arm/v6, 386, ppc64le and s390x.
      - adguard/adguardhome: plus 386, arm/v7, arm/v6 and ppc64le.
      - tailscale: plus 386 and arm/v7.
      - restic/rest-server: plus 386, arm/v7, arm/v6 and ppc64le.
      - vikunja: plus arm/v7 and arm/v6.
      - vaultwarden/server: plus arm/v7 and arm/v6.
      - crowdsecurity/crowdsec: plus 386, arm/v7 and arm/v6.
- **Publishers.**
  - LinuxServer.io builds x86_64 and arm64 only. It deprecated armhf on 2023-07-01 ([notice](https://info.linuxserver.io/issues/2023-07-01-armhf)). Its riscv64 builds of sabnzbd ran from December 2025 until "Temporarily disable RISCV builds" on 2026-05-31 ([commits](https://github.com/linuxserver/docker-sabnzbd/commits/master/jenkins-vars.yml)).
  - Home Assistant answered on 2026-05-07 that it does not plan official riscv64 support ([discussion #1107](https://github.com/home-assistant/architecture/discussions/1107)).
  - .NET 10's supported platforms do not include RISC-V ([supported-os.md](https://github.com/dotnet/core/blob/main/release-notes/10.0/supported-os.md)). That affects Sonarr, Radarr, Lidarr, Prowlarr, Emby, Duplicati, Jellyfin and Kavita.
  - Plex's downloads list no riscv64 build ([plex.tv downloads API](https://plex.tv/api/downloads/5.json)).
- **Closest to a riscv64 image.**
  - Syncthing, nzbget, filebrowser, AdGuard Home and Vikunja ship riscv64 release binaries ([syncthing](https://api.github.com/repos/syncthing/syncthing/releases/latest), [nzbget](https://api.github.com/repos/nzbgetcom/nzbget/releases/latest), [filebrowser](https://api.github.com/repos/filebrowser/filebrowser/releases/latest), [AdGuard Home](https://api.github.com/repos/AdguardTeam/AdGuardHome/releases/latest), [Vikunja](https://api.github.com/repos/go-vikunja/vikunja/releases/latest)).
  - Tailscale merged riscv64 container builds on 2026-03-29 ([tailscale#19165](https://github.com/tailscale/tailscale/pull/19165)), but its `latest` image has none.
- **In the UNOS tree:**
  - `AppTemplate` (`supervisor/src/apps/schemas.py:81-101`) has no platform field.
  - `GET /api/v1/apps/catalog` (`supervisor/src/apps/router.py:55`) lists every template on every host.
  - `_install_app` validates the request, then calls `client.images.pull(template.image)` (`router.py:643-645`), and any Docker error becomes `500 Failed to pull image: ...`.
  - docker-py 7.2.0 can ask the registry first: `client.images.get_registry_data(name).has_platform("linux/riscv64")` calls the Engine's `GET /distribution/{name}/json`, which pulls no layers (`docker/models/images.py:179`, `:335` and `docker/api/image.py:285` of 7.2.0, read in an installed copy).

**Judgment.**
- On a riscv64 host, every pull of the 64 images without riscv64 should fail with Docker's "no matching manifest for linux/riscv64". All of them are multi-platform indexes. This was not tested.

**Work for UNOS.**
- Add `platforms` to `AppTemplate`, filled for the 70 templates from the registries.
- The catalog answer marks each app available or not on the host's platform.
- The App Store shows "Not available on riscv64" and disables Install.
- `POST /api/v1/apps/install` answers 409 before any pull. It may confirm with `has_platform`, which writes nothing to the disk.
- The weekly audit workflow (ADR-0218) compares the recorded platforms with the registries.
- Backend tests and an E2E spec.
- Not recommended: building riscv64 images of third-party apps (a pipeline per app, with .NET and Node.js apps blocked upstream), or running amd64 images under QEMU emulation on the board (slow on the K1, and a host change).

**Size: M.** Three to five days.

**Risks:** high for the product. A riscv64 UNOS is a NAS with six apps until publishers add riscv64. The list to watch is Tailscale, Vaultwarden, Mosquitto, AdGuard Home, Uptime Kuma, Nginx Proxy Manager, CrowdSec, Ollama, PhotoPrism, Grafana and TriliumNext, which have riscv64 requests or merged builds.

## 7. CI and releases

**Facts.**
- **Runners and QEMU.**
  - GitHub's hosted Linux runners are x64 and arm64 only ([runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)). The arm64 standard runners have served private repositories since 2026-01-29 ([changelog](https://github.blog/changelog/2026-01-29-arm64-standard-runners-are-now-available-in-private-repositories/) (snippet)).
  - `ubuntu-latest` has 2 vCPU and 8 GB in private repositories, and 4 vCPU and 16 GB in public ones ([runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)). A hosted job may run for up to 6 hours ([limits](https://docs.github.com/en/actions/reference/limits)).
  - `docker/setup-qemu-action` uses `tonistiigi/binfmt`, which supports riscv64; its latest release is QEMU 10.2.3 ([setup-qemu-action](https://github.com/docker/setup-qemu-action), [binfmt releases](https://github.com/tonistiigi/binfmt/releases)).
  - Known QEMU user-mode problems: GCC's `cc1` segfaults under qemu-riscv64 ([QEMU #3094](https://gitlab.com/qemu-project/qemu/-/issues/3094)). User mode implements no ptrace, does not support namespace flags in `clone`, and translates every system call to the host kernel ([QEMU docs](https://www.qemu.org/docs/master/user/main.html)).
- **Measured build times.**
  - Building RISE's manylinux riscv64 images under qemu-user took about 3 hours ([discuss.python.org](https://discuss.python.org/t/packaging-support-for-riscv64/58475)).
  - A community riscv64 Home Assistant image splits about 180 source builds into parallel jobs to stay under the 6-hour limit ([Home-Assistant.RISC-V](https://github.com/12345qwert123456/Home-Assistant.RISC-V)).
  - Slowdowns published for compile-heavy work under QEMU are about 5-10x. For example, an aarch64 pydantic build went from 9 to 69 minutes ([setup-qemu-action#22](https://github.com/docker/setup-qemu-action/issues/22)).
- **RISE runners** went into Early Availability on 2026-03-24 ([announcement](https://riseproject.dev/2026/03/24/announcing-the-rise-risc-v-runners-free-native-risc-v-ci-on-github/), [FAQ](https://ci.riseproject.dev/docs/faq.html), [six weeks in](https://riseproject.dev/2026/05/12/rise-risc-v-runners-six-weeks-in/)).
  - Terms: native and free for open-source projects, selected with `runs-on: ubuntu-24.04-riscv`. None of these pages says whether private repositories are served.
  - Hardware: Scaleway EM-RV1 boards with 4 cores and a 5.10 vendor kernel, one job per board, with thin capacity. Docker and buildx work.
  - Other offers: Cloud-V runs a similar service, with no published terms ([Cloud-V](https://cloud-v.co/github-riscv-runner)). Scaleway rents an EM-RV1 for EUR 15.99 a month, with 100 Mbit/s Ethernet, eMMC storage and no SLA ([Scaleway Labs](https://labs.scaleway.com/en/em-rv1/)).
- **Building and testing without emulation.**
  - cibuildwheel supports riscv64 since 3.1.0, under QEMU on x86 ([options](https://cibuildwheel.pypa.io/en/stable/options/)).
  - The pyca maintainers do not want cross-compiled builds of their packages ([pyca/infra#749](https://github.com/pyca/infra/pull/749)).
  - buildx can join a native riscv64 node to a builder (`docker buildx create --append --platform`) ([Docker docs](https://docs.docker.com/build/building/multi-platform/)).
  - `uraimo/run-on-arch-action` runs riscv64 with Ubuntu or Alpine edge images, or a custom `base_image` ([README](https://github.com/uraimo/run-on-arch-action)).
- **In the UNOS tree.**
  - There is no release workflow. The `build` job of `.github/workflows/test.yml` runs `docker compose build` on `ubuntu-latest`, on pushes to main only: amd64 only, without QEMU.
  - ADR-0050 (proposed) plans amd64 and arm64 release images on GHCR (`deploy.release-images`).

**Work for UNOS (after `deploy.release-images`).**
- The release workflow builds per platform:
  - amd64 on `ubuntu-latest`;
  - arm64 on `ubuntu-24.04-arm`;
  - riscv64 on `ubuntu-latest`, with `setup-qemu-action` limited to riscv64 and a pinned binfmt image.
- It pushes each image by digest and merges the multi-platform list.
- The supervisor's builder stage is cached (`cache-to: type=registry, mode=max`), so only a change of `requirements.txt` pays the compile. The riscv64 web UI image costs seconds.
- A scheduled job runs the backend suite (pytest with `requirements-dev.txt`) in a linux/riscv64 container built from the supervisor image, with `docker run --platform linux/riscv64`. Under qemu-user the Linux-feature tests exercise the runner's x86_64 kernel, not a riscv64 one.
- Every riscv64 build runs a smoke check: import every compiled module, round-trip bcrypt and cryptography, and start uvicorn.
- If the repository becomes public, the riscv64 job moves to RISE runners.

**Size: M**, two to four days on top of `deploy.release-images`. Estimates:
- a cold riscv64 supervisor build under QEMU on a 2-vCPU private runner takes one to three hours, and a warm one 5-15 minutes;
- the riscv64 web UI image takes under two minutes;
- pytest under QEMU takes 20-90 minutes;
- on RISE, a cold build takes 20-45 minutes.

**Risks:** medium.
- QEMU builds are flaky (segfaults, stalls) and slow.
- The 6-hour job limit.
- QEMU minutes count against the private repository's quota, so riscv64 builds run on tags or nightly, not on every pull request.
- RISE's terms for private repositories are unknown.

## 8. Code assumptions in UNOS

**Facts** (the tree at 9a28743). Searched with `git grep -n -i -E "arm64|aarch64|amd64|x86_64|armv7|armhf|riscv|rk3588|rockchip|uname -m|platform.machine|dpkg --print-architecture|--platform|TARGETARCH"`, and for sensor, thermal, cpufreq, GPU-device and disk-name handling.

| Where | What it assumes | On a riscv64 host |
| --- | --- | --- |
| `deploy/install.sh:66-95` (`detect_system`) | `uname -m` is x86_64, aarch64 or armv7l; anything else exits with "Unsupported architecture" | The installer stops before it changes anything |
| `deploy/install.sh:146-174` (`install_docker`) | Docker's apt repository serves `$(dpkg --print-architecture)` | No packages for riscv64 (area 3) |
| `deploy/install.sh:356`, `deploy/update.sh:395-399` | Both images are built on the host (`docker compose build`) | The web UI's builder stage has no riscv64 image, so a riscv64 install needs release images (areas 1 and 3) |
| `deploy/install.sh:7`, `docs/INSTALL.md:39-43`, `README.md:22-23`, `:145`, `:155-157` | AMD64 and ARM64 (and ARMv7 in INSTALL.md) are the supported architectures | Docs only |
| `supervisor/Dockerfile:1-17`, `supervisor/Dockerfile.test:1-18` | `python:3.11-slim` with its stock pip; apt installs `gcc` and `python3-dev`, but no g++, Rust, libffi-dev or libssl-dev | pip ignores riscv64 wheels, and six packages need toolchains (area 2) |
| `webui/Dockerfile:4`, `:15` | Builder `FROM node:22-alpine` without `--platform`; final stage `caddy:2-alpine` | The builder has no riscv64 image (area 1) |
| `deploy/uninstall.sh:82-85`, `:348` | Runs `alpine`; the purge hint names `docker-ce` | `alpine` works; the hint is wrong |
| `webui/Dockerfile.test:5` | The Playwright image | Test stack only, run on the workstation or in CI |
| `.github/workflows/test.yml` (job `build`) | `docker compose build` on `ubuntu-latest` | No riscv64 (or arm64) image is ever built |
| `supervisor/src/apps/schemas.py:81-101`, `supervisor/src/apps/router.py:55`, `:643-645` | No platform field; every app is offered; a refused pull is a 500 | 64 of 70 apps fail at install (area 6) |
| `supervisor/src/system/router.py:72` | `architecture=platform.machine()` | Reports `riscv64`: works |
| `supervisor/src/system/router.py:85`, `:373`, `supervisor/src/websocket/events.py:204` | Whatever sensors psutil finds | Works; no sensor name is hard-coded (area 4) |
| `supervisor/src/storage/disks.py:31-39` | Hides `mmcblk*`, `mtdblock*`, `zram*`, `ram*`, `loop*`, `sr*` and `fd*` from pools; system disks are judged by mountpoint | The K1's eMMC (with its boot0, boot1 and rpmb devices) and SPI flash stay hidden: works. Loop devices are hidden too, which matters for test VMs (area 9) |
| `scripts/dev/linux-tests.sh:57`, `:108`; `scripts/dev/tree.py:449-462`, `:585` | Reads the device's `uname -m`, default `aarch64`; bundles only the pure-Python files of the test dependencies | Works on a riscv64 board; the stale-pin warning needs marker evaluation (area 2) |
| `scripts/dev/env.sh:48-58`, `:166-167` | One `dev.env`; `UNOS_DEV_ENV_FILE` names another; per-device `UNOS_DEV_SYSTEM_DISK` and lock | A second board is a second env file (area 9) |
| `.claude/hooks/guard.py:73-76`, `:464-469`; `.claude/settings.json` | Secrets are recognised by the names `dev.env` and `tmp/dev/backup` | A second board's files must keep those names |
| `supervisor/tests/test_deploy_scripts.py:771-800` | `install.sh` is tested with `uname` printing aarch64 and `dpkg` printing arm64 | No riscv64 case |
| `supervisor/tests/test_deploy_scripts.py:2159` | The builder line is exactly `FROM node:<n>-alpine AS builder` | Must allow `--platform=$BUILDPLATFORM` |

- No code in `supervisor/src` or `webui/src` names the RK3588, Rockchip, Mali, MPP, RGA, an NPU or `/dev/dri`. No code reads `/proc/cpuinfo` fields or cpufreq.
- The RK3588 appears only in text: ADR-0014's Phase 2 plan for "the RK3588's hardware acceleration", and the start-up timing in ADR-0171 and `CHANGELOG.md:476`.

**Work for UNOS.** Covered by areas 1, 2, 3, 6 and 9. Nothing else is architecture-specific.

**Size: S** beyond those areas. **Risks:** low.

## 9. Testing without and with the board

**Facts.**
- **Debian's riscv64 images.** Debian publishes riscv64 `generic` and `nocloud` qcow2 images (415 MB and 388 MB, built 2026-11-03) ([cloud.debian.org](https://cloud.debian.org/images/cloud/trixie/latest/)). Its wiki gives a QEMU command line with OpenSBI and U-Boot ([Debian wiki](https://wiki.debian.org/RISC-V)).
- **Lima** runs riscv64 guests as an experimental architecture. It warns that a foreign-architecture VM is "extremely slow" and does not install containerd on such guests ([Lima multi-arch](https://lima-vm.io/docs/config/multi-arch/), [templates](https://lima-vm.io/docs/templates)).
- **QEMU's `virt` machine** offers virtio-mmio slots and a PCIe host bridge for extra disks ([QEMU docs](https://www.qemu.org/docs/master/system/riscv/virt.html)).
- **Not found:** a benchmark of riscv64 emulation on Apple Silicon, or a guide to Docker with btrfs in such a VM.
- **In the UNOS tree:**
  - `supervisor/src/storage/disks.py:36` hides `loop*` devices from pools.
  - `scripts/dev/linux-tests.sh:32-33` defaults to a 2400 s timeout and a 3G memory limit (`MemoryMax`, `:64`), and `:70` mounts a 3g tmpfs in its chroot.
  - `deploy/docker-compose.yml` caps the supervisor's `/tmp` tmpfs at 2g (ADR-0171; ADR-0177 proposes sizing it for small boards).
  - The device rules are ADR-0214 (writes measured, builds off the device), ADR-0183 (checks start no container), ADR-0187 (Linux-only tests in RAM) and ADR-0210 (the device lock `/run/unos-dev.lock`, held on the device). `AGENTS.md` says UNOS has one dev device (ADR-0207).

**Work for UNOS without the board.**
- **CI.** The riscv64 smoke check and the scheduled tests of area 7.
- **A VM on the workstation.** A script starts a Debian 13 riscv64 VM from Debian's riscv64 cloud image: `nocloud` with plain QEMU, or Lima with `--arch=riscv64`, whose Debian 13 template uses the `generic` image ([Lima template](https://raw.githubusercontent.com/lima-vm/lima/master/templates/_images/debian-13.yaml)).
  - Docker comes from Debian.
  - Pool disks are extra virtio disks (`vdb`, `vdc`, ...), not loop devices, which UNOS hides.
  - An emulated NVMe disk (`-device nvme`) exercises the SMART paths. That QEMU's NVMe answers SMART is assumed and unverified.
  - Installing QEMU or Lima on the workstation is a package install, so it is an owner action (ADR-0211, ADR-0217).
- **What the VM proves:**
  - Before release images exist: the host side, that is Debian's Docker 26.1 with `check-config.sh`, btrfs RAID1 and scrub on a real riscv64 kernel, and Samba.
  - Once riscv64 release images exist: `install.sh`'s riscv64 path and the catalog filter too. Building the supervisor inside the emulated VM would take hours.
- **What it does not prove:** the K1's drivers, its power use or its AES speed.
- **Speed (estimate):** a vCPU at 0.3 to 1 times a K1 core, and one to five minutes to boot. Keep one VM with snapshots rather than booting a fresh one for every run.

**Work for UNOS with the board, as a second dev device.**
- **Deploys.** The owner prepares the host once: packages, Docker from the distribution, and Docker's data root on NVMe. After that, `scripts/dev/deploy.sh` deploys UNOS as it does to the first device:
  - `sync` copies the runtime files;
  - `build-supervisor` builds the supervisor on the board;
  - `build-webui` builds the static files on the workstation and only the Caddy stage on the board (ADR-0184);
  - `up` starts the stack with `--no-build`.

  This is the first experiment, and it needs no release images.
- **Its own `dev.env`.** `UNOS_DEV_ENV_FILE` names a file called `dev.env` in a folder of its own, so the guard and the deny rules of `.claude/settings.json` apply to it. Its `UNOS_DEV_STATE_DIR` ends in `tmp/dev`, so that its backups land in a `tmp/dev/backup` that the guard and the rule `Read(//**/tmp/dev/backup/**)` protect.
- **The system disk.** `UNOS_DEV_SYSTEM_DISK` names the eMMC (`mmcblk2` on the BPI-F3 in the forum log), even when the root filesystem is on NVMe, because the eMMC is the part that cannot be replaced.
- **Test settings.** Set `UNOS_DEV_TEST_MEMORY` and `UNOS_DEV_TEST_TIMEOUT` for the board. A 4 GB board cannot hold a 3G test cgroup next to the RAM rootfs. The X60 cores, by SpacemiT's own claim about 1.3 times a Cortex-A55, are slower than the RK3588's Cortex-A76 cores (judgment), so runs take longer.
- **ADR-0214 applies to the eMMC:**
  - every step reports its writes;
  - Docker's data root, the image layers and the build cache are on NVMe or the SD card;
  - journald is volatile, and there is no swap on the eMMC (zram instead);
  - the EXT_CSD life-time fields are read before and after the bring-up.

  The board's lock is its own, so deploys to the two devices do not wait for each other.

**Size: M.** Two to three days for the VM script, and one day for the second-device configuration and its docs. **Risks:** medium. The VM is slow; Lima's riscv64 support is experimental; and a second device adds a second write budget to track.

## Proposed feature-list.json entries

To be added by the lead once the owner has decided. They are in the ledger's format (`supervisor/tests/test_feature_list.py`), each id's category is an ADR area, and they are listed in the recommended order. `deploy.riscv64-release-images` and everything after it need `deploy.release-images` first. The new ADR's number goes into `adr` once it is assigned.

```json
[
  {
    "id": "deploy.riscv64-supervisor-image",
    "category": "deploy",
    "description": "The supervisor and web UI images build for linux/riscv64, and the final images carry no compiler or Rust toolchain",
    "steps": [
      "supervisor/Dockerfile starts FROM python:3.11-slim-trixie and installs a pinned pip of 24.1 or later before the requirements",
      "A builder stage compiles the packages without a riscv64 wheel (cryptography, cffi, bcrypt, psutil) into wheels, with the Rust toolchain installed only for riscv64, and the final stage installs only wheels",
      "requirements.txt pins uvicorn without the standard extra, with httptools and websockets, and uvloop only where platform_machine is not riscv64",
      "webui/Dockerfile runs its node builder stage on the build platform, so the linux/riscv64 web UI image runs no Node.js",
      "docker buildx build --platform linux/riscv64 of both images passes, and the supervisor image imports every compiled module"
    ],
    "passes": false,
    "adr": [
      "ADR-0050",
      "ADR-0214"
    ]
  },
  {
    "id": "device.riscv64-dev-board",
    "category": "device",
    "description": "The SpacemiT K1 board is a second dev device whose eMMC writes are measured like the first device's",
    "steps": [
      "scripts/dev/ reach the board through UNOS_DEV_ENV_FILE naming its own dev.env, with a state folder that ends in tmp/dev",
      "scripts/dev/deploy.sh deploys UNOS to the board: the web UI's static files are built on the workstation, and the supervisor is built on the board with Docker's data root off the eMMC",
      "check.sh on the board reports its eMMC writes since boot and the eMMC's life-time estimate",
      "Docker's data root, image layers and scratch data are on NVMe or another replaceable disk, not on the eMMC",
      "linux-tests.sh runs the Linux-only backend tests in the board's RAM, and they pass"
    ],
    "passes": false,
    "adr": [
      "ADR-0184",
      "ADR-0210",
      "ADR-0214"
    ]
  },
  {
    "id": "apps.catalog-platforms",
    "category": "apps",
    "description": "The App Store shows which apps have an image for the host's platform and refuses the others before any pull",
    "steps": [
      "Every catalog template lists the platforms its image publishes, and a test fails for a template without them",
      "On a host whose platform an app's image lacks, Apps marks the app as not available for that platform and disables Install",
      "POST /api/v1/apps/install answers 409 for such an app without pulling an image",
      "The weekly audit workflow compares the recorded platforms with the registries and fails when they differ"
    ],
    "passes": false,
    "adr": [
      "ADR-0218"
    ]
  },
  {
    "id": "deploy.riscv64-release-images",
    "category": "deploy",
    "description": "A release publishes linux/riscv64 images of the supervisor and the web UI next to amd64 and arm64",
    "steps": [
      "The release workflow of deploy.release-images also builds linux/riscv64, under QEMU with a pinned binfmt image or on a native riscv64 runner",
      "The riscv64 wheels of the supervisor's compiled packages are built once per change of requirements.txt and reused from a cache",
      "A riscv64 host pulls both images of a tag, and update.sh updates it without building"
    ],
    "passes": false,
    "adr": [
      "ADR-0050"
    ]
  },
  {
    "id": "ci.riscv64-backend-tests",
    "category": "ci",
    "description": "The backend tests run on linux/riscv64 in CI on a schedule",
    "steps": [
      "A scheduled workflow builds a test image from the riscv64 supervisor image with supervisor/tests and requirements-dev.txt, and runs the backend tests in it, under QEMU or on a native riscv64 runner",
      "A failure fails the run and names the test",
      "Every riscv64 image build runs a smoke check that imports the compiled modules, round-trips bcrypt and cryptography and starts uvicorn"
    ],
    "passes": false,
    "adr": [
      "ADR-0190",
      "ADR-0216"
    ]
  },
  {
    "id": "deploy.riscv64-install",
    "category": "deploy",
    "description": "install.sh installs UNOS on a riscv64 host that runs Debian 13 or Ubuntu 24.04",
    "steps": [
      "install.sh accepts uname -m riscv64",
      "Where Docker's repository has no packages for the host's architecture, install.sh installs docker.io with docker-compose (Debian 13) or docker-compose-v2 (Ubuntu 24.04) from the distribution",
      "On riscv64, install.sh pulls the supervisor and web UI images of the release instead of building them",
      "supervisor/tests/test_deploy_scripts.py runs install.sh with stubs that report riscv64 on Debian 13 and on Ubuntu 24.04",
      "uninstall.sh names the distribution's Docker packages on such hosts",
      "docs/INSTALL.md and README.md list riscv64, the tested board and its limits"
    ],
    "passes": false,
    "adr": [
      "ADR-0049",
      "ADR-0050"
    ]
  },
  {
    "id": "testing.riscv64-vm",
    "category": "testing",
    "description": "A riscv64 Debian 13 VM on the workstation runs the host-level flows without the board",
    "steps": [
      "A script starts a QEMU riscv64 VM from Debian's riscv64 image with extra virtio disks and an emulated NVMe disk",
      "With the riscv64 release images, install.sh, a btrfs RAID1 pool on the extra disks, an SMB share and the install of an app with a riscv64 image pass in the VM",
      "The App Store in the VM marks the apps without riscv64 images as not available"
    ],
    "passes": false,
    "adr": [
      "ADR-0016"
    ]
  }
]
```

## Proposed ADR

For `docs/adr/` once the owner has decided; the lead assigns the number and adds the README row. It is written as a proposal by an agent, like the other proposed ADRs. If the owner accepts it, the lead sets `status: accepted`, `decided-by: owner` and the date of the decision (the adr skill); if the owner changes the scope, the text changes with it before its first commit.

```markdown
---
status: proposed
date: 2026-11-07
area: deploy
decided-by: agent
---

# ADR-NNNN: UNOS runs on riscv64 hosts from prebuilt images, with the distribution's Docker and a catalog that knows each app's platforms

## Context

The owner asked what supporting riscv64 hosts would cost, with a SpacemiT K1 board (most likely a Banana Pi BPI-F3) in mind; `docs/research/riscv64-support-2026-11-07.md` estimates three to five weeks of work. The base images and host packages exist for riscv64, but Docker Inc. publishes no riscv64 Engine packages, only 6 of the 70 catalog images publish linux/riscv64, and the supervisor needs pip 24.1 or later plus source builds of cryptography, cffi, bcrypt and psutil. The K1 implements RVA22, so of the systems UNOS targets it runs Debian 13 and Ubuntu 24.04, not Ubuntu 25.10 or later. Building on the board compiles Rust and C code on hardware whose eMMC must be spared (ADR-0214), and `install.sh` cannot build the web UI image on riscv64 at all, because no `node` image exists for it.

## Decision

Proposed:

- UNOS supports linux/riscv64 hosts as a third architecture, marked experimental until the K1 board passes the checks of ADR-0018. The supported riscv64 systems are Debian 13 and Ubuntu 24.04, Armbian's images of them included.
- On riscv64, `deploy/install.sh` installs Docker Engine and Compose v2 from the distribution, because Docker's repository has none for riscv64: `docker.io` with `docker-compose` on Debian 13, or with `docker-compose-v2` on Ubuntu 24.04. A Compose older than 2.30 keeps the existing warning of `install.sh`.
- End users' riscv64 hosts pull the release images; on riscv64, `install.sh` and `update.sh` never build. The release workflow of ADR-0050 adds linux/riscv64, and the web UI's builder stage runs on the build platform.
- The supervisor image installs pip 24.1 or later. It compiles the packages without riscv64 wheels in a cached builder stage, which installs the Rust toolchain only for riscv64, so the final image carries no compiler. uvloop is left out on riscv64.
- The app catalog records the platforms of each image. On a host whose platform an app lacks, the App Store marks the app as not available, and `POST /api/v1/apps/install` refuses it before any pull. UNOS neither builds nor hosts images of third-party apps, and it does not emulate other architectures.
- The K1 board joins as a second dev device. `scripts/dev/` reach it with its own `dev.env` and state folder, and `scripts/dev/deploy.sh` deploys to it as to the first device: the web UI's static files are built on the workstation (ADR-0184), the supervisor on the board. The system-disk rules of ADR-0214 apply to its eMMC: Docker's data root, image builds and scratch data stay off it.

## Consequences

- The work is the ledger entries deploy.riscv64-supervisor-image, device.riscv64-dev-board, apps.catalog-platforms, deploy.riscv64-release-images, ci.riscv64-backend-tests, deploy.riscv64-install and testing.riscv64-vm. The last four come after deploy.release-images.
- Every release builds a third platform. A cold riscv64 build under QEMU takes hours, and a warm one minutes. Until cryptography and bcrypt publish riscv64 wheels, each of their security releases needs a riscv64 rebuild by UNOS.
- Debian 13 hosts run Docker Engine 26.1 and Compose 2.26, older than on the other platforms, with Debian's limited security support for Go packages.
- On riscv64 the App Store offers only the apps whose publishers ship riscv64 images: six of 70 on 2026-11-07.
- UNOS has two dev devices. Each has its own lock (ADR-0210) and its own write budget (ADR-0214), and agents name the device they act on.

## Considered options

- Building the images on end users' riscv64 hosts, as `install.sh` does on amd64 and arm64: the web UI's builder has no riscv64 `node` image, and the supervisor's source builds take about 15 to 25 minutes on a K1 (estimate) and write the toolchains to the host's disk.
- Docker from a community riscv64 repository: it rests on one maintainer's signing key and builds.
- Running amd64 app images under QEMU emulation on the host: slow on the K1, and a change of the host.
- Building riscv64 images of third-party apps: a pipeline per app, and the .NET and Node.js apps are blocked upstream.
- Not supporting riscv64: no cost, and the K1 board stays unused for UNOS.
```

## Follow-ups found on the way (not riscv64)

Independent of riscv64, and outside this branch:
- **Catalog entries that are broken, deprecated or archived on every architecture.**
  - `lscr.io/linuxserver/readarr:develop` has an empty image index, so its install fails everywhere. LinuxServer deprecated it on 2025-06-27.
  - `linuxserver/overseerr` is deprecated in favour of Seerr.
  - `ghcr.io/ajnart/homarr` was archived on 2026-06-03.
  - `zadam/trilium` now lives on as TriliumNext.
  - `frooodle/s-pdf` is published as `stirlingtools/stirling-pdf`.
  - `traefik:v3.0` was last pushed on 2024-07-03; `traefik:v3` is current.
  - `p3terx/aria2-pro` has not changed since 2022-09-06.
  - `localai/localai:latest-aio-cpu` has not changed since 2026-02-21.
  - The filebrowser repository is archived.
- **psutil.** The pinned psutil 5.9.8 has no Linux aarch64 wheel, so the arm64 image compiles it. psutil 7.x has aarch64 wheels.

## Sources

The main sources, all read on 2026-11-07. Every other source is linked where the report uses it, and was read on the same day.

Base images and Node.js:
- docker-library definition files: [python](https://raw.githubusercontent.com/docker-library/official-images/master/library/python), [caddy](https://raw.githubusercontent.com/docker-library/official-images/master/library/caddy), [node](https://raw.githubusercontent.com/docker-library/official-images/master/library/node), [alpine](https://raw.githubusercontent.com/docker-library/official-images/master/library/alpine), [debian](https://raw.githubusercontent.com/docker-library/official-images/master/library/debian), [ubuntu](https://raw.githubusercontent.com/docker-library/official-images/master/library/ubuntu), checked 2026-11-07
- Docker Hub tag API: [python:3.11-slim](https://hub.docker.com/v2/repositories/library/python/tags/3.11-slim), [caddy:2-alpine](https://hub.docker.com/v2/repositories/library/caddy/tags/2-alpine), [node:22-alpine](https://hub.docker.com/v2/repositories/library/node/tags/22-alpine), [rust:slim](https://hub.docker.com/v2/namespaces/library/repositories/rust/tags/slim), checked 2026-11-07
- [python 3.11 slim-trixie Dockerfile](https://raw.githubusercontent.com/docker-library/python/master/3.11/slim-trixie/Dockerfile), [cpython 3.11.18 ensurepip](https://raw.githubusercontent.com/python/cpython/v3.11.18/Lib/ensurepip/__init__.py), checked 2026-11-07
- [Node.js BUILDING.md](https://raw.githubusercontent.com/nodejs/node/main/BUILDING.md), [unofficial builds index](https://unofficial-builds.nodejs.org/download/release/index.json), [Alpine nodejs riscv64](https://pkgs.alpinelinux.org/packages?name=nodejs*&branch=v3.24&repo=&arch=riscv64), checked 2026-11-07

Python packages:
- PyPI JSON API for each pin, linked in area 2, checked 2026-11-07
- [pip 24.0 `_manylinux.py`](https://raw.githubusercontent.com/pypa/pip/24.0/src/pip/_vendor/packaging/_manylinux.py), [pip 24.1 `_manylinux.py`](https://raw.githubusercontent.com/pypa/pip/24.1/src/pip/_vendor/packaging/_manylinux.py), checked 2026-11-07
- [pypa/manylinux](https://github.com/pypa/manylinux), [pypi/warehouse#18243](https://github.com/pypi/warehouse/issues/18243), [cibuildwheel changelog](https://cibuildwheel.pypa.io/en/stable/changelog/), checked 2026-11-07
- [pyca/cryptography#14460](https://github.com/pyca/cryptography/issues/14460), [pyca/infra#749](https://github.com/pyca/infra/pull/749), [cffi#227](https://github.com/python-cffi/cffi/pull/227), [cffi#233](https://github.com/python-cffi/cffi/issues/233), [uvloop#732](https://github.com/MagicStack/uvloop/issues/732), checked 2026-11-07
- [cryptography installation](https://cryptography.io/en/latest/installation/), [rustc platform support](https://doc.rust-lang.org/nightly/rustc/platform-support.html), [Debian rustc for riscv64](https://qa.debian.org/madison.php?package=rustc&table=debian&a=riscv64), checked 2026-11-07
- [RISE Python wheels](https://pypi.riseproject.dev/), [RISE manylinux notes](https://pypi.riseproject.dev/manylinux.html), checked 2026-11-07
- [uvicorn on PyPI](https://pypi.org/pypi/uvicorn/0.54.0/json), [uvicorn installation docs](https://raw.githubusercontent.com/Kludex/uvicorn/main/docs/installation.md), [Docker build cache docs](https://docs.docker.com/build/cache/optimize/), checked 2026-11-07

Docker and host packages:
- [download.docker.com trixie Release](https://download.docker.com/linux/debian/dists/trixie/Release), [noble Release](https://download.docker.com/linux/ubuntu/dists/noble/Release), [static binaries](https://download.docker.com/linux/static/stable/), [Engine 29 release notes](https://docs.docker.com/engine/release-notes/29/), [docker/cli#6857](https://github.com/docker/cli/issues/6857), checked 2026-11-07
- [Debian madison: Docker](https://qa.debian.org/madison.php?package=docker.io+docker-cli+docker-compose+docker-buildx+containerd+runc+tini+nodejs+npm&s=bookworm-backports,trixie,trixie-backports,forky,sid&text=on), [Debian madison: host packages](https://qa.debian.org/madison.php?package=curl+wget+git+ca-certificates+gnupg+lsb-release+btrfs-progs+smartmontools+lm-sensors+samba+samba-common-bin+hdparm+parted+mdadm+nvme-cli+ethtool+net-tools+iproute2+util-linux+mount+e2fsprogs+udev+systemd+passwd+libc-bin&s=trixie&text=on), [Ubuntu madison: Docker](https://ubuntu-archive-team.ubuntu.com/madison.cgi?package=docker.io+docker-compose-v2+docker-buildx+containerd+runc&a=riscv64&text=on), [Ubuntu madison: host packages](https://ubuntu-archive-team.ubuntu.com/madison.cgi?package=curl+wget+git+ca-certificates+gnupg+lsb-release+btrfs-progs+smartmontools+lm-sensors+samba+samba-common-bin+hdparm+parted+mdadm+nvme-cli+ethtool+net-tools+iproute2+util-linux+mount+e2fsprogs+udev+systemd+passwd+libc-bin+nodejs&s=noble,noble-updates,noble-security&a=riscv64&text=on), checked 2026-11-07
- [Debian 13 release notes: what's new](https://www.debian.org/releases/trixie/release-notes/whats-new.en.html), [issues](https://www.debian.org/releases/trixie/release-notes/issues.en.html), [Ubuntu 25.10 release notes](https://documentation.ubuntu.com/release-notes/25.10/), checked 2026-11-07
- [docker/compose v5.7.0 assets](https://github.com/docker/compose/releases/expanded_assets/v5.7.0), [gounthar/docker-for-riscv64](https://github.com/gounthar/docker-for-riscv64/blob/main/README.md), checked 2026-11-07

The K1 platform:
- [SpacemiT upstream status](https://github.com/spacemit-com/linux/wiki), [kernel.org](https://www.kernel.org/), [SpacemiT forum on Ubuntu versions](https://forum.spacemit.com/t/topic/939), [Bianbu images](https://archive.spacemit.com/image/k1/version/bianbu/), [SpacemiT Debian images](https://archive.spacemit.com/image/k1/version/debian/), [CNX MUSE Book review (Bianbu 2.3, kernel 6.6.63)](https://www.cnx-software.com/2026/01/08/muse-book-laptop-review-testing-an-octa-core-risc-v-linux-laptop-in-2026/), [ITHome on the K1](https://www.ithome.com/0/765/223.htm), checked 2026-11-07
- [Armbian BPI-F3](https://www.armbian.com/bananapi-f3/), [Armbian spacemit.conf](https://raw.githubusercontent.com/armbian/build/main/config/sources/families/spacemit.conf), [Armbian current kernel config](https://raw.githubusercontent.com/armbian/build/main/config/kernel/linux-spacemit-current.config), [Bianbu k1_defconfig](https://gitee.com/bianbu-linux/linux-6.6/raw/k1-bl-v2.2.y/arch/riscv/configs/k1_defconfig), checked 2026-11-07
- [Banana Pi BPI-F3 wiki](https://wiki.banana-pi.org/Banana_Pi_BPI-F3), [CNX on the BPI-F3](https://www.cnx-software.com/2024/05/10/banana-pi-bpi-f3-sbc-spacemit-k1-octa-core-risc-v-ai-soc/), [CNX Milk-V Jupiter review](https://www.cnx-software.com/2024/08/11/milk-v-jupiter-review-risc-v-mini-itx-motherboard-and-pc-ubuntu-based-bianbu-os/), [Geerling sbc-reviews #47](https://github.com/geerlingguy/sbc-reviews/issues/47), [Milk-V Jupiter](https://milkv.io/docs/jupiter/overview), [Bit-Brick K1 brief](https://bit-brick-docs.pages.dev/docs/k1/hardware/processor-brief), checked 2026-11-07
- BPI forum threads: [eMMC log](https://forum.banana-pi.org/t/bpi-f3-running-ubuntu-24-04/18478), [NVMe with Debian](https://forum.banana-pi.org/t/bpi-f3-using-nvme-debian/26278), [NAS and USB disks](https://forum.banana-pi.org/t/spacemit-k1-8-core-risc-v-chip-for-nas-design-with-openmediavault/17969), [root on NVMe](https://forum.banana-pi.org/t/how-to-boot-through-nvme/18316), [boot switch](https://forum.banana-pi.org/t/boot-selection-switch/18112), checked 2026-11-07
- [Samsung eMMC 5.1 datasheet (copy)](https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/791/Samsung-eMMC_2D00_2611204514.pdf), [Milk-V community: RVA22 compliance](https://community.milkv.io/t/spacemit-k1-m1-is-not-quite-rva22-compliant/2870), [linux-pci thread on the Jupiter hang](https://ratatoskr.run/linux-pci/2026/08/17456851/t), checked 2026-11-07

The app catalog:
- Docker Hub tag API per image (`https://hub.docker.com/v2/repositories/<namespace>/<repository>/tags/<tag>`), [explore.ggcr.dev](https://explore.ggcr.dev/) per GHCR reference, [LinuxServer fleet API](https://api.linuxserver.io/api/v1/images?include_config=false&include_deprecated=true), checked 2026-11-07
- [LinuxServer armhf notice](https://info.linuxserver.io/issues/2023-07-01-armhf), [docker-sabnzbd commits](https://github.com/linuxserver/docker-sabnzbd/commits/master/jenkins-vars.yml), [Home Assistant discussion #1107](https://github.com/home-assistant/architecture/discussions/1107), [.NET 10 supported OS](https://github.com/dotnet/core/blob/main/release-notes/10.0/supported-os.md), checked 2026-11-07

CI and emulation:
- [GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners), [Actions limits](https://docs.github.com/en/actions/reference/limits), [setup-qemu-action](https://github.com/docker/setup-qemu-action), [binfmt releases](https://github.com/tonistiigi/binfmt/releases), [QEMU user mode](https://www.qemu.org/docs/master/user/main.html), [QEMU #3094](https://gitlab.com/qemu-project/qemu/-/issues/3094), [setup-qemu-action#22](https://github.com/docker/setup-qemu-action/issues/22), checked 2026-11-07
- [RISE runners announcement](https://riseproject.dev/2026/03/24/announcing-the-rise-risc-v-runners-free-native-risc-v-ci-on-github/), [RISE FAQ](https://ci.riseproject.dev/docs/faq.html), [RISE six weeks in](https://riseproject.dev/2026/05/12/rise-risc-v-runners-six-weeks-in/), [Cloud-V runners](https://cloud-v.co/github-riscv-runner), [Scaleway Labs EM-RV1](https://labs.scaleway.com/en/em-rv1/), [discuss.python.org on riscv64 packaging](https://discuss.python.org/t/packaging-support-for-riscv64/58475), [Home-Assistant.RISC-V](https://github.com/12345qwert123456/Home-Assistant.RISC-V), checked 2026-11-07
- [Docker multi-platform builds](https://docs.docker.com/build/building/multi-platform/), [cibuildwheel options](https://cibuildwheel.pypa.io/en/stable/options/), [run-on-arch-action](https://github.com/uraimo/run-on-arch-action), checked 2026-11-07
- [Debian cloud images](https://cloud.debian.org/images/cloud/trixie/latest/), [Debian wiki RISC-V](https://wiki.debian.org/RISC-V), [QEMU virt machine](https://www.qemu.org/docs/master/system/riscv/virt.html), [Lima multi-arch](https://lima-vm.io/docs/config/multi-arch/), [Lima templates](https://lima-vm.io/docs/templates), [ubuntu.com RISC-V downloads](https://ubuntu.com/download/risc-v), checked 2026-11-07
