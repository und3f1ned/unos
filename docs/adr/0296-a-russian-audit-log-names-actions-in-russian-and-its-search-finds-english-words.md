---
status: accepted
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0296: A Russian Audit Log page names actions and resources in Russian, and its search still finds them by their English words

## Context

ADR-0237 names an action on the Audit Log page by its value with spaces for its underscores (`Login failed`) and a resource by its type and id (`user carol`), the words that the search of `GET /api/v1/audit/logs` finds (`AuditFilter` in `supervisor/src/audit/router.py`). ADR-0287 shows the values of the server through maps to keys of the catalogs, so that a Russian page names them in Russian, while the server and its data stay in English. The second phase of `webui.translations` translates the Audit Log page.

## Decision

- `webui/src/routes/audit/+page.svelte` names an action through `ACTION_NAMES`, a key of `webui/src/lib/i18n/catalog/audit.ts` for each value of `AuditAction`, a resource's type through `RESOURCE_TYPES` and a severity through `SEVERITIES`; a value without a key shows as it comes. The English names stay the ones ADR-0237 gave: the action's value with spaces and a capital, the resource's type as the server records it.
- `supervisor/tests/test_audit_log.py` (`test_the_web_ui_names_every_action_by_words_the_search_finds`) fails for an `AuditAction` without a key and for an English name that the search does not find in that action's entries.
- The search stays on the server, unchanged: on a Russian page it finds an action or a resource by its English words (`login failed`, `user carol`), not by the Russian names that the page shows. The Action filter lists the actions by their names in the language of the page and works in both. `docs/USER_GUIDE.md` (Audit Log) says so.

## Consequences

- On a Russian page, a search for the Russian name of an action finds nothing; the Action filter, or the English words, find it.
- A new `AuditAction` needs its key in `ACTION_NAMES` and both halves of the catalog; until then the test fails, and the page would show the bare value in both languages.

## Considered options

- The server also searching the names of the catalogs: the Russian texts would have to reach the server, whose texts stay English (ADR-0287).
- Turning a Russian query into action values in the browser: the search matches parts of six fields at once, and an action filter would drop the entries that match in another field.
