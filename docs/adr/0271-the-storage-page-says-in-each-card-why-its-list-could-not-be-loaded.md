---
status: accepted
date: 2026-11-07
area: webui
decided-by: agent
---

# ADR-0271: The Storage page loads the disks and the pools apart and says in each card why its list could not be loaded

## Context

The Storage page loaded `GET /api/v1/storage/disks` and `GET /api/v1/storage/pools` together. When one failed, for example when lsblk failed on the host (500 "lsblk failed: ..."), the error was shown above both cards, and below it "No disks found" and "No pools configured", as if the host had neither (storage.disk-list).

## Decision

- `loadData` in `webui/src/routes/storage/+page.svelte` loads the two lists with `Promise.allSettled`. A list that fails is emptied and its card says so instead of showing it: "Could not load the disks" or "Could not load the pools", with the supervisor's detail. The other list is shown as loaded. "No disks found" and "No pools configured" mean that the host reported none.
- The Create Pool dialog reloads the disks itself; when that fails, the dialog and the Physical Disks card both say so, and neither shows the old list, which may no longer match the host.
- Tests: `webui/tests/storage.spec.ts` (`Physical disks (fake data)`, on route fakes), `supervisor/tests/test_disk_safety.py` (`TestDisksEndpoint`: the 500 and its detail).

## Consequences

- As on Disk Health, a list that cannot be read is never taken for an empty one.
- A pool whose scrub status cannot be read says so on its card (ADR-0272).
