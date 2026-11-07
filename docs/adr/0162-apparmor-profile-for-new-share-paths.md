---
status: accepted
date: 2026-11-07
area: shares
decided-by: agent
---

# ADR-0162: UNOS does not manage smbd's AppArmor profile while smbd runs unconfined

## Context

The smbd unit of the device runs `/usr/share/samba/update-apparmor-samba-profile` before
smbd starts. If smbd is confined by AppArmor, a share path that UNOS adds while smbd runs
therefore reaches its profile only when smbd starts again. During slice 2 this ADR proposed
that UNOS refresh the profile after every change of share paths if the device's smbd is
confined, and waited for that to be known: a read-only check before the slice 2 deploy found
AppArmor enabled and no smbd entry in the output of `aa-status`, without showing that
`aa-status` was installed.

A definite read-only check of the device on 2026-11-07 found: AppArmor is enabled in the
kernel (`/sys/module/apparmor/parameters/enabled` is `Y`), the AppArmor tools are not
installed (there is no `aa-status`), no smbd or samba profile is loaded
(`/sys/kernel/security/apparmor/profiles`), and smbd runs unconfined
(`/proc/<smbd pid>/attr/current`).

## Decision

- While smbd runs unconfined, as on the dev device, a new share path needs no AppArmor step:
  UNOS runs no AppArmor tool and changes no profile.
- What UNOS does on a host that confines smbd (run `update-apparmor-samba-profile` and
  `apparmor_parser -r` after a change of share paths, so that smbd serves a new path without
  a restart) is the feature-list entry `shares.apparmor-profile`, decided when such a host is
  supported.

## Consequences

- No code changes; the dev device serves a new share path at once.
- On a host whose smbd is confined, a share path that UNOS adds works only after smbd
  restarts, until `shares.apparmor-profile` is done.
- If the device's smbd becomes confined, for example after the AppArmor tools and a Samba
  profile are installed, the read-only check is repeated: it reads only `/sys` and `/proc`.
