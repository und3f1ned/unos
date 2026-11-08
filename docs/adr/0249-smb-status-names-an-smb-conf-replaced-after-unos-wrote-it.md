---
status: accepted
date: 2026-11-07
area: shares
decided-by: agent
supersedes: [ADR-0130]
---

# ADR-0249: SMB cannot be turned on while Samba serves an smb.conf that UNOS did not write, and the status names one replaced after UNOS wrote it

## Context

ADR-0130 refused activations while the live smb.conf does not start with `smbconf.HEADER`, but gave
`GET /api/v1/shares/smb-status` no line for a file replaced after a successful apply: an
administrator learned of it only from a refused activation (ADR-0195,
shares.status-names-foreign-smb-conf). Such a file may give every Samba account access to its
shares, for example through a share without `valid users`; besides a hand edit, a Samba package
upgrade that installed the maintainer's smb.conf would replace it.

## Decision

The activations, as in ADR-0130:

- Every activation (the user's own, an administrator's with a password, user creation with
  `smb_enabled`) checks that the live smb.conf starts with `smbconf.HEADER`
  (`accounts._require_unos_smb_conf`), right after the tool check, so that a host without Samba
  still answers 503 first, and before anything is written. Otherwise it answers 409:
  - An administrator gets "SMB cannot be turned on until UNOS has installed its own smb.conf:
    REASON", the reason in this order: the file could not be read; "smb.conf was not updated:
    MESSAGE" with the message of the last apply; the last import problem; "it was replaced or
    removed after UNOS wrote it; it is written again at the next change or restart" (the last apply
    succeeded); "UNOS has not written it yet; it is written at the next change or restart".
  - A regular user gets the fixed text "SMB cannot be turned on until UNOS has installed its own
    smb.conf; ask an administrator" (ADR-0131).
  - User creation with `smb_enabled` then creates the user without SMB, with the warning "User
    created, but SMB access could not be turned on: DETAIL".
- Allowing, revocations and password changes of accounts that are already active are never blocked.

The status, new:

- When the last apply left UNOS's file in place (`last_apply().ok` is true: it installed the file
  or found it unchanged), `GET /api/v1/shares/smb-status` reads the live smb.conf and judges it as
  the activations do, by its header (`service._live_conf_problem`). A file without the header, or
  none, is the problem "smb.conf was replaced or removed after UNOS wrote it; it is written again at
  the next change or restart, and SMB cannot be turned on until then" (`service.SMB_CONF_REPLACED`);
  a file that cannot be read is "smb.conf could not be read: REASON".
- Before the first apply since the start, and after a failed one, which the status names as "smb.conf
  was not updated: ...", there is no such line. The status only reads: the next change or start
  writes UNOS's file again.

Implemented in `supervisor/src/shares/accounts.py` and `supervisor/src/shares/service.py`; tested in
`supervisor/tests/test_smb_accounts.py` (TestActivationNeedsUnosSmbConf),
`supervisor/tests/test_smb_integration.py` (SMB is turned on only once UNOS wrote smb.conf; smb.conf
replaced after UNOS wrote it; the status names it), `supervisor/tests/test_share_api.py`
(TestStatus), `webui/tests/users.spec.ts`, `webui/tests/account-smb.spec.ts` and
`webui/tests/shares.spec.ts`.

## Consequences

- Activations wait until UNOS has installed its own file, and the Shares page names a replaced or
  removed file as soon as it loads the status, before anyone tries to turn SMB on.
- A hand edit that keeps UNOS's header is noticed neither by the activations nor by the status;
  the next apply rewrites the file and keeps share sections that UNOS did not write in
  `smb.conf.unos-replaced` (ADR-0092).
- Each status request reads smb.conf once, in a worker thread.

## Considered options

- Compare the live file with the text of the last apply, which would also notice edits that keep
  the header: not taken. An apply that installs a new file while the status reads it would be
  reported as a replacement, unless the status read under `smb_lock()`, which the startup reconcile
  can hold for a long time.
