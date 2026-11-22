---
status: accepted
date: 2026-11-22
area: webui
decided-by: agent
supersedes: [ADR-0453, ADR-0388]
---

# ADR-0459: The web UI writes every number, size, percentage, duration, date and time through one home, in the format of its language

## Context

The walk through every page in Russian (`webui.redesign`'s notes in `feature-list.json`) left two
things open: the dashboard and Files wrote "90.3%" and "2.4" before the Russian megabytes with a
decimal point while Storage, the App Store and Resource Monitor wrote a comma, and dates took
different formats on different pages. ADR-0297 kept the decimal point "until one change across all
areas"; ADR-0297 is superseded by ADR-0453, which carries that remaining rule (each ADR names one
successor, so ADR-0453 is what this one supersedes). Each area had its own size helper and unit
keys, and pages passed their own Intl options (`dateStyle: 'medium'`, `'long'`, a weekday, two-digit
fields, seconds or not). The owner delegated the decisions ("do as much as possible without me").

## Decision

- **One home.** `webui/src/lib/format.ts` writes numbers (`formatNumber`), sizes (`formatSize`),
  rates (`formatRate`), percentages (`formatPercent`), temperatures (`formatTemperature`), durations
  (`formatDuration`) and ages (`formatAgo`); `webui/src/lib/time.ts` writes dates and times
  (`formatDateTime`, `formatDate`, `formatTimeOfDay`, `formatClock`, `formatDayHeading`). Both take
  `$intlLocale` (ADR-0287) and the words come from one catalog,
  `webui/src/lib/i18n/catalog/format.ts` (`format.size.*`, `format.rate`, `format.duration.*`,
  `format.ago.*`). Every page uses them. Gone: `formatBytes` of `lib/api/backup.ts`, `logs.ts` and
  `network.ts`, `formatSize` of `lib/api/files.ts`, `components/apps/size.ts`,
  `components/widgets/bytes.ts`, the Storage page's, Network's and Resource Monitor's own formats,
  `DATE_TIME_MINUTES`, `NO_SECONDS`, `roundedNumber`, the Power tab's own `knownZone`, and the
  per-area keys of units, uptimes, ages and run durations (`apps.size.*`, `backup.size.*`,
  `dashboard.unit.*`, `files.unit.*`, `health.size.*`, `monitor.size.*`, `network.bytes.*`,
  `remote.size.*`, `storage.size.*` and their kin).
- **Numbers.** Intl's digits of the language: a decimal comma in Russian, no grouping of thousands
  by default. Each view keeps the decimals it had: Storage's sizes and percentages and the
  dashboard's percentages one, always written (`ONE_DECIMAL`); a size at most one by default; the
  App Store's pools and images, Network, Resource Monitor, backups and remote access up to two. A
  view that grouped thousands keeps them (the Limits dialog's bounds, Power's MiB). A percentage is
  Intl's percent style, "90.3%" and "90,3 %" with a no-break space, and a Russian text that writes
  one writes `80\u00a0%`. A temperature is Intl's unit `celsius`: "45°C", "45 °C" (Disk Health's
  English "34 °C" becomes "34°C"). A count, a port, an id or a year is no quantity and is written as
  it is.
- **Durations and ages.** One way: from the largest unit, two units ("3d 4h", with the Russian
  abbreviations in Russian: the dashboard's uptime, a balance's time left, a scrub's time, a run's
  duration) or three (General's uptime); a duration in seconds alone keeps its decimals ("1.5s"),
  and one under a second is in milliseconds ("11ms"). A balance's "About 2 h 2 min left" becomes
  "About 2h 2m left", a scrub's "0:01:04" "1m 4s". An age is "Just now", "5m ago" or "3h ago" within
  a day, and its date after: the dashboard's recent activity, which wrote "2d ago", and the
  notification history.
- **Dates and times.** One format of a date and time: the date in numbers with the year in full and
  the hour and minute on the language's clock (Intl's `numeric` fields): "11/7/2026, 5:03 AM" in an
  en-US browser, "07/11/2026, 05:03" in en-GB, "07.11.2026, 05:03" in Russian, where the old
  two-digit minute alone wrote "5:03". It replaces the medium and long styles of the Scheduler,
  Updates, Backup & Restore, Users and the two-factor setup, Power's weekday form, the containers'
  two-digit fields, the volume chart's short month and the seconds of the pages that showed them.
  The seconds stay only where they tell entries apart: the lines of a log (the Audit Log, System >
  Logs, the sign-in history) and a trash checkbox's label (ADR-0434). A date alone and a time of day
  alone are its parts; a schedule's time without a date is `formatClock` ("Daily at 2:00 AM" in an
  en-US browser, "02:00" in Russian); the Audit Log's days are headings in full. A time of what the
  NAS runs by its own clock keeps ADR-0432's rule through `zone` (the Scheduler, Updates, Power).
  The value of an `<input type="time">` (`timeFieldValue`) and the task form's hour and minute lists
  stay 24-hour digits.
- **The check.** `webui/scripts/design-check.mjs` (ADR-0438) fails, in every `.svelte` file and
  `.ts` module of `webui/src` but the two of the home, `ui`'s included, for `toFixed`,
  `toPrecision`, `toLocaleString`, `toLocaleDateString`, `toLocaleTimeString`, `toDateString`,
  `toTimeString`, `toUTCString`, Intl's `NumberFormat`, `DateTimeFormat`, `RelativeTimeFormat` and
  `DurationFormat`, a date's getters (`getFullYear` to `getSeconds`, also of UTC), `padStart(2,
  '0')` (not of a number in another base, a MAC's `toString(16)`), and a percentage or a unit of a
  size put together after a value (`value + ' MB'`, `` `${value}%` ``, `{value} MB` between tags or
  in an attribute other than `style`); and in a catalog for a placeholder before `%` and a Russian
  digit right before `%` or with a plain space between. `design-ignore: <reason>` exempts a use: an
  SVG path's coordinates, a number field's value, the task form's lists, the hourly schedule's
  ":05". `supervisor/tests/test_webui_design_system.py` (`TestHandMadeFormats`) tests the rule,
  `supervisor/tests/test_webui_formats.py` runs the home with node in en-US, en-GB and Russian.
- This supersedes ADR-0453 in part: its rule that ADR-0297's other rules stay (the dashboard and
  Files take their units from their own catalogs and keep a decimal point) and that the Images tab
  writes sizes with `components/apps/size.ts` and the App Store's units; and ADR-0388 in part: the
  units of `dashboard.unit.*` and `dashboard.rate`. Their other rules stay.
- E2E: `dashboard.spec.ts` and `files.spec.ts` assert a decimal comma in Russian (12,5 %, 465,7 GB,
  16,2 %, 2,5 KB in the Russian units) and Files the day of a file; the specs of the Scheduler,
  Backup & Restore, Users, Snapshots, volumes, Storage, Power, notifications, two-factor setup,
  certificates and Disk Health assert the one format.

## Consequences

- A Russian page writes a decimal comma everywhere, an English one keeps its decimal point; an en-US
  page writes a schedule's time and every date in its own 12-hour form.
- The check catches the usual tools of a hand-made format, not every one: a duration put together
  from `Math.floor` and words of its own catalog, a number written with `String(n)` before a word
  that is not a unit, `toISOString` cut down to a day, and a format a server's text carries are for
  review. A new format, such as a rate in bits, is added to the home.
- Texts that are no value keep their own words: Resource Monitor's range labels ("5 min ago", "1
  hour ago") and System > Network's countdown before a change is undone ("in 60 s").
- The home keeps one Intl formatter per language and options, so a list of thousands of rows (a
  folder, a log) builds a few.
- A view that needs other decimals passes them; a new style of date is a function of `time.ts`.

## Considered options

- `Intl.DurationFormat`: its abbreviations follow the browser's ICU (a Russian day is one letter in
  one, three in another), so the specs and the two languages would drift; the catalog's units are
  fixed.
- Intl's units for sizes: English would write "5 byte" (ADR-0297).
- The medium date style as the one format: "Nov 7, 2026, 5:03 AM" is longer on a phone and was the
  format of a few pages only; most showed the numeric date.
