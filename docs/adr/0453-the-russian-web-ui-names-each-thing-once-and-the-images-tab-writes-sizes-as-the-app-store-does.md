---
status: accepted
date: 2026-11-22
area: webui
decided-by: agent
supersedes: [ADR-0297]
---

# ADR-0453: The Russian web UI names each thing once, and the Images tab writes sizes as the App Store does

## Context

ADR-0287 asks for a walk through every page in Russian before the translation passes. The walk
(recorded in the notes of `webui.redesign` and `webui.loading-without-flicker` in
`feature-list.json`) found things named two ways by the branches that translated the areas in
parallel, such as the App Store, whose link in the navigation (`layout.nav.apps`) and page title
(`apps.title`) had two Russian names, and the Images tab of the Containers page still writing
English units ("183.11 MB") with `formatBytes` of `webui/src/lib/stores/stats.ts`, which ADR-0297
left as it was until the areas were translated.

## Decision

- One Russian name per thing, in every catalog of `webui/src/lib/i18n/catalog/`: the App Store has
  its page title's name in the navigation and every text; mounting and unmounting a pool take the
  verbs of the Storage page's buttons (`storage.pool.mount`, `storage.pool.unmount`), also in the
  Audit Log; an app's limit is the word of its Limits dialog (`apps.limits.title`); an image is
  downloaded with the verb of the Images tab's Pull button, never the verb of an upload; the
  dashboard's layout, a sign-in session, a script, a router, an address outside the LAN, the
  off-device copy (`offsite.title`) and a scrub (`scheduler.type.scrub`) have one word each; a
  status is the word of `containers.column.state`; the CPU of a stat tile or card is the
  abbreviation of `apps.resources.cpu`, and disk I/O is the word of `storage.disks.title`; a place
  of the web UI is written as the navigation and the tabs name it, quoted and joined by an arrow
  (`problems.kind.shutdown_postponed`). A number and its unit in a text that can wrap on a phone are
  joined by a no-break space (`\u00a0`, the uptime of the dashboard and of General).
- The Images tab writes an image's size with `formatSize` of `webui/src/lib/components/apps/size.ts`
  ("7.44 MB" in English, a decimal comma and the Russian unit in Russian), the units of the App
  Store's catalog and the number format of the page's language, as the App Store writes the sizes of
  the same images. `formatBytes` of `webui/src/lib/stores/stats.ts`, which nothing else used, is
  removed. This supersedes ADR-0297 in part: its rule that the helpers of `stats.ts` stay as they
  are, and its consequence that the Images tab shows English units. Every other rule of ADR-0297
  stays: the dashboard and Files take their units from their catalogs and keep a decimal point until
  the numbers of every area change together.
- Values that the server writes as English sentences or words from a fixed set are shown through
  maps to catalog keys, as ADR-0287 asks: a failure prediction's recommendation (`RECOMMENDATION` of
  `webui/src/lib/components/system/health/texts.ts`) and an interface's link state in the SMB
  interfaces dialog (`LINK_STATES` of `webui/src/lib/components/network/network.ts`, shared with
  System > Network); an unknown value shows as it comes.
- Checked by `webui/tests/containers.spec.ts` (the Russian image size), `health.spec.ts` (the
  recommendation in Russian), `smb-interfaces.spec.ts` (the link states and listening addresses in
  Russian) and the specs that select the Russian texts.

## Consequences

- On the Containers page and the App Store a size has a decimal comma in Russian, on the
  dashboard and in Files still a decimal point; the walk records this as left, with ADR-0297's
  reason.
- A translator of a new text takes the names above; the term list is in this ADR, not in a check.
