---
status: accepted
date: 2026-11-21
area: ci
decided-by: agent
---

# ADR-0439: The history is scanned by gitleaks pinned by digest, with an allowlist by commit and path

## Context

The secret scan of `make test-fast` (ADR-0212) reads the working tree only; a secret committed
once stays in the history, which is never rewritten (ADR-0208). The old CLAUDE.md published the
dev device's root password and the UNOS administrator's password; both were rotated on 2026-11-07
and kept in the history (ADR-0226), which settled what ADR-0024 left open ("the git history is
cleaned"). The entry `ci.secret-scan-of-history` asks for a CI job that runs a pinned scanner
over every commit, and its step 3 still waited for that cleaning. The owner left the choices to
the agent ("decide yourself").

## Decision

- `scripts/secret-scan.sh`, run by `make secret-scan` and by the job `secrets` (Secret Scan) of
  `.github/workflows/test.yml` after a checkout with `fetch-depth: 0`, runs two checks over every
  commit that HEAD reaches; both run, and either fails it. It refuses a shallow clone.
- gitleaks v8.30.1 from
  `ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`,
  the digest of its index for linux/amd64 and linux/arm64: `gitleaks git` with
  `--log-opts="--remerge-diff <HEAD's SHA>"`, so that a merge is read as what its resolution
  changed. The container has no network, no capabilities and a read-only root with a tmpfs at
  `/tmp`, where git writes the objects of those merges (`GIT_OBJECT_DIRECTORY`, the repository's
  as alternates), and gets the git folder (a worktree's common one), `.gitleaks.toml` and
  `scripts/secret-scan.tmpl`, read-only.
- No value reaches the output: `--redact`, and the template prints a finding's rule, file,
  commit and line only. `--ignore-gitleaks-allow`: an inline comment cannot silence a finding.
  gitleaks logs git's errors and still answers "no leaks" (0 commits scanned for a repository it
  cannot read), so an `ERR` line of its log, or no commit scanned, fails the check.
- `.gitleaks.toml` extends gitleaks' default rules with two that they miss: `sshpass-password`
  (a password after `sshpass -p`, the shape in which CLAUDE.md published the root password) and
  `secret-assignment` (a literal assigned to a name whose last part ends in one of the secret words
  of the guard's `_is_secret_name`, ADR-0211, PASS but in BYPASS, ASKPASS and the like, as
  `dev.env` holds them); a variable or a placeholder is no finding.
- Its allowlist names commits and paths, never values: one block per commit, `condition = "AND"`,
  the paths of its findings and the reason. The rotated passwords are allowed only in `CLAUDE.md`
  of da193b8b; the other 16 blocks hold test values, made-up shapes in the guard's tests, the user
  guide's `YOUR_TOKEN` and the alphabet of the generated CHAP secrets, judged by rule, file and
  commit without showing a value. The first run read 635 commits (505 and the 130 merges whose
  resolution changed something) and found 55 findings in 23 pairs of commit and file
  (generic-api-key 21, secret-assignment 19, sshpass-password 7, curl-auth-header 5,
  curl-auth-user 2, jwt 1), all allowed, none in a merge.
- `scripts/published-passwords.py` holds the two digests that the working-tree scan of
  `test_repo_tooling.py` uses and checks the added lines of every commit, merges by their
  remerge-diff: a token with one of the digests fails, but in the commits and paths of its
  `ALLOWED` (CLAUDE.md, and the test stack, the E2E specs and TESTING.md, which used the
  administrator's password until ADR-0212). gitleaks' rules do not find a bare password.
- Step 3 of `ci.secret-scan-of-history` becomes: the job passes on main with the passwords once
  published in CLAUDE.md, rotated (ADR-0226) and kept in the history (ADR-0208), allowed by
  commit and path with the reason. The entry lists ADR-0208, ADR-0226 and this ADR.
- Tests: `supervisor/tests/test_secret_scan.py` (the job, the target, the pin, the template, the
  rules on sample lines, both allowlists against the history, the digest check, the script with a
  fake docker, and with the real image where it is pulled: a credential in a commit and in a
  merge's resolution, an allowed block, deletions, renames, modes and binaries).

## Consequences

- A new finding fails the scan until the next commit fixes it: a secret goes into `dev.env` and
  is rotated (ADR-0212); a test value or a placeholder gets another value, or a block by commit
  and path. A squash merge would give a new commit that no block names. gitleaks names a file of
  a merge that had a conflict with git's `b/` before its path, and a block for it names it so.
- The job waits with the rest of the CI workflow, which is off (ADR-0230); `make secret-scan`
  needs Docker and takes about a minute, most of it git's remerge of 334 merges. Dependabot does
  not read the script: the pin moves in the monthly pass "The pinned check tools" of
  `docs/maintenance.md`.

## Considered options

- `gitleaks/gitleaks-action`: it needs a license key for an organization's repositories and
  adds an action of a third party to pin; the image runs the same binary.
- A `.gitleaksignore` of fingerprints: they hold the rule and the line, which a new release of
  the rules can change; a block by commit and path keeps its reason beside it.
- Merges by their diff against the first parent (`--diff-merges=first-parent`): it repeats every
  finding of a merged branch at its merge, 110 findings in 34 merges more to allow.
- Rewriting the history: ruled out by ADR-0208 and ADR-0226.
