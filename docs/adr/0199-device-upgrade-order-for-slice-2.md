---
status: superseded
date: 2026-11-07
area: device
decided-by: agent
superseded-by: ADR-0220
---

# ADR-0199: The device is upgraded to slice 2 in a fixed order

## Context

Superseded by ADR-0220: the dev device keeps no legacy state, and its guest-era setup was cleaned up directly.

Upgrading the device from slice 1 to the SMB slice met several rules at once: SMB cannot be turned on while Samba serves an smb.conf that UNOS did not write (ADR-0130); Samba's cache moves to RAM only at a restart (ADR-0094); the pool root's guest-era mode 0777 blocks new share folders and Repair below it (ADR-0153) while UNOS changes no existing mode (ADR-0154); a login's two-factor code cannot be used again (ADR-0067); and every failed SMB sign-in writes passdb.tdb (ADR-0159).

## Decision

The device was to be upgraded in this order, and the chapter "Updating from 1.0.0 or Earlier" of `docs/INSTALL.md` followed it:

1. Deploy, then check that `head -n 1 /etc/samba/smb.conf` is UNOS's header and that the Shares status shows neither "Shares were not imported from smb.conf: ..." nor "smb.conf was not updated: ...".
2. Restart Samba once while nobody is connected (`systemctl restart smbd nmbd`, or reboot).
3. `sudo chmod 755 /mnt/pools/data`, run by the owner.
4. Allow SMB for a regular account; the user turns it on with a password of at least 8 characters and a two-factor code newer than the login's.
5. Give DATA a folder (`shared`, created as root:unos-smb 2770).
6. Move the existing files into it, then run Repair permissions on DATA.
7. Update the clients, and their saved passwords after every password change.
8. For a rollback, follow the downgrade notes (ADR-0181).

## Consequences

- Several steps depended on earlier ones (activation needs UNOS's smb.conf, a new folder and Repair need the chmod), and steps 3, 5 and 6 changed the owner's data, so they waited for the owner (ADR-0182).
- The rollback step relied on documentation, not code (ADR-0181).
