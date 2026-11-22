---
status: accepted
date: 2026-11-22
area: ci
decided-by: agent
---

# ADR-0461: Playwright is pinned exactly, to a release that downloads its browsers for the Ubuntu of ubuntu-latest

## Context

The first dispatched run of the bootstrap job (ADR-0451), Audit run 45038615417 of 2026-11-22,
failed in `make setup`: "Playwright does not support chromium on ubuntu26.04-x64", then
`setup.sh: installing Chromium and WebKit failed`. GitHub's ubuntu-latest is Ubuntu 26.04 now, and
`webui/package.json` asked for `^1.57.0`, which the lock held at 1.57.0 (2025-11-25): that release
names the builds of its browsers for Ubuntu up to 24.04 only, so `make setup` failed on every new
Ubuntu 26.04 machine too. Playwright moves by hand in the monthly pass (ADR-0231, ADR-0427), and
the lock had kept that release since the repository's first commit.

## Decision

- **1.65.1.** `@playwright/test` is 1.65.1 (published 2026-11-13, older than the 7 days of
  Dependabot's cooldown that the toolchain keeps, ADR-0449), pinned exactly in
  `webui/package.json` as the rest of the toolchain (ADR-0456); the lock holds `playwright` and
  `playwright-core` 1.65.1. The lock is written by the npm of `webui/Dockerfile`'s builder (Alpine
  3.22, npm 11.6.4), which refused a lock of the workstation's npm before, and `npm ci` passes with
  it there and on the workstation. `webui/Dockerfile.test` takes
  `mcr.microsoft.com/playwright:v1.65.1-noble` with the digest that tag names (ADR-0305). Its
  browsers: Chromium and its headless shell 159.0.8154.6 (build 1255), WebKit 27.2 (build 2388).
- **The rule.** The pinned release must download its browsers for the Ubuntu of ubuntu-latest.
  `UBUNTU_LATEST` (26.04) and `FIRST_PLAYWRIGHT_FOR_UBUNTU_LATEST` (1.61.0, the first release
  that names ubuntu26.04; 1.60.0 does not) of `supervisor/tests/test_repo_tooling.py` hold that
  knowledge. `test_playwright_is_pinned_exactly_to_a_release_that_knows_ubuntu_latest` checks the
  exact pin, the lock and the floor without the network;
  `test_the_pinned_playwright_downloads_its_browsers_for_ubuntu_latest` runs the installed
  release's `playwright install --dry-run chromium webkit` with
  `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu26.04-x64` and requires a Linux download for every
  browser it lists; it skips while `webui/node_modules` lacks the lock's release. Both failed with
  1.57.0. `test_ubuntu_latest_is_the_ubuntu_of_githubs_runner` runs only on a GitHub runner
  (`GITHUB_ACTIONS`, Linux), where every job of the workflows runs on ubuntu-latest: the runner's
  `/etc/os-release` must be Ubuntu `UBUNTU_LATEST`.
- **When GitHub moves ubuntu-latest**, the weekly bootstrap job fails, in the browser install when
  the pinned release does not know the new Ubuntu and in `make check-fast`'s runner test when it
  does, and the change that fixes it moves Playwright and the two constants together. The monthly
  pass's Playwright step checks the floor (`docs/maintenance.md`).
- `scripts/setup.sh` names the pin beside the network when the browser install fails.

## Consequences

- Only a run on GitHub sees ubuntu-latest move: the bootstrap job, once a week. The constants are
  copied by hand, and a move fails the job even when the pinned release already knows the new
  Ubuntu: a red run whose fix is the constant.
- The dry run leans on an environment variable that Playwright reads to choose its builds, not on
  a documented option: a release that drops it lists the workstation's own builds, and the check of
  Linux downloads fails on macOS.
- Every spec runs on the new release, and so does the compose test stack's image (Ubuntu 24.04,
  with the same browser builds).
- `passes` of `ci.bootstrap-in-ci` stays false until a bootstrap run on GitHub passes.

## Considered options

- `runs-on: ubuntu-24.04` for the bootstrap job: no longer today's fresh machine, which ADR-0451
  and the entry name, and a new checkout on Ubuntu 26.04 would still fail.
- A version floor alone: it checks only a number copied by hand; the dry run checks what the
  installed release does for that system.
- `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64` in `setup.sh` on a newer Ubuntu: builds made
  for another system (WebKit is built for each Ubuntu), and it hides the drift the job is there for.
