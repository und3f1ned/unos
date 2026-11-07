---
status: accepted
date: 2026-11-07
area: system
decided-by: agent
---

# ADR-0234: An interface that carries VLANs is not disabled, and a disable's saved state and the page's reports follow what the host shows

## Context

The automatic revert of ADR-0232 saves and restores the state of the named interface only, but the kernel takes the VLANs on a link down with it, so a connection through a VLAN would stay lost after the revert. The saved state of a kept disable outlived the disable once the interface was on again, so a later Enable could add old addresses and routes to a renumbered interface. A Keep whose host command timed out said that the interface comes back on, though the command's first step may have kept it, and the page said nothing when the revert removed its marker but could not turn the interface on.

## Decision

- `POST /api/v1/network/interfaces/{name}/down` answers 409, before it changes anything, for an interface that VLANs are on (`ip -d -j link show type vlan`: an entry whose `link` is the interface), unless they have loose binding (`LOOSE_BINDING` among their flags). The kernel's 8021q module turns such VLANs off when their link goes down, and turns every one of them on when it comes up, also one that was off, with only the routes of their IPv4 networks. When the VLANs cannot be read, the interface stays enabled (500). A VLAN itself is disabled like any other interface (`supervisor/src/network/links.py`).
- The saved state in `/run/unos/network/<name>/` is put back only while its disable is in effect. Wherever UNOS finds the interface on again, in an Enable of an enabled interface or a Keep of one that was turned back on, the host script `unos-network-forget` calls the revert off and discards the saved state, unless the revert is restoring it at that moment (it removed the marker first, and its unit is active). A Disable replaces the saved state, and a restore consumes it.
- A Keep whose cancel fails or does not answer in time reads the marker again (`test -e`): the cancel's first step, the removal of the marker, is what keeps the disable, and a timeout does not stop the command on the host. A marker that is still there answers 500 "could not be kept, so it comes back on by itself"; a marker that is gone keeps the disable (200) while the interface is off, and answers 409 when the revert came first and the interface is on; a marker that cannot be read answers 500 that the outcome is not known.
- When the page's time to keep a disable has run out, the page reports, 5 seconds later, what the host shows: back on; still counting (the countdown and Keep again); still off with nothing waiting to be kept, because the revert could not turn it on or the change was kept elsewhere, with Enable as the way out; no longer listed; or that the interfaces could not be read. A Keep whose answer is lost claims no outcome, and says that the interface stays disabled once the interfaces show it kept. The newest countdown comes first, so that the Tab after a Disable reaches the Keep of the interface just disabled (`webui/src/routes/system/tabs/NetworkTab.svelte`).
- Tests: `supervisor/tests/test_network.py` (a fake host whose VLANs go down and up with their link), `supervisor/tests/test_network_scripts.py` (the forget script under sh and dash), `webui/tests/network.spec.ts`.

## Consequences

- An interface that carries VLANs is disabled on the host, not from UNOS; its VLANs can still be disabled one by one.
- An interface that is turned on and off again outside UNOS between a kept disable and an Enable from UNOS, without UNOS finding it on in between, still gets the state of that disable back: UNOS does not see what happened meanwhile.
- After the time has run out, a disable kept elsewhere and a revert that could not turn the interface on read alike on the page; the journal of the revert's unit (`journalctl -u unos-network-revert-<name>`) tells them apart.

## Considered options

- Saving and restoring the VLANs together with their link: the revert's command would carry a list of VLANs fixed at the disable, and the kernel turns on every VLAN of the link, also one whose own disable was kept.
- A marker of a failed revert that the listing reports: a new field and a state to clear, for a case that the page already reports with Enable as the way out.
- Telling at Enable whether the interface was on in between, from its traffic or carrier counters: not reliable for tunnels, VLANs and links without a cable.
