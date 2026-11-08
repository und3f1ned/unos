---
status: accepted
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0299: The Storage page writes its sizes and the start of a scrub in the page's language and keeps the server's reasons and btrfs's names

## Context

The second phase of `webui.translations` (ADR-0287) moves the texts of the Storage page (`webui/src/routes/storage/+page.svelte`) into a catalog. The page also shows values that are not texts of its own: the sizes, percentages and rates it writes itself, when the last scrub started as btrfs prints it, a disk's in-use reason that the server writes, and btrfs's names. ADR-0287 settles dates with a time zone and the server's messages, not these, and ADR-0291, which proposes the same for the App Store's sizes, is not decided. The English texts stay word for word: the E2E specs pin them.

## Decision

- The texts come from `webui/src/lib/i18n/catalog/storage.ts`.
- A size that the page writes (of a disk, a partition, a pool, a filesystem found on the disks, a scrub's progress and rate) has one decimal in the number format of `$intlLocale`, without grouping, and the unit of the catalog, by powers of 1024: 59.6 GB in an en-US or en-GB browser, and in Russian with a decimal comma and the Russian unit. A scrub's progress is a percentage of `$intlLocale` with one decimal: 25.0% in en-US.
- When the last scrub started (`last_run` of GET /storage/pools/{name}/scrub) is btrfs's ctime in English, "Fri Nov  6 10:00:00 2026", in the time zone of the supervisor's container, which it does not name. An English page shows it as btrfs prints it; a page in another language shows the same date and clock time in its own date format (the fields of `toLocaleString` in `$intlLocale`, in UTC, which keeps the clock time): 06.11.2026, 10:00:00 in Russian. A value of another form, or a date that does not exist, is shown as it comes.
- A disk's in-use reason (`in_use_reason` of GET /storage/disks), such as "Mounted at /", is the server's message: the server sends the same text in the 409 that refuses the disk for a pool (`_check_pool_devices` in `supervisor/src/storage/pools.py`). It stays in English with the server's other messages (ADR-0287). The page's own words for a disk without a reason, "System disk" and "In use", are texts of the catalog.
- A pool's RAID profile on its card (`raid_level`, btrfs's data profile) goes through a map from btrfs's profiles to keys, as the server's words do (ADR-0287); the texts are btrfs's names (single, dup, raid0, raid1, raid1c3, raid1c4, raid10, raid5, raid6) in English and in Russian, and a profile without a key is shown as it comes. lsblk's names, a filesystem's type (btrfs, ext4) and a partition table's (GPT, DOS), are open sets and are shown as they come, and so is the command `btrfs device stats -z`: names, the same in every language. The RAID levels offered for a new pool are texts of the catalog.
- A label or a name shown on its own takes the quotes of the language: "e2e_tank" in English, guillemets in Russian.
- `webui/tests/storage.spec.ts` ("The Storage page in Russian") shows the page and its dialogs in Russian with these values.

## Consequences

- The walk through the pages in Russian (ADR-0287) finds English on the Storage page in the server's messages, the disks' in-use reasons among them, and in the names of btrfs and lsblk only.
- An English page writes the numbers as the browser's English does, as it writes its dates (ADR-0287): an en-ZA or en-DE browser shows a decimal comma where the page wrote 59.6 GB for every browser before.
- An English page writes the start of a scrub in btrfs's form, unlike the other dates of the web UI. Writing it in the browser's time zone needs the server to answer it as a time with its zone (`architecture.times-with-time-zones`).
- The decimal comma is only for what the page writes: the quota field still takes a number with a decimal point, as its message says in both languages.

## Considered options

- The start of a scrub in the page's date format in English too: the English page and the specs that pin btrfs's form would change.
- Translating the in-use reasons in the web UI by matching the server's English sentences: a copy of the server's wording that drifts from it. Reason codes from the API would do it, as error codes would for the server's messages: a feature of its own.
