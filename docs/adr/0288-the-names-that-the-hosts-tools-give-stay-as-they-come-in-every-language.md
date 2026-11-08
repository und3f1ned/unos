---
status: accepted
date: 2026-11-08
area: webui
decided-by: agent
---

# ADR-0288: The names that the host's tools give stay as they come in every language

## Context

ADR-0287 shows the values that the server answers as words through maps from the value to a key of
the catalog, and `webui/AGENTS.md` names "an action, a state, a type". System > Network shows
values that iproute2 and ss print on the host: an interface's link type (`ether`), a route's
protocol (`kernel`, `dhcp`, `ra`), a socket's protocol (`tcp`, `udp`) and state (`ESTAB`, `LISTEN`,
`UNCONN`, `TIME-WAIT`). They are the names an administrator finds in the output of `ip` and `ss` and
in their documentation, whatever the language of the page (feature `webui.translations`).

## Decision

- `webui/src/routes/system/tabs/NetworkTab.svelte` shows those names as they come in every
  language: the Type of an interface, the Protocol of a route, and the Protocol and State of a
  connection. The catalog `webui/src/lib/i18n/catalog/network.ts` has no key for them.
- The server's values that are words of the page go through maps of keys, as ADR-0287 decides:
  in System > Network the operational state in an interface's badge, beside "Disabled" (`STATES`:
  the kernel's operstate UP, DOWN, LOWERLAYERDOWN, DORMANT, TESTING, NOTPRESENT and UNKNOWN, which
  English shows as the kernel names them) and the destination `default` of a default route; in
  System > Notifications (`NotificationsTab.svelte`) the kinds of channel, the type of a notification
  (`TYPES`: info, warning, error, success) and the names and descriptions of the events of the
  server's list, by their ids (`EVENTS`). A value without a key is shown as it comes.
- A channel's events in the list of channels stay the ids that the server stores (`disk_warning`),
  in both languages, as the English page showed them.
- `webui/tests/network.spec.ts` checks the page in Russian with `tcp`, `ESTAB` and `dhcp` as they
  come, the badge's state and the default route in Russian.

## Consequences

- A Russian page shows `ether`, `kernel`, `tcp` and `ESTAB` as an English page does; the walk
  through every page in Russian (ADR-0287) does not count them as English texts.
- An interface's badge reads the state in Russian words (working, no link) where English reads UP
  and DOWN.
- Nothing has to follow the tools' vocabulary, which grows: a new socket state of ss needs no key.

## Considered options

- Translating the tools' names too (`ESTAB` as the Russian word for established): an
  administrator could no longer match them with the output of `ss` or its documentation, and a
  name without a key would still show as it comes.
