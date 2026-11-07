---
status: accepted
date: 2026-11-07
area: security
decided-by: agent
supersedes: [ADR-0232]
---

# ADR-0233: The audit log is filtered alike by whole units of time, exported without spreadsheet formulas and shown on an administrator page

## Context

ADR-0232 gave `GET /api/v1/audit/logs`, `/logs/count` and `/logs/export` one set of filters and the web UI an Audit Log page (feature-list.json: `security.audit-log`, `webui.audit-log-page`). Before its merge, an end bound with a time stood for one microsecond, while entries carry microseconds (`datetime.now().isoformat()`) and the page shows times to the second, so a time as the page shows it, given as both bounds, matched nothing. The CSV export wrote the values as recorded, though a spreadsheet reads a cell that starts with `=`, `+`, `-` or `@` as a formula (CSV injection) and anyone who reaches the login chooses the user name of a failed sign-in. The search did not find the resource as the page shows it.

## Decision

- One dependency, `audit_filter` in `supervisor/src/audit/router.py`, gives `GET /logs`, `/logs/count` and `/logs/export` the same filters (`AuditFilter`): `action`, `username` and `severity` match exactly; `start_date` and `end_date` include the whole unit they name; `search` (at most 256 characters, without its surrounding spaces) matches any part, in any case, of the user, the action (also with spaces for its underscores, as the page names it), the client address, the browser (the User-Agent), the resource as the page shows it (its type and id, a space between them) and the details.
- A bound is `YYYY-MM-DD`, or that date, then `T` or a space, then `HH`, `HH:MM`, `HH:MM:SS` or `HH:MM:SS` with a fraction of 1 to 6 digits (`_BOUND_FORM`), in the server's local time, the clock of the entries. It covers its whole day, hour, minute, second or fraction: `end_date=2026-11-06T12:00` takes the entries up to 12:00:59.999999, and `end_date=2026-11-06T12:00:00.4` those up to 12:00:00.499999. The bounds are rewritten in the entries' form before the text comparison. Any other value, one with a time zone or a day or time that does not exist included, gets 422.
- The selection and its sorting, newest first, run in the worker thread that reads the log (ADR-0224). `GET /logs` takes `limit` from 1 and `offset` from 0, and `/logs/export` the `format` `json` or `csv`; other values get 422.
- Both export formats are downloads: `Content-Disposition: attachment; filename=audit_log_<YYYYMMDD>.csv` or `.json`.
- The CSV export puts a `'` before each place where a spreadsheet would start a formula (`_spreadsheet_text`, every cell): a value that starts with `=`, `+`, `-`, `@`, a tab or a carriage return (the characters of the OWASP advice), and such a character after a semicolon, a tab or a line break inside a value, where a spreadsheet that splits a line on them starts a cell (the semicolon is the list separator of many locales). The list, the JSON export and the page keep the values as recorded.
- The web UI's Audit Log page `/audit` (`webui/src/routes/audit/+page.svelte`, `webui/src/lib/api/audit.ts`) is for administrators only: an entry of `ADMIN_ONLY_ROUTES` and of the administrator's sidebar. It shows the newest 100 matching entries as a timeline grouped by day, and Show more loads them again with 100 more, from the newest, so that entries written meanwhile do not shift a page. An action is named by its value with spaces for its underscores (`Login failed`), which the search finds. Search, User, Action, From and To apply 300 ms after their last change to the list and its count, and as they stand to Export CSV and Export JSON, which download through `downloadAs` under the server's file name with the browser's date. Times are shown as the entries carry them, to the second, so the days of the timeline and of the date filters are the server's days.
- `/logs/summary`, `/actions` and `DELETE /logs` with its `before_date` stay as they were.
- Tests: `supervisor/tests/test_audit_log.py` (TestSignInAndUserManagementAreListed, TestFilters, TestSearch, TestExport), `webui/tests/audit.spec.ts`, whose filter steps wait for the server's answers to the filters they set, and `webui/tests/roles.spec.ts` for a regular user.

## Consequences

- No part of the last day, hour, minute or second of a range is left out, and a time as the page shows it works as both bounds. An API client that sends a time zone, or another ISO 8601 form such as `20261106`, gets 422; times with their zone wait for `architecture.times-with-time-zones`, which also settles how a browser's day maps to the server's.
- A CSV cell can differ from its entry by these apostrophes, which a spreadsheet shows; the JSON export holds the exact values.
- The page shows and filters by the server's clock, UTC in the container (ADR-0023), not the browser's.
- Every listing, count and export still reads the whole log, at most about 5 MiB (ADR-0224), and the page sends two requests, the list and its count, per change of the filters; an entry written between them can make the count one more than the list. Show more transfers the entries shown before again.
- The User filter takes the exact name; the search finds parts of names.

## Considered options

- An end time as one instant, as in ADR-0232: a time to the second or the minute would leave out nearly every entry of that second or minute.
- Every form that `datetime.fromisoformat` takes (the basic form, week dates, a decimal comma): the unit of a bound would have to be read from each of them.
- The OWASP rule for the first character of a value alone: a spreadsheet that splits a line at semicolons or tabs starts a cell inside a value. Quoting every cell instead: spreadsheets differ in how they read a quote that does not open a field.
- The CSV values as recorded: Export CSV would hand a spreadsheet a formula chosen by anyone who reaches the login.
- A search in the browser over the loaded entries: the count, Show more and the export would not know it.
- Show more by `offset`: a page would shift by the entries written since the first one, and show some twice.
- Bounds with a time zone converted to the server's local time: a rule for every time of the API, which `architecture.times-with-time-zones` sets, not this endpoint alone.
