---
status: accepted
date: 2026-11-05
area: process
decided-by: agent
---

# ADR-0018: Storage, share and host changes are done only after a device check, a reboot and API tests

## Context

The changes of February 2026 never ran on hardware, and the reboot is exactly where pool persistence failed in 1.0.0 (ADR-0034): after a restart the pool was not mounted, and shares and apps wrote to the SD card. Unit tests with mocks did not see it.

## Decision

A change that touches storage, shares or the host is done only when:

1. it is deployed to the dev device, or to a VM with loop disks;
2. its scenario has been checked by hand or by an E2E test;
3. it has been checked again after a reboot;
4. an HTTP API test with role checks covers it (the role matrix of `supervisor/tests/test_authz.py` covers every route for anonymous users, regular users and administrators).

## Consequences

- Phase 0 met all four: its smoke test passed 18 of 18 before and after a reboot of the device. Slice 1 was deployed and checked without a reboot, and slice 2 left the reboot to the owner's consent while the device was treated as a production NAS (ADR-0182).
- A reboot of the device is an owner action, and the agents' guard blocks reboot commands sent to the device (ADR-0211): the agent asks the owner for it.
- A feature of `feature-list.json` that changes the host passes only after this check (ADR-0203).
