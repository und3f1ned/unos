---
status: accepted
date: 2026-11-07
area: system
decided-by: agent
---

# ADR-0244: A disabled network interface comes back on by itself after 60 seconds unless the change is kept

## Context

System > Network disabled an interface at once with `ip link set <if> down`, so disabling the interface that carries the web UI cut the owner off with nothing to turn it back on: the safeguard that ADR-0035 lists as open (feature-list entry `system.network-disable-confirmation`). `ip link set <if> up` alone does not repair that either: the kernel drops the routes through a link that goes down, the default route among them, and the link's IPv6 addresses, and only a network manager or a DHCP client might put them back.

## Decision

- The page asks first: a dialog (`Modal`, ADR-0197) names the interface and warns that the connection to UNOS may drop. After a Disable it counts down and offers "Keep <interface> disabled"; the focus moves to the countdown, one Tab away from that button (`webui/src/routes/system/tabs/NetworkTab.svelte`). When the page's time runs out, it reads the interfaces again 5 seconds later: an interface that is back on is reported as such, a host that still counts gets its countdown and Keep again, and one that stays off is reported with Enable as the way out.
- `POST /api/v1/network/interfaces/{name}/down` (`supervisor/src/network/links.py`, through `src/host.py`) saves the interface's addresses (`ip addr save`) and its routes of the main tables (`ip -4 route save`, `ip -6 route save`) in `/run/unos/network/<name>/` on the host, with a marker `pending` that holds the time of the revert; arms a transient systemd timer on the host, `systemd-run --unit=unos-network-revert-<name> --on-active=60` (accuracy 1 s, unloaded once it ran); and only then runs `ip link set dev <name> down`. It answers `revert_in: 60`. When the state cannot be saved or the timer cannot be armed, the interface stays enabled (500): it is never disabled without its revert. When the disable itself fails, the revert is called off; when its outcome is unknown (no answer in time), the revert stays armed. While the revert of an earlier disable of the same interface still runs, a new disable is refused (409), so that it cannot replace the state that revert restores.
- After 60 seconds the timer runs the revert: `ip link set dev <name> up`, then `ip addr restore` and `ip route restore` from the saved files, up to 5 attempts 2 seconds apart, because a route whose source is a restored IPv6 address fails while the address is tentative. iproute2 skips what exists already, so a network manager that was quicker does no harm.
- `POST /api/v1/network/interfaces/{name}/down/confirm` keeps the change: it removes the marker and stops the timer. The revert and the confirmation both start by removing the marker, and only one of them can: a confirmation that comes too late answers 409, and a revert after a confirmation does nothing. An interface that someone turned back on outside UNOS has nothing to keep (409).
- Enable (`POST .../up`) restores the saved addresses and routes the same way, so a kept disable followed by Enable brings the interface back as it was, and calls off a pending revert once the link is on. While it is not (the restore could not turn it on, or did not answer), the revert stays armed and tries again at its time. Enable leaves an interface that is enabled already as it is.
- `GET /api/v1/network/interfaces` reports `revert_in` for each interface whose disable waits to be kept, and `revert_seconds`, so the page shows the countdown again after a reload, and the dialog states the time.
- The timer and the saved state live on the host: the revert runs also when the supervisor restarts or when the connection went through the disabled interface. `/run` is a tmpfs, so nothing is written to the system disk but the journal lines of the transient unit; a reboot clears the state and brings every interface up as the host's network configuration says.
- The revert's script is the command of the transient unit, and systemd expands `${NAME}` and `$$` in a command's arguments: the scripts contain neither. The scripts are POSIX sh, for dash.
- The loopback and Docker's interfaces (`lo`, `docker0`, `dummy0`, `veth*`, `br-*`) are neither listed nor changed (400): disabling `br-*` would cut the web UI from the supervisor.
- Tests: `supervisor/tests/test_network.py` (the API against a fake host), `supervisor/tests/test_network_scripts.py` (the scripts under /bin/sh and dash, with stub tools), `webui/tests/network.spec.ts` (route fakes and the page's clock).

## Consequences

- Disabling the interface that carries the web UI cuts the connection for about a minute; such a change cannot be kept from the page, because the confirmation needs that connection.
- A host whose PID 1 is not systemd cannot disable interfaces from UNOS.
- A restore puts back the state saved at the disable: addresses with the lifetimes they had then, and routes learned from router advertisements as routes without an expiry time.
- A network manager that brings links up by itself can undo a kept disable; the device check shows what the dev device's does.

## Considered options

- A timer in the supervisor: lost when the supervisor restarts, and it would run in the container.
- `ip link set up` alone: leaves the host without its default route and its IPv6 addresses.
- Reapplying the configuration through the host's network manager (networkctl, nmcli, ifup): depends on which one the host runs, and a host may run none.
- A longer time: a lockout would last longer; a shorter one: too little time to read the page and press Keep.
