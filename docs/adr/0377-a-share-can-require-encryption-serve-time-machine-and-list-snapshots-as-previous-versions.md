---
status: accepted
date: 2026-11-15
area: shares
decided-by: agent
supersedes: [ADR-0161]
---

# ADR-0377: A share can require encryption, serve Time Machine and list snapshots as Previous Versions

## Context

ADR-0161 left per-share encryption, Time Machine and Previous Versions for a later slice. The ledger entry `shares.per-share-protocol-options` asks for all three, and the owner's words for this batch were "then we continue developing UNOS's features with our test hardware in mind", under the standing delegation "work on through the features, ask no questions; I allow everything" (in translation; as ADR-0340). The test hardware is the BPI-W3 of ADR-0376: Debian 13 with Samba 4.22 under Armbian, one btrfs pool on a hard disk. Samba keeps the old value of a parameter that disappears from a section in use at a reload (ADR-0088), but clears and reads again a section's parametric options (`fruit:`, `shadow:`) at every load. Debian's `samba` only recommends `samba-vfs-modules`, which holds the vfs modules these options need, so a host that installs no recommended packages lacks them.

## Decision

- **Stored.** Three options per share, each off by default: `encryption_required`, `time_machine` and `previous_versions` of the share API (`supervisor/src/shares/schemas.py`), in the new table `smb_share_options` keyed by the share (`SmbShareOptions` of `supervisor/src/shares/models.py`, ADR-0319). A row exists only while an option is on; it goes with its share in the same transaction (the ORM cascade). Options of a share that does not exist are deleted by the startup reconcile (`purge_orphan_options`) and by a restore's preparation, which deletes them all when the backup is not authoritative; `check_smb_tables` checks the table when a backup has it, and one made before the table restores with every option off.
- **Rendered** (`_option_lines` in `supervisor/src/shares/smbconf.py`), before the fixed lines of every section: always `smb encrypt = required` or `default` and one `vfs objects` line (empty when no option needs a module), so that turning an option off changes a line (ADR-0088). The modules compose in this order: `catia fruit streams_xattr`, then `shadow_copy2 btrfs`. `smb encrypt` is the share parameter that every Samba knows (from 4.14 a synonym of `server smb encrypt`, which ADR-0091 tolerates as unknown only for `[global]`). An unchanged state renders byte-identical text (ADR-0082).
- **Encryption required.** A client that cannot encrypt (SMB 2.1 and older, guests) is refused at the tree connect. Turning it on closes the share's open tree connects (`shrunk_shares`, ADR-0103): a tree connect keeps what it was granted. A guest share cannot require encryption: create and PATCH refuse the pair with 400 (`GUEST_ENCRYPTION`); a restored row with both renders the requirement.
- **Time Machine.** `fruit:time machine = yes` in the share's section. While a Time Machine share exists, every section loads `catia fruit streams_xattr`, since macOS negotiates Apple's SMB2 extensions on its first tree connect (vfs_fruit(8)), and `[global]` ends with `fruit:aapl = yes` and `fruit:nfs_aces = no`, so that a Mac cannot change the modes of UNOS's share folders (ADR-0150). No size limit is offered: Time Machine takes the free space it sees.
- **The advertisement.** smbd registers `_adisk._tcp` for its shares with `fruit:time machine` itself, beside `_smb._tcp` (`multicast dns register = yes`, ADR-0275), when it starts and when avahi-daemon restarts, never at a reload. UNOS writes no avahi service file of its own; after an apply whose reload reached smbd and whose announced Time Machine shares (`smbconf.time_machine_shares`: the sections with `fruit:time machine` that are available, as smbd's `lp_snum_ok` asks, so that a share that gets or loses its last access counts too) differ from the file before, it runs `systemctl --no-block restart avahi-daemon` (`smb._restart_avahi`), so that smbd registers the new set; a failure is a warning of the answer and is tried again at the next apply.
- **Previous Versions.** `shadow_copy2 btrfs` with `shadow:mountpoint` set to the share's pool, or to its volume when the folder is in one, `shadow:snapdir = .snapshots`, and `shadow:snapprefix = ^\(snap\|auto\)$`, `shadow:delimiter = _`, `shadow:format = _%Y%m%d_%H%M%S`: the names of the Snapshots page and of scheduled snapshots, in UTC (ADR-0243). `snapshot_root` of `supervisor/src/shares/service.py` walks the folder below its pool without following links: a part that starts another file tree right below the pool is a volume; one deeper (a restored copy, a subvolume made by hand, a mount point) is in no snapshot of UNOS.
- **Validated when stored and when rendered** (ADR-0144). The modules are checked on the host with one command (`smb.VFS_CHECK`: `vfs/<module>.so` in the `MODULESDIR` of `smbd -b`). Turning Time Machine or Previous Versions on is refused with 400 that names `samba-vfs-modules` when the host lacks a module, and Previous Versions with the reason where no snapshot of UNOS holds the folder; a check that cannot run answers 500. When rendered, an option that the host or the folder cannot serve is left out and `GET /shares/smb-status` names it; a module check that cannot run renders the options as stored, with a log line.
- **The import** (ADR-0086) is unchanged for files that UNOS did not render. In a file with UNOS's header, the option lines that UNOS renders (`smbconf.is_unos_option_line`: the `vfs objects` lines it composes, `fruit:time machine = yes`, the `shadow:` lines, `shadow:mountpoint` with any path) are its own: they come back as the options, without notes; another value of those keys is noted as not kept, as in any file, and a guest share that requires encryption comes back without guests.
- `deploy/install.sh` and the manual installation of `docs/INSTALL.md` install `samba-vfs-modules`; `docs/INSTALL.md` names `_adisk._tcp` beside `_smb._tcp` among the services of avahi-daemon. The Shares page offers the three options in the share dialog and shows them on the share's card; the audit entries name them.
- Tests: `supervisor/tests/test_share_options.py`, `test_smbconf.py`, `test_share_import.py`, `test_smb_models.py`, `test_smbconf_testparm.py` (every combination, with the device's testparm), `webui/tests/shares.spec.ts`, and the smb area of `scripts/dev/scenario.py` with `supervisor/tests/test_dev_scenarios.py`.

## Consequences

- Windows' Previous Versions and smbclient's `@GMT-` paths list the snapshots of the share's pool or volume; snapshots of other names are not listed, and a share inside a nested subvolume cannot have them.
- While a Time Machine share exists, every share stores Windows' alternate data streams in extended attributes (streams_xattr) and hides Apple's `._` files; each change of the Time Machine shares re-announces every Bonjour service of the host.
- A Mac's backup is a step by hand of the device check; the smb area shows the lines, encryption with an SMB 2.1 and an SMB3 client, and a snapshot listed and gone.
- Turning encryption on disconnects the share's open connections; clients reconnect, with encryption.
- A host without `samba-vfs-modules` refuses Time Machine and Previous Versions until the owner installs it.

## Considered options

- A static avahi service file for `_adisk._tcp`: smbd registers the same type under the same name at its start, and a collision in its entry group would also take `_smb._tcp` down (ADR-0275 left out a static file for that service too).
- Restarting smbd for a change of the Time Machine shares: it would end every open connection to every share.
- `vfs objects` in `[global]`: a section's own line replaces it, and every section writes one.
- `fruit:time machine max size`: Samba counts only the sparse bundles in the share; left for a later entry.
