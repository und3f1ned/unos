---
status: accepted
date: 2026-11-06
area: testing
decided-by: agent
---

# ADR-0189: SMB code is tested against a fake host layer, real testparm and the local stand-in

## Context

The SMB code runs Samba's and the shadow suite's tools on the host through `src/host.py` (ADR-0020). Those tools cannot run on the macOS workstation, the device must not be worn by testing (ADR-0214), and the E2E specs create and delete users and shares (ADR-0215). Tests with invented server texts or impossible flows had passed without covering the real ones.

## Decision

- Backend tests run against `FakeSambaHost` (`supervisor/tests/samba_fake.py`), an in-memory host behind `src.host.run_on_host`: accounts, groups, passdb with tdbsam's lower-cased keys, smbd sessions and `smbcontrol <pid> shutdown`, `mkdir /run/samba`, testparm and the reloads. It records every command, fails the test on a command it does not know, and `install_fake_samba_host` also blocks real subprocesses.
- `supervisor/src/shares/smb.py` calls the host through the module object (`from .. import host`, then `host.run_on_host`), so that every patch of `src.host.run_on_host` sees its testparm, reload and close-share calls.
- Unit tests stay quiet: `quiet_loops` in `supervisor/tests/test_pool_persistence.py` replaces the startup reconcile, unit tests of the service mock `apply_smb_config` and assert that it was awaited, and the content of smb.conf is asserted in `supervisor/tests/test_smb_integration.py`. On Linux, `supervisor/tests/conftest.py` replaces the pool-root mount check, because the tests' pools are not mount points; the tests of that check call the real function.
- The rendered smb.conf is checked by a real testparm where one is installed (`supervisor/tests/test_smbconf_testparm.py`; from the workstation through the device, ADR-0187), and Linux-only tests (O_PATH, ACLs, mountinfo) run in the Linux runner.
- E2E runs only against local targets (ADR-0215): the stand-in, and the compose test stack of `scripts/test-cycle.sh`, both with `E2E_STAND_IN=1`. The specs' route fakes (`webui/tests/shares-fakes.ts`) mirror the server's rules (access entries, version bumps, the username guards) and use the server's exact texts, because the UI shows them verbatim.
- Every new rule gets a test that fails on the code before it (ADR-0186).

## Consequences

- smbd itself, with real sessions, is exercised only by the device check (ADR-0018), and testparm checks the configuration; a Samba-capable test container with smbclient for E2E is a later item.
- The fake must follow the real tools: a change in how UNOS calls a tool changes the fake in the same change.

## Considered options

- An autouse conftest fixture that replaces the startup reconcile in every test, instead of `quiet_loops`: equivalent, not chosen.
- E2E against a Samba-capable test container: left for later.
