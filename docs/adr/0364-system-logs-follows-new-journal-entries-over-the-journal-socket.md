---
status: accepted
date: 2026-11-12
area: system
decided-by: agent
supersedes: [ADR-0257]
---

# ADR-0364: System > Logs reads every source from the host's journal, newest first, and its Auto follows new entries over the journal socket

## Context

ADR-0257 made System > Logs read every source from the host's journal with one set of filters, and
its Auto asked `GET /api/v1/logs/entries` again every 5 seconds: each time a journalctl on the host
read the whole range again, against ADR-0006 (webui.no-http-polling). Working on through the
features under the owner's delegation ("work on through the features, ask no questions; I allow
everything", in translation), the agent made Auto follow the journal over a WebSocket. The rules of
ADR-0257 that stay are restated here.

## Decision

- `GET /api/v1/logs/entries` runs one command on the host through `src/host.py` (ADR-0020), off the
  event loop, under the host's timeout (ADR-0258): `journalctl --no-pager --quiet --all
  --output=json --output-fields=MESSAGE,PRIORITY,SYSLOG_FACILITY,SYSLOG_IDENTIFIER,_COMM,_SYSTEMD_UNIT
  --reverse --lines=<limit>`, then the filters and the source's matches. It answers the entries in
  journalctl's order: the newest first.
- `GET /api/v1/logs/sources` is a fixed list and runs nothing. Its sources are parts of the
  journal: System Journal (`journal`, all of it); Kernel (`dmesg`: `--dmesg`, the kernel's messages
  of this boot from the journal); Docker (`--unit=docker.service`); Samba (`--unit=smbd.service
  --unit=nmbd.service`); Authentication (`SYSLOG_FACILITY=4 SYSLOG_FACILITY=10`); Cron Jobs
  (`--unit=cron.service`). UNOS's own containers log through Docker, not to the journal.
- The filters: `priority` (`emerg` to `debug`) lists that level and the more severe ones
  (`--priority=`). `since` takes `<N> minutes ago`, `<N> hours ago` or `<N> days ago`
  (`--since=`), else 400. `search` (at most 256 characters, without control characters) is matched
  by journalctl itself, literally and ignoring case (`--grep=` with the text through `re.escape`,
  and `--case-sensitive=false`), so that the newest matches of the whole range are found.
- An entry's level is the journal's PRIORITY; its time is `__REALTIME_TIMESTAMP` in UTC with its
  offset, to the second, shown in the browser's time zone; its `unit` is SYSLOG_IDENTIFIER, else
  _COMM, else _SYSTEMD_UNIT; a message that journalctl gives as bytes is decoded as UTF-8 with
  replacement characters; `cursor` is the journal's `__CURSOR`. A journalctl that fails, does not
  answer in time or cannot start answers 500 "Could not read the host's journal: " and the reason.
  `HOST_TOOLS` names journalctl (systemd).
- `/ws/v1/journal` (`supervisor/src/websocket/events.py`) is Auto's socket, for administrators
  only, with the first-message auth and the re-checks of ADR-0031. Its URL takes the values of
  `GET /entries`, checked as that request checks them (`journal_query` of
  `supervisor/src/logs/router.py`). After `auth_ok` it sends `{"type": "entries", "data": [...]}`,
  what `GET /entries` answers, and then `{"type": "entry", "data": {...}}` for each new entry of
  the source, level and search: `journalctl --follow --lines=all`, with the same filters and
  matches and no timeout, from `--after-cursor=` the newest entry it sent, or, when it sent none,
  from `--since=@<the second it read at>`, so that no entry falls between the read and the follow.
  The follow runs through `host.stream_on_host`, in a session of its own; when the client leaves or
  its access is revoked, `HostStream.stop` ends its process group (SIGTERM, then SIGKILL after 5
  seconds), journalctl included, which nsenter forked for `--pid` and would not pass a signal on to
  (ADR-0258). A value it does not take, a journal that cannot be read and a journalctl that ends by
  itself send `{"type": "error", "message": ...}` with the reason and close the socket with 1000.
- The tab (`webui/src/routes/system/tabs/LogsTab.svelte`) shows the entries in the order of the
  answer and says "newest first", sends a new limit with the request it changes, shows only the
  answer of its newest request, and shows an error instead of the entries. Search waits for an
  answer that is still coming. Auto opens the socket (`streamLogEntries` of
  `webui/src/lib/api/logs.ts`), shows "Following new entries", puts each new entry first and keeps
  the newest entries of the limit; a filter or a source changed while it is on, or Refresh, opens the
  socket of the new values in place of the old one; turning it off closes the socket and keeps the
  entries. An error of the socket shows as an error of `GET /entries`; a socket that closes without
  one stops Auto with "Auto stopped: the connection to the server was lost" and keeps the entries;
  4401 ends the session and 4403 says that only administrators read the logs. Nothing asks
  `GET /entries` while Auto is on.
- Tests: `supervisor/tests/test_logs_sources.py` against a fake journal behind
  `src.host.run_on_host` and `src.host.stream_on_host`; `supervisor/tests/test_ws_auth.py`
  (`TestTheJournalSocket`, its errors and its roles); `supervisor/tests/test_host_tools.py` (the
  stream of a real local command, and its process group ended); `webui/tests/logs.spec.ts`, with
  route fakes and `page.routeWebSocket` for the socket.

## Consequences

- An open Auto keeps one journalctl on the host, which reads only when the journal gets new
  entries; the open sockets are capped (ADR-0333).
- An entry that leaves the time range while Auto is on stays listed until the next load.
- The local stand-in has no journalctl, so its tab shows the error: the E2E spec answers the tab
  with fakes, and the device check covers the host (ADR-0018).
- A search through a large journal reads until it has `limit` matches or reaches the start of the
  range; after 30 seconds the tab shows the error. Log files outside the journal are not shown, and
  `GET /logs/files` and `GET /logs/file/{filename}` still read the supervisor container's
  `/var/log`.

## Considered options

- The events socket naming a change of the journal, and the tab reading `GET /entries` again: each
  change would start a read of the whole range on the host, and a busy journal changes every
  second.
- `journalctl --follow --lines=<limit>` alone, for the newest entries too: without `--reverse`,
  journalctl applies `--grep` only to the newest entries of the other filters, so a search would
  miss older matches.
