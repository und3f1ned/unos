---
status: accepted
date: 2026-11-21
area: ci
decided-by: agent
---

# ADR-0446: Both images are scanned weekly by trivy pinned by digest, and a fixable high finding fails

## Context

The audits check the supervisor's Python dependencies and the web UI's npm packages (ADR-0218,
ADR-0423), not what the images ship: the Debian and Alpine packages of their bases, Caddy's Go
binary, the Python packages of the base. The entry `ci.container-image-scanning` asks the weekly
Audit workflow, which stays on (ADR-0230), to build both images and fail on a high finding. The
owner left the policy to the agent ("decide yourself") and the package updates too ("do it
yourself").

## Decision

- `scripts/image-scan.sh`, run by `make image-scan` and by the job `images` (Image
  vulnerabilities) of `.github/workflows/audit.yml`, every Monday and on demand. Without
  arguments it builds both images from the checkout with `docker compose` (project
  `unos-image-scan`) without the build cache, as `deploy/update.sh` builds them, so that apt-get
  and apk install the packages of the day; arguments name built images to scan instead.
- trivy 0.76.2 from
  `ghcr.io/aquasecurity/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb`,
  the digest of its index (linux/amd64, linux/arm64 and two more), released on 2026-11-05. Its
  database is downloaded in a run of its own into `TRIVY_CACHE` (default `~/.cache/trivy`). Each
  image is scanned from an archive of `docker image save`, without the network
  (`--skip-db-update --offline-scan`), the Docker socket or capabilities, with a read-only root,
  as the calling user: on a Linux host root without capabilities could neither write the user's
  cache nor read the archive, of mode 0600.
- The policy: known vulnerabilities only (`--scanners vuln`) of the OS packages and the libraries
  (`--pkg-types os,library`), of severity HIGH or CRITICAL, with a fixed version
  (`--ignore-unfixed`): such a finding fails the run (`--exit-code 3`, since trivy's own errors
  exit 1, which fail it as a failed scan), and the table names the package, the installed and
  the fixed version. A vulnerability without a fix fails nothing until
  a fix is published, since nothing can be done about it yet; MEDIUM and lower are not counted.
- `.trivyignore.yaml` holds the exceptions: each entry names the vulnerability, the package
  (`purls`), the reason (`statement`) and an expiry at most a year on (`expired_at`), after which
  trivy reports it again. Three on 2026-11-21: setuptools 70.3.0 that pip 26.3's SBOM lists for
  its vendored pkg_resources (the vulnerability is in package_index, which pip does not vendor;
  trivy reports it without a path, so the purl of this version alone narrows it; until
  2027-05-21), and golang.org/x/net 0.60.0 and Go 1.27.3 of Caddy 2.11.9, for which no
  Caddy image with the fix is published (until 2026-12-21).
- The first run, with the pins of the branch's base and a build cache, failed with 143 such
  findings: 30 in Debian's linux-libc-dev (the kernel headers that `gcc` and `python3-dev` bring,
  from a cached apt-get layer) and 3 in the Python packages of the supervisor's image, 41 in
  Alpine 3.22's packages and 69 in Caddy 2.10.2's Go modules of the web UI's. Fixed here: the
  build without the cache, whose apt-get takes linux-libc-dev 6.12.117-1; `caddy:2-alpine` moves
  to its digest of 2026-11-21, `003a9780…` (Caddy 2.11.9 on Alpine 3.24, the same platforms,
  linux/riscv64 included) in `webui/Dockerfile`; the pip layer installs setuptools 84.2.1, whose
  vendored jaraco.context 6.1.0 and wheel 0.46.3 have the fixes, in place of the base's 79.0.1.
  The scan then passed. `python:3.11-slim` keeps its pin: its newest digest names the same
  linux/amd64 and linux/arm64 images, which ADR-0305 keeps under the digest the device's cache
  names.
- Step 2 of `ci.container-image-scanning` becomes: a fixable finding of high or critical severity
  fails the run and names the package.
- Tests: `supervisor/tests/test_image_scan.py` (the job, the target, the pin, the ignore file's
  entries, the script with a fake docker) and the pip layer's test in `test_deploy_scripts.py`.

## Consequences

- A new advisory against an unchanged image fails the Monday run: a newer base digest, which
  Dependabot proposes, or a newer package fixes it; an entry of the ignore file is the exception,
  and its expiry brings it back. The Caddy entries expire on 2026-12-21.
- The new Caddy base and the setuptools pin rebuild both images on the dev device at its next
  deploy: the web UI's base pull and the supervisor's pip layer, hundreds of MB on its SD card
  (ADR-0214). `scripts/dev/deploy.sh build-webui` asks for the new Caddy base on the device first:
  the owner pulls it (Open owner actions in PROGRESS.md). The device's own builds use the cache,
  so its apt-get layer keeps the packages of its last build without the cache.
- The runner scans the linux/amd64 images and `make image-scan` on the workstation those of its
  platform; the riscv64 images of a K1 (ADR-0373) are not scanned.
- `make image-scan` needs Docker and the network, for the build and the database; the trivy pin
  moves by hand in the monthly pass "The pinned check tools" of `docs/maintenance.md`.

## Considered options

- `aquasecurity/trivy-action`: one more third-party action to pin, which runs the same binary.
- Mounting the Docker socket into trivy's container: it would give the scanner the daemon of the
  host; an archive of the image gives it only that image.
- Failing on unfixed findings too: a run that fails with nothing to do teaches to ignore it.
