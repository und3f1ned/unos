---
name: feature-pass
description: Carry one feature-list.json entry to passes true in one pass, from the plan and the failing tests to the reviewed and checked user scenario, the ledger, PROGRESS.md and the ADRs. Use when asked to implement, fix or verify a feature-list.json entry, or to take on the next one.
---

# One feature per pass

A pass takes one entry of `feature-list.json` from `passes: false` to `passes: true`
(ADR-0203). Its rules:

- `passes` turns true only after every step of the entry passed as a user scenario: tests in
  `verified_by` assert each step, the E2E specs on the local stand-in cover the steps in the web
  UI, and an entry whose steps make a host-level change (the term in GLOSSARY.md) has also passed
  a device check and a re-check after a reboot of the deployed tree (ADR-0018). A skipped test
  proves nothing.
- A pass changes `passes`, `verified_by` and `notes`. A changed description or changed steps are
  a requirements change: an ADR added on this branch records it and names the entry's id, and
  the entry's `adr` lists that ADR. Dropping an entry needs an ADR added on this branch that
  names its id. `supervisor/tests/test_feature_list.py` checks both against the ledger and
  `docs/adr/` at the branch's merge base with `main`, and skips where that base has no ledger.
  Until the ledger is merged into `main`, neither a local run nor CI catches a dropped or
  reworded entry (`ci.ledger-changes-checked`) unless you name the base yourself:
  `FEATURE_LIST_BASE=$(git merge-base HEAD <base>) make test-fast`, with the worktree's base
  (AGENTS.md, Start of a session). The review of step 7 also reads the diff of
  `feature-list.json`.
- One entry per pass, worktree, branch and pull request (ADR-0210). What else you notice becomes
  a new entry with `passes: false`, or a line in PROGRESS.md, outside this diff.
- Commits and pushes happen when the owner asks (ADR-0208).

## Steps

1. **Pick the entry.** The one the owner or PROGRESS.md (Now) names; otherwise propose two or
   three entries with `passes: false` and let the owner choose. Read the entry, its ADRs, the
   AGENTS.md of its area and `git log --oneline -10`. Result: you can quote the entry's id and
   steps, and you work in a worktree and on a branch of its own (AGENTS.md, Start of a session).

2. **Check the baseline.** `make smoke` and `make check-fast`; `make check` before a large
   change. Result: a green run, or the red part written in PROGRESS.md (Now) and told to the
   owner before you change anything.

3. **Plan in plan mode.** Map each step of the entry to what will assert it: a backend test in
   `supervisor/tests/` through the API, an E2E test in the spec of the page in `webui/tests/`,
   the device check of a host-level step. List the files to change, the choices that no ADR
   settles (ask the owner, then the adr skill), and what stays out of scope. Result: the owner
   approved a plan in which every step has its test or check.

4. **Write the failing tests first** (ADR-0186). Run a backend file with
   `make test TESTS=tests/<file>` (`PYTHON=<interpreter>` picks another Python), which waits for
   the machine's test lock; never run pytest directly. Run a spec with
   `scripts/standin/e2e.sh tests/<name>.spec.ts`. Result: the new tests fail on the current code,
   for the reason the entry describes (quote the failing assertion). From now on they stand:
   weakening one to get green needs the owner's yes.

5. **Make them pass** with the simplest change that meets the entry fully: the error paths, the
   role checks (ADR-0030), no needless writes to the system disk (ADR-0214), and every caller in
   the web UI updated with an API change (ADR-0207). Result: the new tests pass.

6. **Run the gate.** `make check`; after a change to `webui/` or to an API the web UI calls, also
   `make e2e-local` (the standin-e2e skill). Result: both exit 0, nothing failed or was flaky, and
   the entry's E2E tests ran instead of being skipped.

7. **Get the change reviewed in a fresh context** (ADR-0017). Give a reviewer without this
   session's history (a subagent, or a new session with mattpocock-skills:code-review) the diff,
   the entry and its ADRs, and ask it to refute the change: a step that is not met or not
   asserted, a counterexample, an error path or a role check left out, a test weakened or fitted
   to the code, a reworded entry. Fix each confirmed finding and run step 6 again. Result: every
   finding is fixed, or recorded with its reason for the Log entry of step 10.

8. **Check a host-level entry on the device.** For an entry whose steps make a host-level change
   (GLOSSARY.md): the owner starts the deploy with /deploy-device (the model cannot load
   that skill), then run the device-checks skill for the entry's steps; the re-check after a reboot waits for the owner's
   reboot. Report the SD MiB of every step. Result: for each host-level step, a command and its
   result from the device, before and after the reboot.

9. **Update the entry.** Set `passes: true` only when steps 6 and 7 hold, and step 8 for a
   host-level entry; list the tests that assert the steps in `verified_by`, and leave the
   description and the steps as they are. A step that could not be checked keeps
   `passes: false`, and `notes` says what is missing. Run `make test-fast`. Result:
   `tests/test_feature_list.py` passes, and `git diff feature-list.json` changes this entry only.

10. **Record the pass.** PROGRESS.md gets Now, Dev device after a deploy, and a dated Log entry
    that names the review's findings left unfixed and why; each decision gets an ADR (the adr
    skill); new terms go into GLOSSARY.md; the docs describe the changed behaviour (ADR-0061).
    Result: `git status --short` lists them.

11. **Commit when the owner asks**, in Conventional Commits in English with the trailer
    `Feature: <id>` (ADR-0209). Result: `git log -1 --format='%(trailers:key=Feature,valueonly)'`
    prints the id, and the commit-msg hook accepted the message.

## Gotchas

- When a re-check of a passing entry fails, or a review finds a step that its tests do not
  assert as written, set its `passes` back to false at once, say in `notes` what is missing and
  say so in PROGRESS.md. That is a regression, not a requirements change.
- An entry too big for one pass is split by an ADR added on the branch: the new entries name it
  in their `adr`, and it names the dropped one.
- Backend tests alone are a checkbox without a check for an entry with steps in the web UI: those
  steps need E2E tests that assert what the page shows, not only that it renders.
- Playwright runs only against the stand-in (ADR-0215). What the stand-in lacks is skipped there
  with `E2E_STAND_IN=1` and needs the device check instead.
- A test that is an expected failure or a skip on macOS, such as the Linux-only paths of uploads
  and host tools, proves its step only on Linux: run it with `scripts/dev/linux-tests.sh`.
- One backend test run per machine at a time: the tests use fixed paths under /tmp until
  `testing.per-test-temp-paths` passes. The test targets of make wait for the lock
  `/tmp/unos-backend-tests.lock`; a bare pytest does not.
