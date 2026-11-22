---
status: accepted
date: 2026-11-22
area: security
decided-by: agent
---

# ADR-0457: Install Update, Rollback and the automatic install are audited when they start and when they end, and their settings as a change of settings

## Context

Install Update, Rollback and the automatic install's settings of System > Updates wrote no audit
entry (ADR-0443, ADR-0455, entry `system.in-app-updater`), though an update replaces UNOS's
containers and a rollback puts back an older database. ADR-0351 and ADR-0402 audit the other changes
of administrators after they succeed, but an update's outcome comes minutes later from a unit on the
host, often to the next supervisor, and the automatic install starts one with nobody signed in. The
owner delegated the decisions, asking above all that it be safe ("do as much as possible without
me", in translation).

## Decision

- **A job writes two entries**, resource type `release` with the version it goes to as the id (the
  release of an update, the version a rollback returns to), through `audit_change` of
  `supervisor/src/audit/changes.py`:

  | When | Action | By | Details, severity |
  | --- | --- | --- | --- |
  | `POST /updates/install` started the job | `system_update_started` | the administrator, with the request's address and browser | "Started the update from v1.2.0 to v1.3.0", warning |
  | the automatic install started it | `system_update_started` | `system`, no address | "The automatic install started the update from v1.2.0 to v1.3.0", warning |
  | the unit's result is success | `system_update_completed` | `system` | "Updated from v1.2.0 to v1.3.0", info |
  | the unit rolled back or failed, the job failed before the switch, the unit ended without a result or past its deadline, or a stop interrupted the job before its handover | `system_update_failed` | `system` | "The update from v1.2.0 to v1.3.0 failed: <the job's error>", warning, success false |
  | `POST /updates/rollback` started the job | `system_rollback_started` | the administrator | "Started the rollback from v1.3.0 to v1.2.0", warning |
  | the rollback ended | `system_rollback_completed`, `system_rollback_failed` | `system` | "Rolled back from v1.3.0 to v1.2.0", info; "The rollback from v1.3.0 to v1.2.0 failed: <error>", warning, success false |

  The start's entry is written by the job, inside `install_update` and `rollback` of
  `supervisor/src/updates/updater.py` (`Starter` names who started it), before any step that can fail,
  so that it precedes the end's entry also when a step fails at once; the request answers without
  waiting for it. The end's entry follows the job's progress and history, written by whichever
  supervisor records the result (`_record_switch`), the next one included, and by
  `recover_after_start` (now awaited by the lifespan) for a job that a stop interrupted. A start is a warning because UNOS restarts and a rollback replaces
  the database (ADR-0351's rule); a failure is a warning, as failed.
- **The automatic install's settings** (`PUT /updates/config`) write `settings_changed` with resource
  type `automatic_install`, by the administrator, info: "Automatic install settings changed:
  auto_install (off to on), check_interval_hours (24 to 6)" (`changed_fields`, ADR-0402).
- **Real changes only** (ADR-0320): a refused install or rollback (400, 404, 409) and a look of the
  automatic install that finds no newer release write none. Settings saved as they are stored, also
  the defaults while none are stored, write no entry and no longer rewrite `update_config.json`
  (ADR-0214); `save_config` answers the fields that changed. Stored settings that cannot be read (an
  I/O error) are written over, every field named as changed from unknown, so that a save is never
  dropped as unchanged.
- **No secret**: the details hold the versions and the job's error as `GET /updates/progress` and the
  history already show it to administrators (it may name a backup's folder or update.sh's first error
  line, none of them secret); never the release's notes. No field of the settings is secret.
- **An entry that cannot be written** is in the supervisor's log, and the request and the job go on as
  they do otherwise (ADR-0351); a failure of the write never stops a job.
- The six actions join `AuditAction` and the Action filter (ADR-0402); the Audit Log page names them
  and the resource types `release` and `automatic_install` in English and Russian (ADR-0287,
  ADR-0298).
- Tests: `TestTheAuditLog` of `supervisor/tests/test_update_install.py`; `test_audit_log.py` (names and
  the filter); `webui/tests/audit.spec.ts`.

## Consequences

- The audit log shows who started an update or a rollback of UNOS, from where, and how it ended, and
  who changed the automatic install. An end's entry carries `system`, not the administrator: the start
  before it names who started the job.
- Each job appends two lines of a few hundred bytes to the audit log on the system disk (ADR-0224); an
  idle updater writes none.
- A supervisor stopped between a job's progress and its end's entry loses that entry, as any entry on
  a power cut, and one stopped right after the answer, before the job's first step, leaves only the
  failure that the next start writes; the progress and the history still say how the job ended.
- The failure of a job that a stop interrupted names the version that runs at the next start as the
  one it came from: after `update.sh` was run by hand in between, that version is the wrong one.
