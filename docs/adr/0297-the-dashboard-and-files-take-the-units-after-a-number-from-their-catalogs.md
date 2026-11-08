---
status: accepted
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0297: The dashboard and Files take the units they write after a number from their catalogs, and numbers keep their decimal point

## Context

ADR-0287 moves every text of the web UI into catalogs in English and Russian, but says neither how
the units that the web UI's own code writes after a number are translated nor how the numbers are
written. The dashboard (`webui/src/routes/+page.svelte` and the widgets of
`webui/src/lib/components/widgets/`) and Files with its trash write sizes ("1.5 MB"), a network
rate ("/s"), the uptime ("3d 4h"), the age of a notification ("5m ago") and a temperature
("45°C"). What the server sends as text, such as a disk's risk factors, stays as it comes
(ADR-0287).

Other pages write sizes and rates with helpers of their own or with shared ones. `formatBytes` and
`formatBytesPerSecond` of `webui/src/lib/stores/stats.ts`, which webui/AGENTS.md names for the live
rates, write English units ("B" to "TB", "B/s" to "GB/s"); the Images tab of the Containers page
and the Monitor and Network tabs of the System page use them, and `webui/src/lib/api/` and the
Storage page have more such helpers. Branches translate the areas in parallel, and none of them
edits the files of another area or a shared catalog (ADR-0287). The App Store, translated by one of
them, writes a pool's free and total space in the number format of the page's language, with the
units of its own catalog (ADR-0291, proposed).

## Decision

- This decision is for the dashboard and Files. A unit that their code writes is a text of their
  catalogs. Russian writes the units of a size (byte to terabyte), of a rate per second and of
  days, hours and minutes with its own abbreviations, and puts a space before the degree sign; the
  English halves keep what the code wrote before, word for word.
- Where the languages order or space a number and its unit differently, the number is a
  placeholder of one text: `'{n}m ago'`, `'{value}°C'`, `'{size}/s'` in English. A size's unit is a
  key of its own, which the code writes after the number and a space: `formatSize` of
  `webui/src/lib/api/files.ts` takes the units from its caller (`files.unit.*` of
  `webui/src/lib/i18n/catalog/files.ts`), and the widgets look up `dashboard.unit.*`. Each area
  has its own unit keys, because an area edits no shared catalog (ADR-0287).
- Numbers keep the format the code gives them, a decimal point in both languages ("1.5", "16.2%").
- The shared helpers of `webui/src/lib/stores/stats.ts` and the helpers of other areas stay as
  they are: units from their callers would change the pages of areas that other branches are
  translating at the same time. That change, and numbers in the format of the page's language
  (`$intlLocale` of `webui/src/lib/i18n`, a decimal comma in Russian, as the App Store writes them)
  on the dashboard and in Files, are one change across the areas, after they are translated.
- Tests: `webui/tests/dashboard.spec.ts` ("the stats cards write sizes, rates and the temperature
  with their units" in English; "shows the dashboard in Russian": the stats cards of a fake stats
  socket, the sizes of the pools, the uptime and the age of a notification) and
  `webui/tests/files.spec.ts` ("Files and the trash count, size and date their items in
  Russian").

## Consequences

- The Russian dashboard and Files write their sizes with Russian units and a decimal point, and
  the Russian App Store with a decimal comma; the English pages do not change.
- The unit words are repeated in the catalog of each area that writes sizes.
- A size that a `.ts` module writes, as `formatSize` does, gets its units from its caller: the
  check of the texts reads only `.svelte` files, and there it sees the keys of the units.
- Until the change across the areas, a Russian page that writes a size or a rate with
  `formatBytes` or `formatBytesPerSecond` of `webui/src/lib/stores/stats.ts` shows English units,
  as the Images tab of the translated Containers page does. The check of the texts does not see
  them (ADR-0287): the walk through every page in Russian before `webui.translations` passes has
  to.

## Considered options

- `Intl.NumberFormat` with `style: 'unit'` (byte, kilobyte and so on): English would write
  "5 byte" for "5 B", and change texts that the specs select.
- The units in `common.ts`, shared by every area: the branches that translate the areas in
  parallel add nothing to it.
- Units from the caller in `formatBytes` and `formatBytesPerSecond` of
  `webui/src/lib/stores/stats.ts` now, for every page: their callers belong to the Containers and
  System areas, which other branches translate at the same time, and their units would need keys
  in the catalogs of those areas, which this change does not edit (ADR-0287).
