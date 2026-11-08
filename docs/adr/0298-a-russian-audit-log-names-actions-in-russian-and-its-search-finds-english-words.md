---
status: proposed
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0298: A Russian Audit Log page names actions and resources in Russian, and its search still finds them by their English words

## Context

ADR-0237 names an action on the Audit Log page by its value with spaces for its underscores (`Login failed`) and a resource by its type and id (`user carol`), the words that the search of `GET /api/v1/audit/logs` finds (`AuditFilter` in `supervisor/src/audit/router.py`). ADR-0287 shows the values of the server through maps to keys of the catalogs, so that a Russian page names them in Russian, while the server and its data stay in English. The second phase of `webui.translations` translates the Audit Log page.

On a Russian page the two accepted ADRs disagree: ADR-0287 names the actions and the types of resource in Russian, and ADR-0237 says that the page names an action by words which the search finds, and that the search matches the action as the page names it and the resource as the page shows it. The point of the Decision marked below changes that rule of ADR-0237 for a Russian page. Such a change is the owner's (AGENTS.md, Working loop, step 2), so this ADR is a proposal until the owner accepts or refuses it; the Audit Log page's code already does what it says.

## Decision

- `webui/src/routes/audit/+page.svelte` names an action through `ACTION_NAMES`, a key of `webui/src/lib/i18n/catalog/audit.ts` for each value of `AuditAction`, a resource's type through `RESOURCE_TYPES` and a severity through `SEVERITIES`; a value without a key shows as it comes. The English names stay the ones ADR-0237 gave: the action's value with spaces and a capital, the resource's type as the server records it.
- `supervisor/tests/test_audit_log.py` fails for an `AuditAction` without a key and for an English name that the search does not find in that action's entries (`test_the_web_ui_names_every_action_by_words_the_search_finds`), and for a type of resource that the server's code records without a key and an English name other than the type, which the search would not find as `<type> <id>` (`test_the_web_ui_names_every_resource_type_as_the_server_records_it`).
- The search stays on the server, unchanged: on a Russian page it finds an action or a resource by its English words (`login failed`, `user carol`), not by the Russian names that the page shows. This changes ADR-0237's page and search for a Russian page. The Action filter lists the actions by their names in the language of the page and works in both. `docs/USER_GUIDE.md` (Audit Log) says so, and so does a Russian page when a search finds nothing (`audit.noSearchMatchesHint`, with `login failed` and `user carol` as examples); the English page keeps its hint. `webui/tests/audit.spec.ts` searches a Russian page by the Russian name and by the English words of a failed sign-in.

## Consequences

- Until the owner decides, ADR-0237 stands: on a Russian page the search does not find an action or a resource as the page names it, which ADR-0237 promises, so `webui.translations` does not pass with it.
- Accepted, this ADR becomes accepted, and a new ADR supersedes ADR-0237 and restates its rules of the page and the search with the exception of a Russian page (the adr skill). Refused, either the Russian page names the actions and the types of resource by ADR-0237's English words, which the walk of ADR-0287 counts as English left unless the owner counts them with the server's values, or the search also finds the names of the page's language: the page could send, beside the text, the actions and the types whose names in its language contain it, for the server to match as well, a change of `GET /api/v1/audit/logs`, `/logs/count` and `/logs/export`.
- On a Russian page, a search for the Russian name of an action finds nothing; the Action filter, or the English words, find it.
- A new `AuditAction` or a new type of resource needs its key in `ACTION_NAMES` or `RESOURCE_TYPES` and both halves of the catalog; until then the test fails, and the page would show the bare value in both languages.

## Considered options

- The server also searching the names of the catalogs: the Russian texts would have to reach the server, whose texts stay English (ADR-0287).
- Turning a Russian query into action values in the browser: the search matches parts of six fields at once, and an action filter would drop the entries that match in another field.
