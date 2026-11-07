---
status: accepted
date: 2026-11-05
area: system
decided-by: agent
---

# ADR-0044: Custom shell tasks stay, but are administrator-only and validated

## Context

A `custom` scheduled task runs an arbitrary shell command as root in the privileged supervisor container, which shares the host's PID namespace and has the Docker socket: whoever can create, edit or run tasks is root on the host. In 1.0.0 any account could. The audit offered two fixes: remove the custom task type, or keep it for administrators only.

## Decision

- Custom tasks stay. Every endpoint of the scheduler is administrator-only: `require_admin` on the router in `supervisor/src/scheduler/router.py`, besides the administrator-only include in `main.py` (ADR-0030).
- Tasks are validated when they are saved and again before they run (`validate_task`, `execute_task`): the name (1 to 100 printable characters), the schedule, `keep_count`, the target pool or disk, and for a custom task a command of at most 4096 characters without NUL bytes. The command itself runs with `shell=True`, on purpose.
- Tasks are stored only in `/data/scheduled_tasks.json`, which no other API writes: the file manager is confined to the pools, and a restore does not restore that file (ADR-0040).
- Tests: `supervisor/tests/test_scheduler_validation.py`.

## Consequences

- Creating or running a task is equivalent to root on the host; that is intended, for administrators only.
- The check before each run also covers tasks written before the validation existed or edited by hand.

## Considered options

- Removing the custom task type, the audit's other option: not chosen; administrators keep the feature, and only they can reach it.
