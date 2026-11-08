---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0261
---

# ADR-0158: NetBIOS stays until a discovery slice adds WS-Discovery and Bonjour

## Context

Superseded by ADR-0261: wsdd2 and avahi-daemon announce the NAS, and NetBIOS is off.

Clients find the NAS by name through a discovery protocol. Slice 2 (ADR-0059) installs no
WS-Discovery (wsdd) or Bonjour (avahi) service, so NetBIOS, served by nmbd, is the only one
the NAS has.

## Decision

nmbd and NetBIOS stay: `deploy/install.sh` enables and starts smbd and nmbd, and the
rendered `[global]` leaves NetBIOS on, with `dns proxy = no` (`smbconf.global_settings`). No
wsdd or avahi is installed. A later discovery slice adds WS-Discovery (wsdd2) and Bonjour
(avahi) and then turns NetBIOS off (`disable netbios = yes`, `smb ports = 445`).

Implemented in `supervisor/src/shares/smbconf.py` and `deploy/install.sh`; tested in
`supervisor/tests/test_smbconf.py` (TestGlobal).

## Consequences

nmbd keeps running and answering NetBIOS on the LAN until that slice; its cache lives in RAM
(ADR-0094).
