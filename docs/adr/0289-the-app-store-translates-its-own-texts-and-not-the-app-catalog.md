---
status: proposed
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0289: The App Store gives each category one label and shows the app catalog's texts as the server answers them

## Context

The second phase of `webui.translations` (ADR-0287) moves the texts of the App Store (`webui/src/routes/apps/+page.svelte`, `webui/src/lib/components/apps/`) into a catalog. Three kinds of text there are not plain texts of the web UI: an app's category, which the category list named "Smart Home", a card "Home Automation" (the value turned into words) and the details "home_automation" (the value as it came); a pool's free and total space, written by `formatBytes` with English units; and what the server's app catalog (`supervisor/src/apps/catalog.py`) holds, in English only. ADR-0287 keeps only the server's messages, the client's stand-ins for them and the console in English.

Two points of the Decision, marked below, change rules of ADR-0287, which is accepted: its English half keeps the existing texts word for word, and its walk through the pages in Russian accepts no other English than the texts it names. Such a change is the owner's (AGENTS.md, Working loop, step 2), so this ADR is a proposal until the owner accepts or refuses it; the App Store's code already does what it says.

## Decision

- The App Store's texts come from `webui/src/lib/i18n/catalog/apps.ts`.
- A category (`AppCategory` of `supervisor/src/apps/schemas.py`) has one label, the category list's, in the list, on the cards and in the details: `webui/src/lib/components/apps/AppCategory.svelte` maps each value to a key and shows a value without one as it comes. In English the cards say "Smart Home", "AI & LLMs" and "Downloads" where they said "Home Automation", "Ai" and "Download", and the details "Media" where they said "media". This changes ADR-0287's English word for word.
- The source of an image and the state of an app are shown through keys, with the labels they had: Official, LinuxServer on a card and LinuxServer.io in the details, Verified, Community; Running, Stopped. The details take the server's value `linuxserver.io` as LinuxServer.io, which they showed as Community.
- A size that the page writes itself, a pool's free and total space in the install form, takes the number format of `$intlLocale` (`webui/src/lib/i18n/index.ts`) and the unit of the catalog, by powers of 1024 as `formatBytes` of `webui/src/lib/stores/stats.ts`: 1.5 GB in English, with a decimal comma and the Russian unit in Russian.
- What the app catalog holds is shown as the server answers it, in English, like the server's messages: the names and the descriptions of the apps, the names, values and descriptions of their variables, the images, the versions and the memory recommendations (such as 512m). The walk through the pages in Russian that `webui.translations` needs counts them with the server's messages. This changes ADR-0287's walk.
- `webui/tests/apps.spec.ts` shows the catalog, the details of an app and the installed apps in Russian, the labels of the source and the category in English, and a pool with 1.5 GB free in the number format of each language.

## Consequences

- Until the owner decides, ADR-0287 stands: its walk counts the app catalog's texts as English left on the Russian App Store, so `webui.translations` does not pass with them.
- Accepted, this ADR becomes accepted, and a new ADR supersedes ADR-0287 and restates its rules with these two exceptions (the adr skill). Refused, the cards get keys of their own with their former words ("Home Automation", "Ai", "Download"), and the app catalog needs a translation before `webui.translations` passes.
- A Russian App Store describes its apps in English. Translating them needs the app catalog in both languages on the server: a feature of its own.
- The English cards and details name a category as the category list does, so a card found under "Smart Home" no longer says "Home Automation".
- Other pages that write sizes with `formatBytes` still write English units until their areas translate them.

## Considered options

- Keys of their own for the cards' words ("Home Automation", "Ai"): two labels for one category, and the filter "Smart Home" lists cards marked "Home Automation".
- Translating the descriptions in the web UI, by app id: a copy of the server's catalog that drifts from it.
- Units in `formatBytes` of `webui/src/lib/stores/stats.ts` for every page at once: a shared module that the translations of other areas use too, changed in parallel by their branches.
