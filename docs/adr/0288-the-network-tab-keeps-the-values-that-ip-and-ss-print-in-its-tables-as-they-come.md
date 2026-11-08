---
status: accepted
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0288: The Network tab keeps the values that ip and ss print in its tables as they come in every language

## Context

ADR-0287 shows a value that the server answers as a word through a map from the value to a key of
the catalog. The Network tab of the System page (`webui/src/routes/system/tabs/NetworkTab.svelte`)
shows in its tables and in an interface's details what iproute2 and ss print on the host: an
interface's link type (`ether`), a route's protocol (`kernel`, `dhcp`, `ra`), a socket's protocol
(`tcp`, `udp`) and state (`ESTAB`, `LISTEN`, `UNCONN`, `TIME-WAIT`). They are the tools' own terms,
without common Russian names, which an administrator compares with the output of `ip` and `ss` and
looks up in their documentation (feature `webui.translations`).

## Decision

- The Network tab shows those values as they come in every language: an interface's Type, a
  route's Protocol, and a connection's Protocol and State, each under a heading that names the
  tool's field. The catalog `webui/src/lib/i18n/catalog/network.ts` has no key for them.
- What the tab says in its own words goes through maps of keys, as ADR-0287 decides, also where a
  tool printed the word first: the state in an interface's badge, the tab's summary of the
  interface beside its own "Disabled" and coloured by it (`STATES`: the kernel's operstate UP,
  DOWN, LOWERLAYERDOWN, DORMANT, TESTING, NOTPRESENT and UNKNOWN, which English shows as the kernel
  names them), and the destination of a default route, which the tab recognises itself (an empty
  destination or ip's `default`) and names `default` in English. A state without a key is shown as
  it comes.
- `webui/tests/network.spec.ts` checks the tab in Russian with `tcp`, `ESTAB` and `dhcp` as they
  come, and the badge's state and the default route in Russian.

## Consequences

- A Russian Network tab shows `ether`, `kernel`, `tcp` and `ESTAB` as an English one does; the walk
  through every page in Russian (ADR-0287) does not count them as English texts.
- Nothing has to follow the tools' vocabulary, which grows: a new socket state of ss needs no key.
- This covers those columns of the Network tab alone. A value of a host tool on another page, such
  as a SMART result or a pool's state, follows ADR-0287 unless a decision of its own says otherwise.

## Considered options

- Translating these values too (`ESTAB` as the Russian word for established): an administrator
  could no longer match them with the output of `ss` or its documentation, and a value without a
  key would still show as it comes.
- Keeping the badge's state as it comes as well, since `ip link` prints it (`state UP`): the badge
  is read at a glance as the tab's own verdict on the interface, next to its "Disabled", not as a
  field of the tool's output.
