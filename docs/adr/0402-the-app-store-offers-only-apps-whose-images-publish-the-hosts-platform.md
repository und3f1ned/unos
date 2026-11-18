---
status: accepted
date: 2026-11-18
area: apps
decided-by: agent
---

# ADR-0402: The App Store offers only apps whose images publish the host's platform, and the install refuses the others before any pull

## Context

The catalog is one list for every host, but an image is built for some platforms only. On the riscv64 board (`deploy.riscv64-bpi-f3`) 6 of the 70 images that the catalog then had publish a linux/riscv64 build (`docs/research/riscv64-support-2026-11-07.md`, area 6): the install of any other app failed at the pull with Docker's error, as a 500 after the checks. The owner delegated the choices, asking above all that it be safe.

## Decision

- **Recorded platforms.** `supervisor/src/apps/image_platforms.json` holds, for every image reference of the catalog (each template's, each companion's and those that a hardware profile substitutes, exactly as written, with the digest of a pinned one), the sorted Linux platforms of its registry's manifest list ("linux/amd64", "linux/arm64", "linux/riscv64", "linux/arm/v7"); `unknown/unknown` attestation entries and other systems are left out, and arm64's variant v8 is not named. `ContainerSpec.platforms` (a computed field of every template and companion) answers it. Tests fail for an image without a record and for a record that no image uses. A manifest list that names no Linux platform is recorded as none, which is true of one image, Readarr's tag (an empty index; the project is retired): no host can pull it, so it is not available anywhere, and a test lists the images that may publish nothing (the set may only shrink).
- **Availability.** `supervisor/src/apps/platforms.py`: the host's platform is the supervisor's `platform.machine()` (`aarch64` is linux/arm64, `x86_64` linux/amd64, `riscv64`, `armv7l`, `armv6l`, `i686` and the like); a machine it does not know names no platform. An image runs on a host when its list has the platform; an armv7 host also runs armv6 images, as Docker does, and any other variant counts only when it is recorded. An app is available when every one of its containers' own images runs on the host. An image with no record at all and a host without a name never refuse anything: they are left to Docker, and the first cannot occur for the catalog (a test). The hardware profiles' images are recorded and audited too: the catalog API checks the image the install pulls on this host (it names the profile's image where a profile applies), the request's 409 checks the containers' own images, and the install job checks again, with the profile's images, before its first pull and ends failed with 409.
- **API.** `GET /api/v1/apps/catalog` and `/catalog/{id}` add `host_platform` and `available` to each template. `POST /api/v1/apps/install` answers 409, after 404 and before the request's other checks, any Docker or pool read and any job, for an app that is not available: "<name> is not available for <platform>: <image> publishes <platforms>", naming each image that lacks it (a multi-container app is refused when any of its images lacks the platform). Nothing is pulled and nothing is written; no audit entry is written, since nothing changed.
- **App Store.** The card of an app that is not available shows "Not available for <platform>" and a disabled Install, and its details show the reason in a warning and a disabled Install button, in English and Russian. The Installed tab is unchanged.
- **The audit's tool.** `scripts/catalog-platforms.py` (`check`, `record`; `make catalog-platforms`) asks each image's registry for its manifest, anonymously, read-only and without a pull of a layer (the manifest of the pinned digest or tag; a single manifest's config when the tag is no list), and `check` fails when the recorded platforms differ from the registries' or an image cannot be read; `record` writes the file only when every image was read. An image that cannot be queried is an error, never a guess. The weekly workflow (ADR-0218) runs `check` in a job of its own, and the script runs by hand with the supervisor's Python, as CI is off (ADR-0230). Its test replays recorded registry answers (`supervisor/tests/fixtures/registry_manifests.json`).
- **Floating tags.** Most templates still use a floating tag (`UNPINNED_TEMPLATES`): their record is that of the tag on the day it was made, and the weekly check fails when a publisher adds or drops a platform, which is the signal to run `record` and review the diff.

## Consequences

- On a riscv64 host the App Store lists 61 of the 67 apps as not available instead of failing at their pull; on arm64 and amd64 nothing changes except the apps whose image lacks the platform.
- The record is a snapshot of third-party registries: it can be stale between weekly runs, and the install then falls back to Docker's own refusal.
- The weekly check reads one manifest per image (about 70 manifests); Docker Hub counts them against its anonymous pull limit.
- The host's platform is the kernel's machine: a 32-bit userland on an arm64 kernel would be taken for arm64.

## Considered options

- Ask Docker (`GET /distribution/{name}/json`) at install time: needs Docker and the network at every install and writes nothing, but cannot mark the App Store's cards before an install. It stays a possible second check.
- Fields written into `catalog.py` by hand: 70 lists that the audit would then have to rewrite in Python source.
