---
status: accepted
date: 2026-11-12
area: security
decided-by: agent
---

# ADR-0351: Backups, power, scheduled tasks and the host's network and system settings are audited after they succeed, and answer when their entry cannot be written

## Context

`AuditAction` has `backup_*`, `system_reboot`, `system_shutdown`, `task_*`, `settings_changed`
and `network_config_changed`, but nothing wrote them (step 3 of `security.audit-admin-actions`):
an administrator's session could restore another configuration, reboot the host or add a
scheduled command that runs as root without a trace. ADR-0320 keeps entries to real changes;
ADR-0251 and ADR-0343 let only a sign-in and a user's own account changes answer when their
entry cannot be written. The agent decided the rest under the owner's delegation ("work on
through the features, ask no questions; I allow everything", in translation).

## Decision

- Each change writes one entry after it was made, by the administrator, with the client address
  and browser of the request (`audit_change` of `supervisor/src/audit/changes.py`):

  | Request | Action | Resource | Details, severity |
  | --- | --- | --- | --- |
  | `POST /backup/create`, `POST /backup/upload` | `backup_created` | `backup`, its file name | "Backup created" or "Backup uploaded", "Encrypted" before an encrypted one |
  | `POST /backup/restore/{filename}` | `backup_restored` | `backup`, its file name | "Restored database, smb_config" (what it restored), warning |
  | `DELETE /backup/{filename}` | `backup_deleted` | `backup`, its file name | none |
  | `POST /power/reboot`, `/power/shutdown` | `system_reboot`, `system_shutdown` | `host` | the answer, "The host reboots in 1 minute", warning |
  | `POST /power/cancel` | the action of what it cancelled | `host` | "The pending reboot was cancelled" |
  | `POST /power/wol/{interface}/enable`, `/disable` | `network_config_changed` | `network_interface`, its name | "Wake-on-LAN turned on" or "off" |
  | `POST /scheduler/tasks`, `PUT`, `DELETE /scheduler/tasks/{id}`, `/toggle` | `task_created`, `task_updated`, `task_deleted` | `task`, its name | "Created the snapshot task Nightly"; an edit names the fields it changed ("hour, keep_count", "name (was Old)", "passphrase"), a toggle "Enabled" or "Disabled" |
  | `POST /scheduler/tasks/{id}/run` | `task_run`, a new action | `task`, its name | "Started a run of the snapshot task Nightly" |
  | `POST /network/interfaces/{name}/down`, `/down/confirm`, `/up` | `network_config_changed` | `network_interface`, its name | "Disabled; it comes back on by itself in 60 seconds unless the disable is kept", "Disable kept: it stays off", "Enabled" |
  | `PUT /network/hostname`, `PUT /system/timezone` | `settings_changed` | `host` | "Host name changed from unos to nas", "Time zone changed from Etc/UTC to Europe/Berlin" |

  Warning marks what makes the NAS unavailable or replaces its configuration and ends every
  session; the rest is info. No passphrase, command or other value of a task is in an entry,
  only the names of the fields that changed.
- Real changes only (ADR-0320): a refused or failed request writes none, also one that changed
  part of the state before it failed (an Enable whose saved addresses could not all be
  restored); a restore that restored nothing, a cancel with nothing pending, Wake-on-LAN, a
  host name or a time zone as they were, an Enable of an enabled interface and an edit that
  changes no field write none. Such an edit and the deletion of a task that is not there no
  longer rewrite the tasks file (ADR-0214).
- A restore writes its entry in its own task, which a cancelled request does not stop
  (`_restore_and_reconcile` of `supervisor/src/backup/router.py`).
- A reboot or shutdown writes its entry as soon as `shutdown -r +1` or `shutdown -h +1` was
  accepted, a minute before logind acts. The entry is appended without fsync (ADR-0224); the
  clean reboot or shutdown writes the page cache out with the file systems, so the entry is on
  the disk when the host goes down. A power cut within the minute can lose it, as any entry.
- An entry that cannot be written, such as on a full `/data`, is in the supervisor's log
  (`log_action`, ADR-0251), and the request answers what it did: the change is made, and an
  error would say it was not. This extends ADR-0343's rule to these requests.
- The web UI names `task_run` ("Task run") and the resource types `backup`, `host`,
  `network_interface` and `task` (ADR-0298).
- Tests: `supervisor/tests/test_audit_system_actions.py`; `supervisor/tests/conftest.py` keeps
  every test's audit log in its `tmp_path`.

## Consequences

- The audit log shows who restored, rebooted, changed a task or a setting of the host, from
  where, and the Audit Log page's **Action** filter finds it.
- Each of these changes appends a line of a few hundred bytes to the audit log on the system
  disk (ADR-0214, ADR-0224); the scheduler's own runs write none, as they are in the task runs.
- Suspend, the power and UPS settings that no page sets, the off-device copy, the notification
  settings, the firewall and the TLS certificate stay unaudited until steps 1, 2 and 5 of
  `security.audit-admin-actions` or entries of their own take them up.
