---
status: accepted
date: 2026-11-18
area: security
decided-by: agent
supersedes: [ADR-0359]
---

# ADR-0402: The firewall, the login history, pool mounts, images, power and notification settings and the off-device copy are audited, and the Action filter offers what the supervisor writes

## Context

ADR-0350, ADR-0351 and ADR-0359 left changes of administrators out of the audit log: the firewall,
clearing the login history, mounting, unmounting and importing pools, creating containers and
pulling and removing images, the power and UPS settings, suspend, the notification channels and the
off-device copy's target and settings. ADR-0359 also removed `firewall_rule_added` and
`firewall_rule_deleted` from `AuditAction` because nothing wrote them. The owner delegated the
decisions, asking above all that it be safe ("work on through the features, ask no questions; I allow
everything", in translation). They are the last open steps of `security.audit-admin-actions`. These changes decide who can reach the NAS, what is kept where and what
the NAS tells its owner, so they belong in the log.

## Decision

- Each change below writes one entry after it was made, by the administrator, with the client address
  and browser of the request, through `audit_change` of `supervisor/src/audit/changes.py`, for a real
  change only (ADR-0320), and answers as it does otherwise when the entry cannot be written (ADR-0251,
  ADR-0343, ADR-0350):

  | Request | Action | Resource (type, id) | Details, severity |
  | --- | --- | --- | --- |
  | `POST /security/firewall/enable`, `/disable` | `firewall_enabled`, `firewall_disabled` | `firewall` | "The firewall was turned on" / "off" (warning); only when it was off / on, read strictly: a status that `ufw status` cannot give answers 500 and writes nothing |
  | `POST /security/firewall/rules`, `DELETE .../rules/{id}` | `firewall_rule_added`, `firewall_rule_deleted` | `firewall_rule`, the rule's id | "Added the rule Web: allow in tcp port 8080 from 192.0.2.0/24"; the removal is a warning |
  | `POST /security/firewall/reset` | `firewall_reset` | `firewall` | "The firewall was reset to its defaults (SSH, HTTP and HTTPS)" (warning); as failed, "ufw was reset, then: ...", when a step after ufw's own reset failed |
  | `DELETE /security/login-history` | `login_history_cleared` | `login_history` | "Cleared 3 login attempts older than 30 days" (warning); none when it cleared nothing |
  | `POST /storage/pools/{name}/mount`, `/unmount` | `pool_mounted`, `pool_unmounted` | `pool`, its name | "Mounted the pool data", "Unmounted the pool data; its data was kept" (warning); only when the pool was mounted or unmounted or its fstab entry written or dropped; an unmount that left the pool unmounted and failed is `pool_unmounted` as failed |
  | `POST /storage/filesystems/{uuid}/import` | `pool_imported` | `pool`, its name | "Imported the filesystem <uuid> as the pool backup" |
  | `POST /containers` | `container_created` | `container`, its name | "Created the container web from example/web:latest"; never the environment, ports or volumes; as failed, "...; then: <error>", when Docker made the container and could not start it (a port in use) |
  | `POST /containers/images/pull`, `DELETE /containers/images/{id}` | `image_pulled`, `image_removed` | `image`, the reference or first tag | "Pulled the image alpine:3.20" only when the image is new or the reference now names another image; the removal is a warning |
  | `PUT /power/config` | `settings_changed` | `power` | "Power settings changed: auto_shutdown (off to on), shutdown_hour (23 to 22)" |
  | `PUT /power/ups/config` | `settings_changed` | `ups` | "UPS settings changed: enabled (off to on), password, username": the NUT user name and password by name only |
  | `POST /power/suspend` | `system_suspend` | `host` | "The host suspends now" (warning), written before `systemctl suspend` runs, since the host may be asleep when it returns; when systemctl fails a second entry of the action, as failed, says why |
  | `POST`, `PUT`, `DELETE /notifications/configs` | `settings_changed` | `notification_channel`, its name | "Added the email channel Mail", "Changed the channel Chat: enabled (on to off), events, webhook_config.url", "Removed the webhook channel Chat" (warning) |
  | `PUT`, `DELETE /offsite/settings` | `settings_changed` | `offsite`, the repository | "Off-device copy set up: local:/mnt/usb/copy", "Off-device copy settings changed: keep_daily (7 to 10), password", "Forgot the off-device copy target ...; the copies stayed where they are" (warning) |

- An entry never holds a secret. `changed_fields` of `supervisor/src/audit/changes.py` names the
  fields that differ and shows a value only for a field that the caller lists as shown: a channel
  shows its name and switch, never a webhook URL, a bot token or an SMTP password, which come as the
  field's name; the UPS and the off-device copy name their passwords, the S3 secret key and the access
  key ID, and the UPS user name, without a value; a container's environment is left out whole.
- Real changes only. A settings request that changes nothing writes no entry and no longer rewrites
  its file (ADR-0214): `PUT /power/config`, a channel saved as stored, and the same off-device
  settings and secrets (`save_settings` stores only when `_change_made` found a difference), and
  `PUT /power/ups/config` with the defaults while none are saved. A Mount of a pool that is mounted and kept in the fstab, an Unmount of one that
  is neither, an Enable of an enabled firewall and a pull that left the image as it was write none
  (`mount_pool_changed`, `unmount_pool_changed` of `supervisor/src/storage/pools.py`,
  `pull_image_changed` of `supervisor/src/containers/docker.py`).
- A reset of the firewall now stops at the first failing `ufw` command and answers 500, where it
  ignored their exit statuses.
- Two starts or two stops of one container at the same moment write one entry between them: the step
  that reads the container's state and the start or stop that follows hold the lock of that
  container (`_state_lock` of `supervisor/src/containers/docker.py`, keyed by Docker's container ID);
  other containers go on meanwhile, and the checks of an app's folders stay outside it.
- The new values of `AuditAction` are `pool_mounted`, `pool_unmounted`, `pool_imported`,
  `container_created`, `image_pulled`, `image_removed`, `system_suspend`, `firewall_enabled`,
  `firewall_disabled`, `firewall_rule_added` (back), `firewall_rule_deleted` (back), `firewall_reset`
  and `login_history_cleared`; the settings use `settings_changed`. The Audit Log page names them and
  the resource types `firewall`, `firewall_rule`, `login_history`, `image`, `power`, `ups`,
  `notification_channel` and `offsite`, in English and Russian (ADR-0287, ADR-0298).
- From ADR-0359, restated: `GET /audit/actions` offers only values that some module of
  `supervisor/src` writes, and `test_the_action_filter_offers_only_actions_that_the_server_writes`
  fails for one that nothing names; the `ssl_cert_*` values are `security.custom-certificates`'s, which
  writes them with the certificate routes, and the test neither requires nor forbids them. An entry's
  `action` is the name as recorded (`AuditEntry.action: str`): an older entry whose action `AuditAction`
  no longer has is listed, counted, summed up, exported, searched and found by the `action` filter,
  its place in the hash chain holds (ADR-0328), the page shows its action as it comes, and `log_action`
  and `audit_change` raise `ValueError` for a name that `AuditAction` lacks.
- Tests: `supervisor/tests/test_audit_security_and_power.py`,
  `test_audit_pools_containers_images.py`, `test_audit_pool_import.py` and
  `test_audit_notifications_and_offsite.py`, which failed before the change (46 of 75: no entry; the
  rest guard the requests that write nothing); `test_audit_log.py` for the filter and the
  older actions; `webui/tests/audit.spec.ts`.

## Consequences

- The audit log shows who changed the firewall, cleared the login history, mounted or imported a pool,
  created a container, pulled or removed an image, changed the power, UPS, notification and
  off-device settings or suspended the host, and the **Action** filter finds each.
- A suspend that fails leaves two entries, the first as written before the attempt and the second as
  failed: the log never claims a suspend that did not happen without saying so.
- Each change appends a line of a few hundred bytes (ADR-0214, ADR-0224); requests that change
  nothing append none and write no file.
- Still unaudited: the certificates (security.custom-certificates), the notification test and send
  requests, Run now and the restores of the off-device copy, mounting a pool that the supervisor
  does at startup and the other changes it makes on its own.
- Known gaps: an Unmount that fails after the host unmounted the pool, in the step that drops the
  fstab entry or guards the mountpoint (the one failure it records is the pool that could not be
  mounted again), and an off-device copy whose second file write fails after the first, write no
  entry; a container that Docker created and could not start has an entry, but the image that Docker
  pulled for it has none.
- The firewall routes run `ufw` inside the supervisor container, which has none
  (`security.host-firewall`): until that feature acts on the host, they answer 500 and write nothing,
  and the audit code is tested with a fake `ufw`.
- The fixes of ADR-0350's and ADR-0351's consequences: mounting, unmounting and importing pools,
  creating containers, pulling and removing images, suspend, the power and UPS settings, the
  notification settings and the off-device copy are no longer unaudited, and the double entry for two
  starts is gone.

## Considered options

- One action per settings area (`power_settings_changed`, `ups_settings_changed`, ...): more values
  for the filter and the page for one meaning; `settings_changed` with a resource type says the same.
- Writing the suspend's entry after `systemctl suspend` returns: the host can be asleep for hours
  first, and the entry would carry the time of the wake-up.
- Showing the values of every changed field: the entry would carry URLs with keys and passwords.
