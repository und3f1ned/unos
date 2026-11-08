---
status: accepted
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0273: The web UI takes its texts from catalogs in English and Russian, chosen on the Account page and checked by make frontend

## Context

ADR-0023 lists the English-only web UI as debt, and the feature `webui.translations` asks for Russian first. Every text was inline English in the .svelte files, the E2E specs find elements by those texts, and an i18n library is a new npm dependency, which is the owner's call (webui/AGENTS.md). The work comes in two phases: this one builds the catalogs, the choice of language, the check, and translates the frame, the sign-in, setup and Account pages and the shared components; later branches translate one area each, in parallel.

## Decision

- No library. `webui/src/lib/i18n/translate.ts` looks a key up in the current language, else in English, else returns the key itself, fills `{name}` placeholders and picks a plural form by `Intl.PluralRules` from `params.n`. `webui/src/lib/i18n/index.ts` has the stores `locale`, `t` (`$t('area.key', params)`) and `intlLocale` (for dates).
- Catalogs: `webui/src/lib/i18n/catalog/<area>.ts` exports `en` and `ru: Translation<typeof en>`, keys start with `<area>.`, and a text with a count has the plural forms of its language (`one` and `other` in English; `one`, `few` and `many` in Russian). `import.meta.glob('./catalog/*.ts', { eager: true })` collects them: a new area adds its file and edits no shared one. `common.ts` holds the words that many areas use; this phase adds `layout`, `login`, `setup`, `system`, `account` and `ui`.
- The choice: the Account page (System > Security for an administrator, Account for a regular user) has a Language card that offers English and Russian, each named in its own language. It applies at once and is kept in this browser's `localStorage` as `unos-locale`: it belongs to the browser, not to the account, so the sign-in page shows it too. Without a saved choice the language is Russian when the primary subtag of `navigator.language` is `ru`, else English. `<html lang>` follows it. Dates take the browser's own locale when it is of the chosen language (en-GB keeps British dates), else the chosen language.
- English stays: the server's messages (the API's `detail`, shown as it comes; the route fakes copy them, ADR-0189), the client's stand-ins for a missing one in `webui/src/lib/api/client.ts` ("Request failed", the rate-limit text) and the developer console.
- The specs run in English: `webui/playwright.config.ts` sets an en-US browser, and the English half keeps the existing texts word for word. `webui/tests/language.spec.ts` switches to Russian and back on the Account page, for an administrator and a regular user, through reloads and the sign-in page, and checks the default of a browser set to Russian.
- The check, `webui/scripts/i18n-check.mjs` (`npm run i18n`, which `make frontend` runs), reads every .svelte file with svelte/compiler and TypeScript. It fails for a text with letters that does not come from `$t`: text between tags; the value of a user-facing attribute or prop (placeholder, title, alt, aria-label, label, hint, description, error, a prop named ...Label, ...Hint, ...Text and the like); a string literal that an expression of the markup shows; a string literal that the script gives to a variable, prop or property whose name says that it holds a text, or to confirm(), alert() or prompt(). It fails for a `$t` key that no catalog has, a plural text without `n`, and a catalog whose `ru` half differs from `en` in keys, plural forms or placeholders, or holds an empty text. `webui/scripts/i18n-untranslated.txt` lists the files not translated yet, by area with blank lines between the groups, so that git merges branches that take off different areas; a listed file with nothing left to translate fails. `supervisor/tests/test_i18n.py` tests the check and the lookup.
- `webui.translations` passes once that list is empty.
- The `ru` halves of the catalogs, the name of Russian in `LANGUAGES` of `webui/src/lib/i18n/index.ts` and the Russian texts that `webui/tests/language.spec.ts` expects are the web UI's Russian texts themselves; everything else in the repository stays English (ADR-0200).

## Consequences

- A text that a function returns (a formatter's "Unknown") or that a .ts module holds (`webui/src/lib/components/shares/texts.ts` and `access.ts` there) is out of the check's reach: review checks it, and webui/AGENTS.md says to translate it too.
- A Russian page shows the server's messages in English, and a text set by an action stays in the language it was set in until the next action.
- Both languages are in the main bundle.
- The catalogs are TypeScript, so svelte-check also fails for a missing or an extra key of a `ru` half.

## Considered options

- An i18n library (svelte-i18n, typesafe-i18n): a new dependency is the owner's call; the store and the lookup are about 100 lines.
- One catalog file per language: every branch of the second phase would edit the same two files.
- Translating the server's messages: that needs error codes from the API, outside this feature.
