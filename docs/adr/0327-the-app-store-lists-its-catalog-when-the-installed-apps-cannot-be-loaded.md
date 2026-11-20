---
status: superseded
superseded-by: ADR-0430
date: 2026-11-10
area: apps
decided-by: agent
---

# ADR-0327: The App Store lists its catalog when the installed apps cannot be loaded

## Context

Superseded by ADR-0430: on its first load the catalog waits for the installed apps' answer up to 1 s after its own, so that the page is drawn once; the rest of this decision stays.

`webui/src/routes/apps/+page.svelte` loaded the app catalog and the installed apps with one `Promise.all`. The catalog comes from the supervisor alone; the installed apps come from Docker, for which the supervisor answers 503 when it cannot be reached (ADR-0323). So a Docker that was down hid the whole catalog behind one error, and the page said "0 apps available, 0 installed" and "No apps found" under it (`apps.catalog-without-docker`). On the stand-in, which has no Docker, `webui/tests/appstore.spec.ts` skipped its check of the catalog's cards. The agent decided this under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation).

## Decision

- The page loads the catalog and the installed apps each on its own, with a state each (loading, loaded, failed).
- The catalog is listed as soon as it came, whatever the installed apps' answer, and however long it takes; a Docker that takes connections and does not answer holds it up to its timeout (ADR-0254).
- A count is shown only from a load that succeeded:
  - the summary gives how many apps are available once the catalog came, and how many are installed once the installed apps came too;
  - the Installed tab has its count only then.
- A load that failed says so with the server's message: "The installed apps could not be loaded: ...", or "The app catalog could not be loaded: ...". A list that failed shows no empty state under its error: no "No apps installed", no "No apps found".
- Tests:
  - "the catalog does not wait for the installed apps" of `webui/tests/appstore.spec.ts` holds the answer for the installed apps on a route fake and finds the catalog listed meanwhile.
  - "should show app cards in catalog" of the same spec runs on the stand-in too, without its skip.
  - "the Apps page lists the catalog and says that the installed apps cannot be loaded" of `webui/tests/containers.spec.ts` checks it there against the stand-in's 503.

## Consequences

- While the installed apps are unknown, the catalog's cards offer Install, and marks no app as installed. An install then fails with the server's 503 in the install dialog before it writes anything (`_install_app` in `supervisor/src/apps/router.py` checks the pools before it asks Docker).
- A refresh loads both again. When Docker answers again, the installed apps and their counts come back.
