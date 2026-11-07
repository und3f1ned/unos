---
status: accepted
date: 2026-11-07
area: ci
decided-by: owner
---

# ADR-0230: The CI workflow is off on GitHub, and make check on the workstation is the only gate

## Context

On 2026-11-07 the owner asked to "turn off the CI checks on GitHub; right now they seem superfluous to me, and they fail anyway". Since the repository's conversion to the agentic patterns every run of the CI workflow (`.github/workflows/test.yml`, ADR-0190) had failed: a Linux-only upload test failed on the runner's ext4, which refuses an unnamed file in a removed folder with EPERM where tmpfs creates one (fixed in `supervisor/src/files/router.py`), and two of Dependabot's pull requests failed their own checks (ADR-0231).

## Decision

- The workflow CI is disabled on GitHub (state `disabled_manually`). `.github/workflows/test.yml` stays in the repository as ADR-0190 describes it, kept in step by `supervisor/tests/test_deploy_scripts.py` and `test_repo_tooling.py`, so that turning it on again changes no file: "Enable workflow" in the Actions tab, or the API's `enable` for `test.yml`.
- `make check` on the workstation is the only gate before a merge (ADR-0216). `scripts/dev/linux-tests.sh` runs the backend tests that skip on macOS (ADR-0187), and the commit-msg hook checks commit messages (ADR-0209).
- The weekly Audit workflow (`.github/workflows/audit.yml`, ADR-0218) stays on: it checks no pull request, and it reports advisories against dependencies that did not change.
- Dependabot keeps proposing updates (ADR-0218, ADR-0231).

## Consequences

- Nothing checks a pull request on GitHub. Whoever merges runs `make check` on the pull request's branch first, and `scripts/dev/linux-tests.sh` when the change touches what the Linux-only tests cover.
- A Dependabot pull request changes the dependencies themselves, so its `make check` needs a checkout set up from its branch with `make setup`, which needs the network: the owner's machine.
- While the workflow is off, the statements that CI runs the same targets and is what the owner merges on (ADR-0216), and that CI checks every pull request (ADR-0229), do not hold.
- The Linux-only tests run only in the dev device's RAM, on tmpfs: a difference of another filesystem, such as ext4's EPERM, shows only in a test that fakes it.
- The `ci.*` entries of `feature-list.json` that extend the workflow wait until it is on again.
