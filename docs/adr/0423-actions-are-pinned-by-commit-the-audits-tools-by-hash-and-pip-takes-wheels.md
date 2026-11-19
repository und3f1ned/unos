---
status: accepted
date: 2026-11-19
area: ci
decided-by: agent
supersedes: [ADR-0190, ADR-0216, ADR-0218]
---

# ADR-0423: Actions are pinned by commit, the audit's tools by hash, and pip takes wheels only

## Context

The workflows named their actions by a major tag (`actions/checkout@v7`), which the owner of an
action's repository can move to other code; the next run takes it with the job's token.
`pip-audit -r supervisor/requirements.txt`, the Python audit of ADR-0190, ADR-0216 and ADR-0218,
resolves the requirements in a temporary virtual environment into which it first installs the
latest pip, setuptools and wheel, unpinned, and pip cannot take hashes for them there: a
constraints file with hashes turns on hash-checking for the requirements too, which have none.
`scripts/test-cycle.sh` ran Playwright through npx, which downloads a missing program from the
registry, and every pip install could run the build code of an sdist. ADR-0175 listed the last
three. This ADR supersedes only those audit and install commands of ADR-0190, ADR-0216 and
ADR-0218; every other rule of the three stays in force as written there.

## Decision

- Every `uses:` line of `.github/workflows/` names the full commit SHA of a release with its version
  in a comment, `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`, and an action
  has the same pin in every workflow. Dependabot's `github-actions` updates, without an ignore rule,
  propose the next release's commit and rewrite the comment.
- `scripts/audit-python.sh` replaces `pip-audit -r` in `make audit`, `test.yml` and `audit.yml`. In
  a temporary virtual environment of Python 3.11 (it refuses another version) it installs
  `.github/audit-env-requirements.txt`: pip, setuptools, wheel and packaging (which wheel needs),
  pinned with the sha256 of their wheels, `--require-hashes --only-binary=:all:`. pip and packaging
  keep the versions of `.github/lint-requirements.txt`. That pip resolves
  `supervisor/requirements.txt` as pip-audit did (`--dry-run --report`), with
  `--ignore-installed --only-binary=:all: --no-binary=nut2`, and `--no-build-isolation`, so that
  the pinned setuptools builds nut2's sdist instead of one fetched from the index; `pip-audit
  --disable-pip --no-deps --strict` checks the resolved versions. An empty resolution fails.
- The pip installs of the checks take wheels only, `--only-binary=:all:`: `scripts/setup.sh` for
  both venvs, the CI jobs, `supervisor/Dockerfile.test` and the audit. Where they install or
  resolve `supervisor/requirements.txt`, directly or through `requirements-dev.txt`, the command
  line adds `--no-binary=nut2` after it (the other order would clear it): nut2 is the one package
  without a wheel.
- `supervisor/Dockerfile` and `supervisor/requirements.txt` are left as they were: the image
  copies `requirements.txt` right before its pip layer, so an option in either file would build
  that layer anew, about a hundred MB of writes on the dev device's SD card (ADR-0214), and change
  no wheel it installs. With the exact pins of `requirements.txt`, pip's default already takes the
  wheel wherever the pinned version has one, which meets "where they exist" there. On riscv64
  cryptography, bcrypt, cffi, psutil, uvloop and PyYAML publish no wheel and are compiled
  (ADR-0373).
- `scripts/test-cycle.sh` runs `./node_modules/.bin/playwright` in `webui/`.
- Tests: `TestContinuousIntegration`, `TestAuditEnvironment`, `TestWheelsOnly` and `TestTestCycle`
  of `supervisor/tests/test_deploy_scripts.py`, and the setup, make and Dependabot tests of
  `supervisor/tests/test_repo_tooling.py`.

## Consequences

- The action pins, the audit environment's file and the lint tools move only by a commit; the last
  two by hand in the monthly pass of `docs/maintenance.md`, which Dependabot does not cover.
- A pinned package whose version has no wheel for the platform stops `make setup`, the CI jobs and
  the audit until its command lines name it with `--no-binary`; for a dependency that is not
  pinned, they take the newest version that has a wheel, which can be older than the one the image
  installs from its sdist, so the audit can then check another version than the image's. An Intel
  Mac cannot run `make setup` or `make audit`: cryptography 50 has no macOS x86_64 wheel. The
  audit resolves for the platform it runs on, as `pip-audit -r` did.
- Outside the audit, nut2's sdist is still built with the newest setuptools from the index (pip's
  build isolation): in `make setup`, the CI jobs and the images. The image still builds any
  package whose resolved version has no wheel, and a riscv64 build the packages it compiles.
- The supervisor image's pip layer does not change.

## Considered options

- `PIP_CONSTRAINT` with hashes for pip-audit's own environment: pip 24.0 refuses unpinned
  requirements in hash-checking mode, and every pip then demands hashes of the supervisor's
  requirements too.
- `--only-binary=:all:` in `supervisor/Dockerfile`, or `--no-binary nut2` in
  `supervisor/requirements.txt`: either builds the image's pip layer anew on the dev device and
  changes none of the wheels it installs.
- `--only-binary` on riscv64 for the packages that have a wheel there: a later version without
  one would stop the build, or resolve an older version than the other platforms and the audit.
- `--no-build-isolation` for nut2 in `make setup` and the images: it would build every sdist with
  whatever setuptools the environment has (ensurepip's, or the base image's), and the riscv64
  build needs isolation for the packages it compiles.
