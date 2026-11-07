---
status: accepted
date: 2026-11-07
area: system
decided-by: agent
---

# ADR-0232: Power actions run on the host through host.py and are off where host commands run locally

## Context

The power router ran reboot, shutdown, cancel, suspend and the Wake-on-LAN commands inside the supervisor container, behind `is_in_container()`, which `/.dockerenv` makes true there: reboot, shutdown and suspend answered "unavailable", and Wake-on-LAN listed and set only the container's `eth0`. The local stand-in relied on that check (`container=docker`) to keep the workstation from being rebooted, and `supervisor/src/system/router.py` had its own unguarded copies of reboot, shutdown and cancel (`system.power`).

## Decision

- `supervisor/src/power/router.py` runs every power command on the host through `src/host.py` (ADR-0020), off the event loop with timeouts (ADR-0021):
  - Reboot and Shutdown schedule `shutdown -r +1` and `shutdown -h +1`, with a wall message, so that the host acts one minute later.
  - The pending action is logind's: `busctl --json=short get-property org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager ScheduledShutdown`, answered by `GET /api/v1/power/pending` and with every action. `reboot`, `kexec` and `soft-reboot` count as a reboot, `poweroff` and `halt` as a shutdown; logind's dry runs count as nothing.
  - Cancel runs `shutdown -c` only while a reboot or shutdown is pending, and fails when it is still pending afterwards.
  - Suspend runs `systemctl suspend` once `/sys/power/state` of the host offers `mem`, and answers 409 otherwise.
  - Wake-on-LAN lists the host's Ethernet devices of `ip -d -j link show` that have no link kind (no veth, bridge, bond, VLAN or tunnel) with `ethtool <name>`, and sets `ethtool -s <name> wol g` or `wol d`: on is magic packet only. A change that the host did not take answers 500.
- Guard: with `UNOS_HOST_EXEC=local` (`host.runs_locally()`) these endpoints answer 503 with the text of `POWER_OFF_DETAIL` and run nothing. The supervisor then runs its host commands on the machine or container it runs on: the local stand-in on a workstation (`scripts/standin/run_supervisor.py`) and the unprivileged compose test stack (`deploy/docker-compose.test.yml`), never a NAS host. The guard replaces `is_in_container()` for these endpoints; the UPS monitor keeps that check until `system.ups-monitoring`, and the stand-in keeps `container=docker` for it.
- An administrator's Wake-on-LAN choice per interface name is kept in `/data/wol_config.json`, written only when it changes (ADR-0214), and applied again at each start of the supervisor, in the lifespan (ADR-0043), because many drivers reset Wake-on-LAN at boot.
- The duplicates `POST /api/v1/system/reboot`, `/system/shutdown` and `/system/cancel-shutdown` are removed (ADR-0207).
- `HOST_TOOLS` names `shutdown` (systemd-sysv), `busctl` (systemd), `ip` (iproute2) and `ethtool` (ethtool).
- System > Power shows each answer, the pending action with a Cancel button, and the interfaces with their MAC address, their state and a button to turn Wake-on-LAN on or off; Reboot, Shutdown and Suspend ask for confirmation first.
- Reboot, Shutdown and Suspend take at most two requests a minute per client, as reboot and shutdown did before.
- Tests: `supervisor/tests/test_power.py` against a fake host, `webui/tests/power.spec.ts` with route fakes and the stand-in's own refusal, `supervisor/tests/test_lifespan.py` and `supervisor/tests/test_standin.py`.

## Consequences

- On a real host a confirmed click reboots, shuts down or suspends it. Reboots of the dev device stay owner actions (ADR-0211), and the agents' guard blocks those API calls to it.
- The stand-in and the test stack cannot try a power action: the E2E specs answer the Power tab with route fakes, and the device check covers the host (ADR-0018).
- A supervisor that runs natively with `UNOS_HOST_EXEC=local` cannot reboot its own host.
- A host without logind on D-Bus cannot schedule, read or cancel: the API answers 500 with the host's message.
- Wake-on-LAN comes back only once the supervisor has started after a boot, and a renamed interface loses its choice. Turning it on drops the other wake events (`p`, `u`, `m`, `b`) that the interface had.
