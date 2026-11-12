---
status: accepted
date: 2026-11-12
area: system
decided-by: agent
---

# ADR-0356: The NUT password of the UPS settings is write-only, and upsc runs on the host, where its absence makes the UPS unavailable

## Context

`GET` and `PUT /api/v1/power/ups/config` answered the stored NUT password, and the settings file was readable by everyone. The UPS monitor (ADR-0043) and the UPS endpoints ran `upsc` in the supervisor's container, which has no NUT client, so the UPS state could never be read, and the monitor's status did not say so. NUT is installed on the host by its owner, and `deploy/install.sh` does not install it. Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent took a short-term safety fix of `system.ups-monitoring` and made these choices.

## Decision

- The NUT password is write-only, as the secrets of the off-device copy are (ADR-0337), in `supervisor/src/power/router.py`:
  - `GET` and `PUT /power/ups/config` answer the settings without it, with `has_password`.
  - A `PUT` whose `password` is absent, null or empty keeps the stored one, so that a form's empty field means unchanged. A `PUT` without a `username` stores no password; a password without a username is refused (400, "The NUT password needs a username").
  - The file `/data/ups_config.json` is written with mode 0600, and only when the settings change (ADR-0214). The password is left out of `repr()`, and a file that cannot be read is logged with its fields and their error types, never their values (ADR-0324).
  - When the monitor starts, with the supervisor, the file and the one kept as `.bad` get mode 0600 when they have another one, such as the 0644 that the version before gave the file with the password in it, or a file put in place by hand; a file of mode 0600 is left as it is (ADR-0214). This keeps the file's rule, not an older version (ADR-0207). `chmod` changes the mode alone, so it also covers the `.bad` file, which cannot be read as settings and so cannot be written again.
- `upsc` runs on the host through `src/host.py` (ADR-0020), as `timeout --kill-after=5 10 upsc ...` (ADR-0258), off the event loop (ADR-0021). `upsc` joins `OPTIONAL_HOST_TOOLS` with the package `nut-client`, so the startup check leaves it out. `ups_name`, `host` (a host name, an IPv4 address, or an IPv6 address in brackets) and `port` (1 to 65535) are checked, since they reach `upsc` as one argument (ADR-0293).
- When the UPS cannot be read, `UPSStatus` answers `available: false` with a `reason`. While the host has no `upsc` (exit status 127 of `timeout`), the reason is "The host has no NUT client: upsc was not found. Install the package nut-client on the host":
  - `GET /power/ups/status` answers it with 200;
  - `POST /power/ups/test` answers 503 "Unable to connect to the UPS: <reason>";
  - `GET /power/ups/list` answers an empty `devices` list with the reason as `error`.
- `upsc`'s values are read one by one, as numbers with decimals or without (`100`, `23.4`); the percentages and the minutes are cut to whole numbers. A value that is no finite number is left out (null) and the others are read: the UPS stays available. No reason holds a value that `upsc` answered, so a value that changes does not make the monitor log again.
- The monitor keeps its last check in RAM. `GET /power/ups/monitor/status` answers `available`, `reason` and `last_checked` from it; with no check, before the first one and while the settings are off, `available` is false and both are null. While the UPS cannot be read, the monitor only checks again every 30 seconds. It shuts nothing down, writes nothing, and logs a warning only when the reason changes, so a host without `upsc` gets one line. It logs once more when the UPS can be read again, which happens with no restart once NUT is installed.
- Tests: `supervisor/tests/test_ups.py`, against a fake host with and without `upsc`, with values that have decimals or cannot be read, and with a settings file of mode 0644 at the monitor's start.

## Consequences

- No answer, log line or other user of the host can read the NUT password. UNOS does not use it yet: `upsc` reads without credentials. The work on the shutdown (step 2 of `system.ups-monitoring`) decides what uses it. If that work sends it to the NUT server, it must also decide whether a change of the server needs the password again, as ADR-0337 decided for the off-device copy.
- A host with NUT now gets its UPS read. A host without NUT costs one `upsc` that fails on the host every 30 seconds while the settings are enabled, and nothing else.
- Settings stored before with a `ups_name` or `host` that the checks refuse are kept as `.bad`, and the defaults are used (ADR-0324, ADR-0207).
- The web UI has no UPS page, so nothing there changes. The shutdown still runs only outside a container (ADR-0233).
