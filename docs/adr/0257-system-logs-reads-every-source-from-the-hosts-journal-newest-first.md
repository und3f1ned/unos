---
status: superseded
date: 2026-11-07
area: system
decided-by: agent
superseded-by: ADR-0366
---

# ADR-0257: System > Logs reads every source from the host's journal, newest first, with one set of filters

## Context

Superseded by ADR-0366: Auto follows new entries over the journal socket instead of asking again every 5 seconds.

The readers of `supervisor/src/logs/router.py` ran journalctl inside the supervisor container, which has no journal, with `subprocess.run` on the event loop and no timeout. Kernel ran dmesg there; Samba, Authentication and Cron Jobs fell back to the container's `/var/log`, Docker and Web Server asked systemctl in the container, and UNOS Supervisor fell back to a docker CLI that the image lacks. journalctl and dmesg print the oldest entries first, and the tab showed them as received; a severity meant that level and the more severe ones for journalctl but only that level for dmesg, the time range did not reach the kernel, and the search looked only among the newest entries (system.logs-viewer).

## Decision

- `GET /api/v1/logs/entries` runs one command on the host through `src/host.py` (ADR-0020), off the event loop with a timeout of 30 seconds (ADR-0021): `journalctl --no-pager --quiet --all --output=json --output-fields=MESSAGE,PRIORITY,SYSLOG_FACILITY,SYSLOG_IDENTIFIER,_COMM,_SYSTEMD_UNIT --reverse --lines=<limit>`, then the filters and the source's matches. It answers the entries in journalctl's order: the newest first.
- `GET /api/v1/logs/sources` is a fixed list and runs nothing. Its sources are parts of the journal:
  - System Journal (`journal`): all of it.
  - Kernel (`dmesg`): `--dmesg`, the kernel's messages of this boot from the journal instead of from dmesg, so that the levels, the time range and the search work as in the other sources.
  - Docker: `--unit=docker.service`. Samba: `--unit=smbd.service --unit=nmbd.service`, since Samba logs to syslog (ADR-0094). Cron Jobs: `--unit=cron.service`.
  - Authentication: `SYSLOG_FACILITY=4 SYSLOG_FACILITY=10`, the facilities auth and authpriv that syslog writes to `/var/log/auth.log`.
- UNOS Supervisor and Web Server are no longer sources: with Docker's json-file log driver the UNOS containers' output is not in the host's journal, and `docker logs unos-supervisor` or `docker logs unos-webui` on the host shows it. The source fields `path` and `available` are removed (ADR-0207): every source is read from the journal, and the tab lists them all.
- The filters: `priority` (`emerg` to `debug`, else 422) lists that level and the more severe ones (`--priority=`). `since` takes `<N> minutes ago`, `<N> hours ago` or `<N> days ago`, the form of the tab's time ranges (`--since=`), else 400. `search` (at most 256 characters, without control characters) is matched by journalctl itself, literally and ignoring case (`--grep=` with the text through `re.escape`, and `--case-sensitive=false`), so that the newest matches of the whole range are found. `unit`, which the tab never sent, is removed. Each value reaches journalctl as `--option=value`.
- An entry's level is the journal's PRIORITY, never guessed from words such as "failed". Its time is `__REALTIME_TIMESTAMP` in UTC with its offset, to the second, and the tab shows it in the browser's time zone. Its `unit` is the program as journalctl's own output names it: SYSLOG_IDENTIFIER, else _COMM, else _SYSTEMD_UNIT. A message that journalctl gives as bytes is decoded as UTF-8 with replacement characters.
- A journalctl that fails, does not answer in time or cannot start answers 500 "Could not read the host's journal: " and the reason.
- `HOST_TOOLS` names journalctl (systemd).
- The tab (`webui/src/routes/system/tabs/LogsTab.svelte`) shows the entries in the order of the answer and says "newest first", sends a new limit with the request it changes, and shows only the answer of its newest request. Its Auto reload, every 5 seconds, skips while an answer is still coming, so that a slow search does not start one journalctl after another on the host.
- The old `GET /logs/dmesg` and `GET /logs/journal` read the same way until system.no-legacy-log-endpoints removes them.
- Tests: `supervisor/tests/test_logs_sources.py` against a fake journal behind `src.host.run_on_host`, and `webui/tests/logs.spec.ts` with route fakes.

## Consequences

- The local stand-in has no journalctl, so its tab shows the error: the E2E spec answers the tab with route fakes, and the device check covers the host (ADR-0018).
- A search through a large journal reads until it has `limit` matches or reaches the start of the range; after 30 seconds the tab shows the error, and a shorter time range helps. journalctl ignores the case of the letters A to Z, not necessarily of other letters.
- A host that runs cron or Samba under other unit names shows nothing in those sources.
- Log files outside the journal, such as `/var/log/dpkg.log`, are not shown. `GET /logs/files` and `GET /logs/file/{filename}` still read the supervisor container's `/var/log` and have no view: which of the host's files the API may read as root is the owner's decision.

## Considered options

- dmesg on the host: its `-l` lists only the given level, its `-T` times drift after a suspend, and older util-linux has no time range.
- The UNOS containers' output through the Docker API: the supervisor's own warnings carry no level, so a severity could not apply to them; a later feature can bring that output into the tab.
- The search after journalctl's limit, as before: it found matches only among the newest entries.
