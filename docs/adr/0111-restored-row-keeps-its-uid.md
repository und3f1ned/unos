---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0112
---

# ADR-0111: In a restore carry-over the restored row keeps its uid

## Context

Superseded by ADR-0112: a restored row never takes a uid that a live account holds.

A restore can bring back a database whose SMB accounts differ from the live ones. When the
restored database is not authoritative for SMB, the live accounts are carried over into it
by username (ADR-0141), and a restored row and a carried-over live account can claim the
same uid. SMB uids own files on the pools (ADR-0109), so the carry-over has to decide who
keeps the uid.

## Decision

In the carry-over, a restored row that holds a uid keeps it, and the live account is carried
over without a uid; it gets a fresh one at its next activation. A test pinned this order.

## Consequences

A backup of another installation, whose uids also start at 70000, or a crafted archive could
give the restored user the uid of a live account of another name, and with it the files that
account owns on the pools. ADR-0112 replaced the rule, and the test was inverted.
