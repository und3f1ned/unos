---
status: accepted
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0299: The Shares page writes a location's kind in its language from the name that the server answers

## Context

The second phase of `webui.translations` (ADR-0287) moves the texts of the Shares page into a catalog. The share form and the NFS export form list the locations of `GET /shares/paths`, and showed each one's `label`, which the server wrote in English: "data (Pool)", "data/media (Volume)". ADR-0287 shows a value that the server answers as a word through a key of the catalog, so a Russian page must not show these words in English, and the page cannot take them out of a label without depending on the server's wording.

## Decision

- `SharePath` of `supervisor/src/shares/router.py` answers `name` in place of `label`: the pool's name, or pool/volume for a volume, with no word around it. `type` stays `pool` or `volume`, and `label` is gone (ADR-0207).
- `webui/src/lib/components/shares/ShareFormModal.svelte` and `NfsExportFormModal.svelte` write a location as `shares.location.pool` or `shares.location.volume` of `webui/src/lib/i18n/catalog/shares.ts` with its name: "data (Pool)" and "data/media (Volume)" in English, as before, and the same names with the Russian words for pool and volume in Russian.
- Tests: `supervisor/tests/test_share_api.py` (TestPaths) for the answer; `webui/tests/shares.spec.ts` and `webui/tests/nfs-exports.spec.ts` for the English forms, which keep "tank (Pool)", and for the Russian ones (In Russian). The route fakes of `webui/tests/shares-fakes.ts` answer `name`, as the server does (ADR-0189).

## Consequences

- The web UI is the only client of `GET /shares/paths`: a script that read `label` writes the kind itself.
- The forms of the Shares page also translate their own copies of the server's answers, which they show before anything is sent (a pool's top level or `apps/` as the folder, the guest warning): they are texts of the page (ADR-0287 named `webui/src/lib/components/shares/texts.ts` among the texts to translate), and their English stays word for word the server's. What the server answers is shown as it comes, in English, so a Russian form can show the same folder problem in Russian before a save and in English in the server's refusal or in the `path_problem` of a share that is not served.

## Considered options

- The page takes the name from the path, a pool's mountpoint ending with its name: a convention of `supervisor/src/storage/pools.py` that the page would depend on, with `label` left unused.
- The label as the server answers it: English words on a Russian page, which the walk of ADR-0287 finds.
