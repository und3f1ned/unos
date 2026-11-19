---
status: superseded
date: 2026-11-07
area: ci
decided-by: agent
superseded-by: ADR-0425
---

# ADR-0231: Dependabot proposes no major npm versions and leaves Playwright alone

## Context

Superseded by ADR-0425: Svelte 5 comes with SvelteKit 2, and SvelteKit 3 goes with the other majors of `webui.toolchain-major-versions`; the Dependabot rules stay.

On 2026-11-07 Dependabot's npm group (pull request 26) carried new major versions of the web UI's toolchain: Svelte 5, SvelteKit 3, Vite 8, Tailwind CSS 4, TypeScript 7, svelte-check 4 and lucide-svelte 1. They need a migration of the web UI, and `npm ci` failed on the group as it was (SvelteKit 3.1.1 accepts TypeScript ^6 only). Its Docker group (pull request 24) moved the Playwright image of `webui/Dockerfile.test` to 1.65.0 while `webui/package-lock.json` holds @playwright/test 1.57.0, which `test_image_has_the_playwright_version_of_package_lock` refuses: the ignore rule named the image `mcr.microsoft.com/playwright`, and Dependabot names it `playwright`. Both pull requests were closed without a merge.

## Decision

- `.github/dependabot.yml`, npm: semver-major updates of every dependency are ignored, and so is `@playwright/test`. The weekly npm group carries minor and patch updates only.
- `.github/dependabot.yml`, Docker: the ignore rule names the image `playwright`, as Dependabot does.
- New major versions of the web UI's toolchain are feature work: `webui.svelte-5` (Svelte 5 with SvelteKit 3), and `webui.toolchain-major-versions` for the others.
- Playwright moves by hand in the monthly maintenance pass (`docs/maintenance.md`): @playwright/test in `webui/package.json` and `webui/package-lock.json` together with the image tag of `webui/Dockerfile.test`, and the browsers with `make setup`.

## Consequences

- The weekly npm pull request can pass `make check` as it is.
- A security fix that comes only with a new major version is not proposed. The weekly audit reports it only for the runtime dependencies (`npm audit --omit=dev`: svelte, lucide-svelte, qrcode), not for the build toolchain (SvelteKit, Vite, TypeScript, Tailwind CSS, Playwright), so the monthly pass of `docs/maintenance.md` lists outdated majors with `npm outdated` and audits the dev dependencies too.
- Playwright falls behind between monthly passes.
