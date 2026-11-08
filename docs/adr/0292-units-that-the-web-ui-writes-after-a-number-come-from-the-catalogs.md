---
status: accepted
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0292: Units that the web UI writes after a number come from the catalogs, and numbers keep their decimal point

## Context

ADR-0287 moves every text of the web UI into catalogs in English and Russian, but says neither how
the units that the web UI's own code writes after a number are translated nor how the numbers are
written: the sizes of Files and of the dashboard ("1.5 MB"), a network rate ("/s"), the uptime
("3d 4h"), the age of a notification ("5m ago") and a temperature ("45°C"). What the server sends
as text, such as a disk's risk factors, stays as it comes (ADR-0287).

## Decision

- A unit that the code writes is a text of its area's catalog. Russian writes the units of a
  size (byte to terabyte), of a rate per second and of days, hours and minutes with its own
  abbreviations, and puts a space before the degree sign; the English halves keep what the code
  wrote before, word for word.
- Where the languages order or space a number and its unit differently, the number is a
  placeholder of one text: `'{n}m ago'`, `'{value}°C'`, `'{size}/s'` in English. A size's unit is a
  key of its own, which the code writes after the number and a space: `formatSize` of
  `webui/src/lib/api/files.ts` takes the units (`files.unit.*` of
  `webui/src/lib/i18n/catalog/files.ts`), and the widgets of `webui/src/lib/components/widgets/`
  look up `dashboard.unit.*`. Each area has its own unit keys: an area edits no shared catalog
  (ADR-0287), and the areas are translated in parallel.
- Numbers keep the format the code gives them, a decimal point in both languages ("1.5", "16.2%").
  Writing them in the format of the page's language (`$intlLocale` of `webui/src/lib/i18n`, a
  decimal comma in Russian) is one change for every area at once, after the areas are translated.
- Tests: `webui/tests/dashboard.spec.ts` ("shows the dashboard in Russian": the sizes of the pools,
  the uptime and the age of a notification) and `webui/tests/files.spec.ts` ("Files and the trash
  count, size and date their items in Russian").

## Consequences

- A Russian page writes its sizes with Russian units and a decimal point until the change for
  numbers; the English pages do not change.
- The unit words are repeated in the catalog of each area that writes sizes.
- A size that a `.ts` module writes, as `formatSize` does, gets its units from its caller: the
  check of the texts reads only `.svelte` files, and there it sees the keys of the units.

## Considered options

- `Intl.NumberFormat` with `style: 'unit'` (byte, kilobyte and so on): English would write
  "5 byte" for "5 B", and change texts that the specs select.
- The units in `common.ts`, shared by every area: the branches that translate the areas in
  parallel add nothing to it.
