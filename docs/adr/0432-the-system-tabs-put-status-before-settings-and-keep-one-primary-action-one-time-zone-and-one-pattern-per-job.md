---
status: accepted
date: 2026-11-20
area: webui
decided-by: agent
---

# ADR-0432: The System tabs put status before settings, and keep one primary action, one time zone and one pattern per job

## Context

The owner called the redesign of ADR-0428 careless ("the design is slapdash, the UX limps"), sent
the Power schedule card and the Backup retention row as examples of a mess, and delegated the
decisions, asking above all that they be safe. A UX audit of the web UI found, on the System tabs
other than Security: settings and status mixed in one card with two time zones (Power), event
choices that do nothing and four stat tiles of zeros (Notifications), fields of one row at different
heights and a Save disabled without a reason (Backup), solid orange and red buttons for routine
actions, icon rows in three colours (Scheduler, Backup), health shown three ways (Disk Health),
charts without scales (Resource Monitor), and five looks of tabs. ADR-0431 placed every setting
once; this ADR decides how each System tab shows it. The shared components that the audit asks for
(Switch, FieldGroup, ActionMenu, ConfirmDialog, Toast, Tabs) are another entry's; these tabs are
built so that each becomes a one-line swap.

## Decision

- **Status first, settings apart.** A tab opens with what the NAS does now, then its actions, then
  its settings. Power: a Status card (the pending reboot or shutdown with Cancel, the next shutdown,
  the next wake, the idle shutdown), then Reboot, shutdown and suspend, then the Shutdown and wake
  schedule, then Wake-on-LAN (`webui/src/lib/components/system/power/`). Off-device copy: the state
  and the snapshots in one card, its settings in a second one (`OffsiteCopy.svelte`).
- **One time zone per tab, named once.** Power and Scheduler show every time, the plan's, the next
  runs' and the history's, on the NAS's clock and name its zone once under the tab's title
  (`knownZone` of `components/system/power/texts.ts`; the browser's zone only when it does not
  know the NAS's). This amends the web UI part of ADR-0407, whose plan showed the browser's zone.
- **One reason, said once.** When the host's power state cannot be read, Power says it once with
  Retry; the cards say they wait, and Reboot, Shut down and Suspend are disabled with that reason
  beside them. A shutdown's reasons to wait show only while that shutdown is on, each once with
  its count ("2 login sessions of root"). Technical text (restic's error, the RTC's sysfs path,
  smartctl's attribute names) follows a plain sentence behind a Details or Why disclosure.
- **One primary action per view, routine actions secondary.** The page header holds the primary
  (New task, Create backup, Add channel); a form's submit is its own. Reboot, Shut down, Disable an
  interface, Turn off remote access and Forget this target are secondary; the solid danger colour
  is the confirming button's in a dialog. A card header's action that opens a dialog is a small
  secondary button (Local names' Add name, Peers' Add peer).
- **One pattern per job.** Views and sources are page tabs (`tab-bar`, `tab-underline`: Network,
  Notifications, System Logs); filters are the segmented control. Families of add buttons become
  one: Add channel and Create… ask for the kind in their dialog. An on/off is a checkbox standing in
  for the Switch: a channel's alerts, a task's schedule, the power schedule's three rows, Logs'
  Follow (formerly Auto). Icon actions are neutral with their names as tooltips, and an action icon
  is never a state: a task shows Paused or Last run failed as a badge, a backup's Restore is a
  word. Badges read alone in sentence case (Disk Health: one badge per disk, Replace soon,
  Failing, Healthy, Not read or Asleep; the SMART dialog keeps smartctl's overall health).
- **Forms.** Related fields form a group with a legend and one line of help; labels are short and
  units sit beside the field; compact numbers are compact fields (Backup's Snapshots to keep and
  Integrity check, Power's idle minutes). A Save disabled while nothing changed says "No changes";
  one that waits says for what (the off-device copy's target, folders and password, a task's
  passphrase); Remote access's Turn on stays enabled and names the missing address at its field.
- **Notifications offer only what works.** Every enabled channel receives every alert
  (`_deliver_alert` of `supervisor/src/health/router.py`, ADR-0332), so the dialog offers no
  events and a channel shows none; the events a channel was stored with are sent back unchanged.
  The four stat tiles go: the history is one tab away.
- **Charts and numbers.** Resource Monitor's charts have their scale (the top line's value) and a
  time axis (the start of the range, Now); values are plain text with a swatch per line; each core
  shows its share; Read and Write are words. Rates elsewhere (Network) are plain text too.
- **Casing and names.** The catalogs of these tabs are in sentence case, and each tab's title is
  its name in the navigation (Scheduler, Backup & Restore, Updates).
- Checked by the specs of the tabs (`webui/tests/power.spec.ts`, `notifications.spec.ts`,
  `scheduler.spec.ts`, `backup.spec.ts`, `offsite.spec.ts`, `health.spec.ts`, `logs.spec.ts`,
  `monitor.spec.ts`, `network-virtual.spec.ts`, `remote-access.spec.ts`, `updates.spec.ts`) and the
  views of `mobile-layout.spec.ts`.

## Consequences

- A new card on these tabs follows the order status, actions, settings, and names its time zone
  only if the tab has none named.
- Until the shared Switch, FieldGroup, ActionMenu and ConfirmDialog exist, checkboxes, legends,
  secondary buttons and the hand-made restore and delete dialogs stand in for them; each place is
  one line to change.
- The server's list of events (`GET /api/v1/notifications/events`) and a channel's `events` field
  are no longer shown by the web UI.
- A peer has no "Show config…": its configuration holds the peer's private key, which the web UI
  shows once when the peer is added and the server never answers again (ADR-0420); a lost one is a
  new peer.
- Specs that clicked Auto, the Disable of a channel or the play icon of a task now use the on/off
  checkboxes; texts that changed case changed in the specs with them.
