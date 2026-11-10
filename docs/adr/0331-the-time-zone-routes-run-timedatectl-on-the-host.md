---
status: accepted
date: 2026-11-10
area: system
decided-by: agent
---

# ADR-0331: The time zone and hostname routes ask the host, and PUT /network/hostname alone sets the name

## Context

`GET` and `PUT /api/v1/system/timezone` and `GET /api/v1/system/timezones` ran `timedatectl` with `subprocess.run` inside the supervisor container, where it has no systemd to ask: on a real host the zone read as "Unknown" and could not be set. `PUT /api/v1/system/hostname` ran `hostnamectl` there too, beside `PUT /api/v1/network/hostname`, which runs it on the host. Both GETs read the name from a bind mount of the host's /etc/hostname, which hostnamed replaces by a rename: after a change they showed the old name until the container restarted. And when hostnamectl failed, `PUT /network/hostname` set the kernel's name with `hostname NAME`, which a reboot loses, and answered 200.

## Decision

- The three time zone routes run `timedatectl` through `host.run_on_host_async` (ADR-0020), under the host's `timeout 10` (ADR-0258); `timedatectl` joins `HOST_TOOLS` (systemd). A zone has the form of a tzdata name (`Etc/GMT-3` included), never an option or a path.
- A zone that timedatectl does not list is refused (400), and a failed change answers 500 with timedatectl's message. Without timedatectl (no systemd, or the local stand-in) the zone reads "Unknown" and the list is empty.
- `PUT /api/v1/system/hostname` is removed (ADR-0207): `PUT /api/v1/network/hostname` sets the host's name with hostnamectl alone (a failure answers 500 with its message), and `GET /network/hostname` and `GET /system/info` read the name with `hostname` on the host; the bind mount of /etc/hostname is gone from the compose file.

## Consequences

- The Scheduler follows a zone set here at its next check, as it reads the host's /etc/localtime (ADR-0264).
- No page of the web UI sets the hostname or the zone yet (`system.hostname-and-time-zone`).
- The other commands of the system and security routers still run in the container or call nsenter themselves (`architecture.host-command-layer`).
