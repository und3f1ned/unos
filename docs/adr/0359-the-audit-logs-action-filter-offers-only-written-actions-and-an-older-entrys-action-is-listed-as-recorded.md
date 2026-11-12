---
status: accepted
date: 2026-11-12
area: security
decided-by: agent
---

# ADR-0359: The audit log's Action filter offers only actions that the supervisor writes, and an older entry's action that AuditAction no longer has is listed as recorded

## Context

After ADR-0343, ADR-0350 and ADR-0351, three values of `AuditAction` were still written by
nothing: `firewall_rule_added`, `firewall_rule_deleted` and `ssl_cert_uploaded`. `GET
/audit/actions` lists every value, so the **Action** filter of the Audit Log page offered three
choices that could never find an entry (step 5 of `security.audit-admin-actions`). The router that
owns them, `supervisor/src/security/router.py`, audits none of its changes: its firewall routes run
ufw inside the supervisor container, which has no ufw (`security.host-firewall`), and an uploaded
certificate is stored but not served (`security.custom-certificates`). The agent decided this
under the owner's delegation ("work on through the features, ask no questions; I allow
everything", in translation), as for ADR-0340.

## Decision

- Removed from `AuditAction` (`supervisor/src/audit/schemas.py`), from the page's `ACTION_NAMES`
  (`webui/src/routes/audit/+page.svelte`) and from the audit catalog:
  `firewall_rule_added`, `firewall_rule_deleted`, `ssl_cert_uploaded`. No route writes an entry
  for the firewall or the certificates (ADR-0207: no value kept for them).
- Every value that stays is written by the action it names:

  | Values | Written by |
  | --- | --- |
  | `login_success`, `login_failed` | `POST /auth/login` |
  | `logout`, `password_change`, `totp_enabled`, `totp_disabled` | a user's own account (ADR-0343); `password_change` and `totp_disabled` also the emergency reset (`supervisor/src/emergency.py`) |
  | `user_created`, `user_updated`, `user_deleted`, `smb_account_updated` | user management and the SMB flows of `supervisor/src/auth/router.py` (ADR-0320) |
  | `pool_*`, `volume_*`, `snapshot_*`, `container_*`, `app_*` | the storage, snapshot, container and app routers (ADR-0350) |
  | `share_*`, `nfs_export_*`, `smb_shares_imported`, `smb_reconciled`, `smb_restored` | `supervisor/src/shares/` (ADR-0320, ADR-0321) |
  | `backup_*`, `system_reboot`, `system_shutdown`, `settings_changed`, `network_config_changed`, `task_*` | the backup, power, network, system and scheduler routers (ADR-0351) |
  | `other` | API tokens and new recovery codes, the clearing of the log and the entry that says its end was cut (ADR-0328) |

- An entry's `action` is the name as recorded (`AuditEntry.action: str`); writers still pass an
  `AuditAction` to `log_action`. An older entry that names an action `AuditAction` no longer has is
  a valid entry: listed, counted, summed up by `/logs/summary`, exported, found by the search and by
  the `action` filter, which takes any action name exactly, as `username` does, and its place in
  the hash chain holds (ADR-0328). The page shows such an action as it comes, `firewall_rule_added`,
  since `ACTION_NAMES` has no key for it (ADR-0287). A line without an action, or whose action is
  not text, is still not an entry and is skipped (ADR-0224).
- Tests: `test_the_action_filter_offers_only_actions_that_the_server_writes` of
  `supervisor/tests/test_audit_log.py` fails for an action of `GET /audit/actions` that no module
  of `supervisor/src` names outside the enum (an AST scan for `AuditAction.<NAME>`; the code names
  an action only to write it); `TestAnActionNoLongerWritten` there, whose damaged-lines test now
  uses a line without an action; the test "the actions of the filter" of `webui/tests/audit.spec.ts`.

## Consequences

- Choosing any action in the filter can find entries; a new action that nothing writes fails the
  test before it reaches the page.
- Changes of the firewall (turning it on or off, rules, reset) and of the certificates (upload,
  self-signed, delete) write no audit entry. `security.host-firewall` and
  `security.custom-certificates`, which make them act on the host and serve the certificate, add
  their actions with the routes that write them.
- `GET /audit/logs?action=<name>` with a name that no entry has answers no entries where it
  answered 422; the page sends only the names of `GET /audit/actions`.
- An entry put into the log file by hand with an action of its choosing is listed with it, marked
  **Chain broken** unless its hashes were made for it, as for any edit (ADR-0328).

## Considered options

- Writing `firewall_rule_added`, `firewall_rule_deleted` and `ssl_cert_uploaded` from their routes:
  in a router that audits none of its neighbours, for firewall routes that cannot succeed in the
  container, and leaving turning the firewall off, its reset and a certificate's deletion
  unaudited beside them.
- Keeping `action` an `AuditAction` and skipping an entry whose action it no longer has: the entry
  would vanish from the list and the next one would show **Chain broken**.
