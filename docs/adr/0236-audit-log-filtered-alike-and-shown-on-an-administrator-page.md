---
status: superseded
date: 2026-11-07
area: security
decided-by: agent
superseded-by: ADR-0237
---

# ADR-0236: The audit log is listed, counted and exported with one set of filters and shown on an administrator page

## Context

Superseded by ADR-0237: the same filters, exports and page, with bounds that cover the whole unit they name, the resource searched as the page shows it, and a CSV export without spreadsheet formulas.

`GET /api/v1/audit/logs` and `/logs/count` filtered by action, user, severity and dates, but `/logs/export` only by dates; nothing searched the text of the entries, and no page showed the log (feature-list.json: `security.audit-log`, `webui.audit-log-page`). Entries carry the server's local time without a zone (`datetime.now().isoformat()`; `architecture.times-with-time-zones`), and the date bounds were compared as text, so `end_date=2026-11-06` left out every entry of that day.

## Decision

- One dependency, `audit_filter` in `supervisor/src/audit/router.py`, gives `GET /logs`, `/logs/count` and `/logs/export` the same filters (`AuditFilter`): `action`, `username` and `severity` match exactly; `start_date` and `end_date` include what they name, a date (YYYY-MM-DD) its whole day and a date and time that instant; `search` (at most 256 characters, without its surrounding spaces) matches any part, in any case, of the user, the action (also with spaces for its underscores, as the page names it), the client address, the browser (the User-Agent), the resource type and id, and the details.
- The bounds are read as ISO 8601 in the server's local time, the clock of the entries, and rewritten in the entries' form before the text comparison. A value that is not a date or a date and time, or that has a time zone, gets 422.
- The selection and its sorting, newest first, run in the worker thread that reads the log (ADR-0224). `GET /logs` takes `limit` from 1 and `offset` from 0, and `/logs/export` the `format` `json` or `csv`; other values get 422.
- Both export formats are downloads: `Content-Disposition: attachment; filename=audit_log_<YYYYMMDD>.csv` or `.json`.
- The web UI's Audit Log page `/audit` (`webui/src/routes/audit/+page.svelte`, `webui/src/lib/api/audit.ts`) is for administrators only: an entry of `ADMIN_ONLY_ROUTES` and of the administrator's sidebar. It shows the newest 100 matching entries as a timeline grouped by day, and Show more loads them again with 100 more, from the newest, so that entries written meanwhile do not shift a page. An action is named by its value with spaces for its underscores (`Login failed`), which the search finds. Search, User, Action, From and To apply 300 ms after their last change to the list and its count, and as they stand to Export CSV and Export JSON, which download through `downloadAs` under the server's file name with the browser's date. Times are shown as the entries carry them, so the days of the timeline and of the date filters are the server's days.
- `/logs/summary`, `/actions` and `DELETE /logs` with its `before_date` stay as they were.
- Tests: `supervisor/tests/test_audit_log.py` (TestSignInAndUserManagementAreListed, TestFilters, TestSearch, TestExport), `webui/tests/audit.spec.ts`, and `webui/tests/roles.spec.ts` for a regular user.

## Consequences

- The last day of a range is no longer left out. An API client that sends a time zone gets 422 until the times of the API carry their zone (`architecture.times-with-time-zones`), which also settles how a browser's day maps to the server's.
- The page shows and filters by the server's clock, UTC in the container (ADR-0023), not the browser's.
- Every listing, count and export still reads the whole log, at most about 5 MiB (ADR-0224), and the page sends two requests, the list and its count, per change of the filters; an entry written between them can make the count one more than the list. Show more transfers the entries shown before again.
- The User filter takes the exact name; the search finds parts of names.

## Considered options

- A search in the browser over the loaded entries: the count, Show more and the export would not know it.
- Show more by `offset`: a page would shift by the entries written since the first one, and show some twice.
- Bounds with a time zone converted to the server's local time: a rule for every time of the API, which `architecture.times-with-time-zones` sets, not this endpoint alone.
