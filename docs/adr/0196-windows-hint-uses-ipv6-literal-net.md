---
status: accepted
date: 2026-11-07
area: webui
decided-by: agent
---

# ADR-0196: The Windows connection hint uses the ipv6-literal.net form for IPv6 hosts

## Context

The Account card and the share cards tell users how to connect, using the host name the browser reached the NAS by. When that is an IPv6 address, the browser gives it with brackets (`[fd00::103]`), and the Windows hint `\\[fd00::103]\share` does not work: Windows takes an IPv6 address in a UNC path only in the ipv6-literal.net form.

## Decision

- `windowsAddress` in `webui/src/lib/components/shares/texts.ts` writes a bracketed host as `<address>.ipv6-literal.net`, with ':' replaced by '-' and a zone's '%' by 's' (`\\fd00--103.ipv6-literal.net\share`). The Account card (`SmbAccessCard`) and the share cards (`ShareCard`) use it.
- `smb://` URLs (macOS Finder, Linux file managers) and the Linux mount line keep the bracketed address.
- `docs/USER_GUIDE.md` documents the form.
- Tests: "connection hints of a page opened by an IPv6 address" in `webui/tests/shares.spec.ts`, which runs only on a stand-in on the same machine, reached as `[::1]`.

## Consequences

- Windows users who open the web UI by an IPv6 address get a hint they can paste.
- The form has not been checked on a real Windows client yet (ADR-0195).
