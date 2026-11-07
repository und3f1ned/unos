---
status: accepted
date: 2026-11-07
area: system
decided-by: agent
---

# ADR-0258: A journal read of System > Logs ends on the host after 30 seconds, two run at a time, and a search without a match lists nothing

## Context

The 30-second timeout of ADR-0257 ended only the supervisor's wait for journalctl: `subprocess.run` kills the nsenter of `src/host.py`, which forks for `--pid` and does not pass the signal on, so journalctl read on, on the host, and a search for a word that the journal does not have writes nothing that could stop it. Nothing limited how many reads ran at once, and each held a thread of the default executor, which sign-ins (bcrypt) use. `journalctl --grep` exits with status 1 when nothing matched, as grep does, and the API answered that as an error; `str.splitlines()` cut the line of an entry whose text has U+2028 or U+2029, which journalctl prints as they are; and after a failed request the tab still listed the entries of the request before.

## Decision

- `supervisor/src/logs/router.py` runs journalctl under coreutils' timeout(1) on the host: `timeout --kill-after=5 30 journalctl ...` (`JOURNAL_TIMEOUT`, `KILL_AFTER`). timeout(1) ends journalctl with SIGTERM after 30 seconds and with SIGKILL 5 seconds later, and exits with status 124, which answers 500 "Could not read the host's journal: journalctl did not answer within 30 seconds". The supervisor waits 40 seconds for the host (`JOURNAL_WAIT`), longer than timeout(1) may take, and answers the same when that wait runs out.
- The reads run on a pool of two threads of their own (`_JOURNAL_READS`, threads `unos-journal`), as the alerts and the e-mail do: at most two journalctl run on the host at once, a further request waits for a free thread, and no read takes a thread of the default executor.
- A search that finds nothing answers 200 with no entries: journalctl exits with status 1 and prints nothing, neither an entry nor a message. Status 1 with a message, status 1 without a search and every other status stay errors.
- journalctl's output is split into entries at `\n` alone: its JSON escapes the control characters, or gives such a field as its bytes, and has every other character as it is.
- The tab (`webui/src/routes/system/tabs/LogsTab.svelte`): a request that fails empties the list, and the error is shown instead of it. Search, the button and Enter, waits while an answer is still coming, like the refresh button and Auto; a change of the source or of a filter still asks at once, and the answer of the newest request is shown (ADR-0257).
- Tests: `supervisor/tests/test_logs_sources.py`, whose fake journal prints U+2028 as journalctl does, exits with status 1 for a search without a match, and ends a long read under timeout(1) but lets it read on when only the wait ends; `webui/tests/logs.spec.ts`.

## Consequences

- A read keeps a journalctl on the host for at most 35 seconds. While two slow searches run, a further request, also one for a source without a search, waits up to 30 seconds for a thread.
- The other host commands of `src/host.py` still run on after their timeout: ending nsenter's process group there would end them all, and belongs to architecture.host-command-layer.
- The local stand-in has no journalctl, and macOS has timeout(1) only from Homebrew's coreutils: its tab shows the error, as before.
- After an error the tab lists nothing until an answer comes, so no entry shown can be taken for one of the request that failed.

## Considered options

- An `asyncio.Semaphore`: it binds to the event loop that first waits on it; a pool of two threads limits the reads as well and keeps them off the default executor.
- Refusing a further read with 503: the source picked last would fail while two older reads went on.
- Aborting superseded requests in the tab: FastAPI does not stop an endpoint when its client goes away, so journalctl would read on.
