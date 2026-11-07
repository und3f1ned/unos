---
status: accepted
date: 2026-11-07
area: system
decided-by: agent
---

# ADR-0247: Waking a host that is off by Wake-on-LAN depends on its board, and the Power tab says so

## Context

ADR-0233 made System > Power turn Wake-on-LAN on and off on the host's network cards. On 2026-11-07 the dev device (an RK3588 board, its RTL8125B on PCIe, driver r8169) had `Wake-on: g` armed and was shut down from the API; magic packets sent from the workstation on the same subnet, as UDP broadcasts to the subnet and to 255.255.255.255, did not start it. They were sent to the address that systemd gives the card, to the random address the driver picked at that boot (the card has no permanent address of its own), and to 00:00:00:00:00:00, which the driver restores at shutdown when there is no permanent one. The board cuts the network card's power when the host is off, as many single-board computers do.

## Decision

- UNOS keeps turning Wake-on-LAN on and off on the host's interfaces and setting the choice again at each start (ADR-0233).
- The Power tab and `docs/USER_GUIDE.md` say that a magic packet starts a host that is off only where the board keeps the network card powered then; the shutdown dialog says that it starts again with its power button, or with a Wake-on-LAN packet where the board supports that.
- The step of `system.power` about the magic packet now asks for that statement instead of a wake, which depends on hardware that UNOS cannot change.

## Consequences

- The dev device starts again only with its power button after a shutdown; a reboot is the way to restart it remotely.
- Whether a board can wake from power-off is not detected: no interface reports it, so the tab cannot tell before a test.
