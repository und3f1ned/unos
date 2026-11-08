---
status: accepted
date: 2026-11-08
area: shares
decided-by: agent
supersedes: [ADR-0037]
---

# ADR-0262: Share and export values are validated before they reach smb.conf or an export file, and an NFS export from the web interface is an existing folder for one local network, read-only or read-write

## Context

ADR-0037 let an NFS export name every location, `*` or a host name as its clients and any known option, as typed. NFS with AUTH_SYS asks for no password: whoever reaches the server is trusted for the user ids it claims, `*` takes in the internet wherever the host has a global IPv6 address and no filter (ADR-0227), and `no_root_squash` or `insecure` give a client's root or any of its users more. exportfs resolves symbolic links in an export's path. The SMB rules of ADR-0037 stay as they are.

## Decision

- Values written into `smb.conf` are validated against directive injection. smb.conf has no quoting, so a line break, a NUL byte or a trailing backslash in any value is refused, and every section name must be a valid share name (ADR-0038); the serializer guards `_safe_value`, `_safe_path` and `_safe_name` of `supervisor/src/shares/smbconf.py` check every value once more before it is written. Share paths must lie strictly inside the pools base, without `..` components, and must not pass through symbolic links (`validate_pools_path` and `validate_share_path` in `supervisor/src/security/validators.py`); a share path also refuses `%`, `"`, `\` and leading, trailing or repeated whitespace, which Samba would read differently. `smb.conf` is replaced atomically, and only after the host's `testparm` accepts the new file (ADR-0089); when the host has no `testparm`, the file is installed with a warning in the log.
- An NFS export added in the web interface (`NfsExportCreate` in `supervisor/src/shares/schemas.py`, checked by `supervisor/src/shares/nfs.py`) has a folder, one client and an access, and UNOS writes the rest:
  - The client is one IPv4 or IPv6 address or network, written as Python's `ipaddress` writes it (a network with its prefix length, an address without one), inside the networks that SMB answers (`HOSTS_ALLOW` of `smbconf.py`, ADR-0095). `*`, host names, wildcards, netgroups and other addresses are refused (400).
  - The folder lies inside the pools base without symbolic links (`validate_pools_path`), has only letters, digits and `._/-` in its path, so that an export file needs no escaping for it, at most 1024 bytes, which exportfs reads, and is neither a pool's top level, `apps/` or below, a snapshot folder nor the trash (400). Its pool is one that UNOS manages (ADR-0263; 503 when the pool list cannot be read) and is mounted: the pool's root is on the pool's mount, checked on its descriptor as for a share folder (ADR-0152; 409). It exists: UNOS creates no folder for NFS, since its owner and mode decide what the clients' user ids can do (400 otherwise). Every folder above it, from the pool's top level, can be changed by root only (409, with the folder and the command of ADR-0153's check): exportfs resolves symbolic links, so whoever could replace the folder would choose what is exported. exportfs resolves them again at every run, after each change in UNOS and at every start of the NFS server, so `GET /shares/nfs-status` checks the folders above every export of UNOS once more, as for SMB shares (ADR-0153): it names each one that other accounts can change, with the command to run, and each export whose folders could not be checked. Exports that UNOS did not write are the owner's, and not checked.
  - The access is `read` or `write`. UNOS writes `ro` or `rw`, then `sync,no_subtree_check,root_squash,secure` and `mp=` with the pool's mountpoint, which makes exportfs and mountd export the folder only while the pool is mounted. No option is typed.
  - UNOS's own export file (ADR-0261) has one export of UNOS per folder (409), and a folder that another entry exports, in another export file or written by hand into UNOS's, to whatever client, is refused too (409): mountd answers a client from one entry of the folder, chosen by the kind of client (a host name before a network) and by the order of the files, so UNOS could not tell which entry applies.
- Shares > NFS offers Location (the pools that UNOS manages and their volumes), Folder, Client network and Access (Read only by default), and says that NFS asks for no password (`NfsExportFormModal.svelte`, `NFS_NOTE` in `webui/src/lib/components/shares/texts.ts`).
- Tests: `supervisor/tests/test_validators.py`, `supervisor/tests/test_smbconf.py`, `supervisor/tests/test_smbconf_testparm.py`, `supervisor/tests/test_share_api.py` (share values); `supervisor/tests/test_nfs.py` (TestCreate; TestList for the status of the folders above an export) and `webui/tests/nfs-exports.spec.ts` (exports).

## Consequences

- Since slice 2, UNOS renders the whole `smb.conf` from its database (ADR-0074), and these checks guard every value it renders. Without `testparm` on the host a broken file is caught only by Samba itself; `deploy/install.sh` installs samba-common-bin, which provides it.
- An export with other clients or options (a backup client with `no_root_squash`, Kerberos, a whole pool) is written by hand into another export file on the NAS; UNOS lists it and keeps it. A folder whose name has a space or another character outside `._/-` cannot be exported from the web interface.
- Every computer in the client network can claim any user id: the folder's owner and mode decide what it reads and writes, and root on a client counts as nobody.
- An export of UNOS below a folder that has become changeable by other accounts stays, with its status line, as an SMB share does (ADR-0153). Refusing UNOS's next change, or skipping its exportfs, while one is unsafe was not chosen: the NFS server runs exportfs at every start all the same, and the refusal would block the delete of that very export.
- `no_subtree_check`, nfs-utils' default, lets a client that forges file handles reach files of the folder's filesystem outside the folder. Whether UNOS writes `subtree_check` instead, which costs stale handles when files are renamed, or exports volumes only, is the owner's call.
