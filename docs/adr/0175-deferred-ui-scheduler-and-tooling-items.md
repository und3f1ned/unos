---
status: proposed
date: 2026-11-06
area: webui
decided-by: agent
---

# ADR-0175: Fix the deferred UI, scheduler, container and tooling items

## Context

The reviews of Phase 1 slices 1 and 2 left low-severity items in the web UI, the scheduler, the container API, the test tooling and the uninstall script. They are recorded here so that a later slice fixes them instead of finding them again.

## Decision

Proposed, not implemented:

- Editing a scheduled task clears its last run and last status: `PUT /api/v1/scheduler/tasks/{id}` stores the task as sent (`update_task` in `supervisor/src/scheduler/router.py`).
- Most labels of the Scheduler tab's form are not associated with their controls; only Target Disk and Enable task are.
- While Docker is down, the container API answers HTTP 500 with Docker's error (`supervisor/src/containers/router.py`); it should answer 503.
- The NFS exports parser of `supervisor/src/shares/nfs.py` is still to be reviewed.
- Tooling: pip-audit's own temporary environment installs unpinned pip, setuptools and wheel (the CI comment in `.github/workflows/test.yml` says so); `scripts/test-cycle.sh` runs Playwright through `npx`; the hashed install of the lint tools could add `--only-binary :all:`; and because pip is pinned in `.github/lint-requirements.txt`, the documented `pip install --require-hashes` needs `python -m pip` on Windows.
- One Escape closes one dialog, chosen by the order in which the dialogs are declared (ADR-0197). A page that ever shows two dialogs stacked needs a stack of open dialogs instead.
- `deploy/uninstall.sh` restarts smbd twice when it removed a Samba password and `smb.conf.original` exists (once after the removals, once after restoring the file, ADR-0180), and it restores that file with a plain copy rather than a temporary file and a rename.

## Consequences

- Until these are fixed they behave as described; each was judged low severity.
- Each fix needs a test that fails on the code before it (ADR-0186).
