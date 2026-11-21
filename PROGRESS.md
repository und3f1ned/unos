# Progress journal

Where the work stands, what runs on the dev device, and a dated log, newest first (ADR-0204). At
the start of a session read it together with `git log --oneline -10`; update it after each
significant step. Features and their state live in `feature-list.json`, decisions in `docs/adr/`.

## Now

- `main` holds the first and second feature batches (#32 to #51) and the Wake-on-LAN statement
  (#42, ADR-0247). The second batch: the trash, notification channels with TLS for SMTP, the logs
  of the host, the Images view, the scheduler's command output and the host's time zone, smb.conf
  re-applied when a pool goes, sign-in with a full system disk and API token fixes, and tests that
  prove the first-run setup, two-factor sign-in, the disk list, mounts, forget and scrub.
- Done in the night of 2026-11-08 (#32 to #73): three feature batches, the fix of unmounting, NFS
  exports, the command timeouts and the whole web UI in Russian (Language on the Account page).
  That morning: the dev device's inbound IPv6 filter (#75, ADR-0301), the web UI on a phone
  (#76, ADR-0302), and the device's NFS server with the device check of `shares.nfs-exports`
  (ADR-0303). That evening: the scenario checks of the dev device (#79, ADR-0304), the base
  images pinned by digest (#80, ADR-0305) and the database's write-ahead log (ADR-0306); on
  2026-11-09 the web UI that waits for the API also at boot (ADR-0307), and the write-ahead
  log's device check, after which a sign-in writes at most 0.1 MiB (ADR-0308), the steps of
  two SMB entries that the scenario check leaves out, checked by hand before and after a
  reboot, and the scenario check `smbconf` that runs those checks since (ADR-0309); that evening
  the scrub's start through one helper (ADR-0311) and the device checks that pass it and
  `shares.samba-runtime-writes` before and after a reboot; later that night the WebSocket's
  re-check that runs to its end (ADR-0312), the scrub's status from its start (ADR-0313), the
  scenario check `scrub` (ADR-0314), passwords checked as given (ADR-0315) and three cleanups of
  legacy code, and `storage.scrub` passing again on the scenario check scrub before and after a
  reboot.
- On 2026-11-11 (Log): Telegram alerts through the host's network (ADR-0336), off-device copies
  with restic (ADR-0337), the access log of refused and failed requests only (ADR-0338), and the
  scenario checks `shares` (ADR-0340; ADR-0339 supersedes ADR-0098) and `restore` (ADR-0341), so
  that `scenario.sh all` runs 10 areas, 147 steps. `main` is d6e98c0; the dev device runs 96db310,
  whose runtime differs from it only in a docstring (Dev device).
- In the night of 2026-11-11 to 12 (Log): a batch of six MVP fixes, ADR-0342 to ADR-0349: Samba
  refuses password changes over SAMR, a user's own sign-out, password and 2FA changes are audited,
  an unmount without Docker is refused, backups hold no fstab and a gzip `.enc` upload is refused,
  the dashboard shows the CPU's temperature, a stalled upload is cut off after 120 seconds, and the
  file manager refuses bind mounts before a move. `main` is 405d318 and runs on the dev device;
  `scenario.sh all` runs 148 steps.
- On 2026-11-12 in the morning (Log): a second batch, ADR-0350 to ADR-0355: the audit log
  records storage, snapshots, containers and apps, backups, power, scheduled tasks and the
  host's settings; the rest of `storage.edge-cases`; recorded SMB uids for restores; leftovers
  of interrupted uploads found and removed on request; and the backup API without its unused
  options. `main` is 641f2a5 and runs on the dev device.
- On 2026-11-12 in the afternoon (Log): a third batch, ADR-0356 to ADR-0359: the NUT password
  write-only and upsc on the host, the certificates' honest status, restored copies listed and
  deleted on the Snapshots page, and the audit log's action filter. `main` is b01cf92 and its
  tree runs on the dev device.
- Later that afternoon (Log): a fourth batch, ADR-0360 to ADR-0362: the Storage page lists disks
  attached while it is open, open dialogs form a stack, and every replaced smb.conf is kept in a
  dated copy. `main` is a9adcb1 and its tree runs on the dev device.
- That evening (Log): a fifth batch, ADR-0363 to ADR-0368: Repair permissions as a background
  job, a restore in one streaming pass, no page polling the API, and renaming a user with its SMB
  account. `main` is 7827340 and its tree runs on the dev device.
- On 2026-11-13 (Log), merged by the owner on 2026-11-15 as #81 (e80e2d5): UNOS for the Banana Pi
  BPI-F3 (SpacemiT K1, riscv64), ADR-0370, ADR-0372 and ADR-0373. The owner's BPI-F3 (10.1.1.56)
  runs a64f891e of the branch `mvp.batch-18` since 2026-11-21 (SD MiB, its eMMC: sync 5.309,
  build-supervisor 0.246, build-webui 0.156, up 0.344, check 0.000; check: OK). Before it, the
  same day, 8297e142, deployed at the owner's request
  with `UNOS_DEV_HOST=10.1.1.56`, an empty `UNOS_DEV_SYSTEM_DISK` and its own
  `UNOS_DEV_STATE_DIR=tmp/dev-f3`, after the owner allowed the pull of the pinned
  `caddy:2-alpine` base (MiB written to its eMMC: sync 18.793, build-supervisor 2.051, pull
  0.762, build-webui 0.149, up 0.344, check 0.000). Both containers healthy; The first check.sh
  showed only the missing inbound IPv6 filter, and its install refused: the board had no `nft`.
  At the owner's word ("install it yourself") nftables 1.1.3 was installed there
  (50.523 MiB; `nftables.service` stays disabled) and `ipv6-filter.sh install` loaded the filter
  (0.117 MiB); check.sh: OK (0.016). Its pool is now one M.2 NVMe drive (Log, 2026-11-20).
  Before the merge it ran UNOS from the branch, with the pool `data` (btrfs RAID1 on two SATA SSDs),
  Docker's data root on the pool, and Home Assistant and Frigate beside UNOS, their data in
  `apps/` of the pool, and the supervisor at 3.5% of a core with no dashboard open (ADR-0374,
  ADR-0375). Its administrator account of UNOS, a dev.env for it and the scenario checks before
  and after a reboot wait until it is on again.
- On 2026-11-15 (Log): notes, a ledger entry (`deploy.arm64-bpi-w3`) and guard tests for the dev
  device's board, the Banana Pi BPI-W3 (ArmSoM W3, Rockchip RK3588), which needs no code of its
  own (ADR-0376, proposed); `scenario.sh all` passed on the device before and after a reboot,
  and the checks by hand of the entry's notes before and after a second one. Merged as main
  dfe81f5; the device runs e80e2d5, whose code is the same. Open: a fresh
  `install.sh` on a second microSD card and an NVMe pool (Open owner actions, 6 and 7). That
  afternoon the device's Armbian was upgraded to 26.11.0-trunk (kernel 6.1.172), and hdparm,
  nvme-cli and acl installed (Log).
- On 2026-11-16 (Log): a sixth batch on the branch `mvp.batch-6`, merged into `main` as ce5f86c,
  four entries with ADR-0377 to ADR-0380: Jellyfin with the RK3588's video hardware
  (`apps.rk3588-hardware-acceleration`), idle timeouts of data disks (`storage.disk-spindown`),
  a pool's balance (`storage.pool-balance`) and per-share encryption, Time Machine and Previous
  Versions (`shares.per-share-protocol-options`). Checked on the dev device across four reboots,
  with fixes from what the device showed; the device ran cc9482e, whose runtime differs from the
  branch's head only in a comment of `deploy/uninstall.sh`, until the deploy of the next bullet's
  branch at 21:38 UTC. Passing now: `storage.disk-spindown`,
  on two judgements the owner may overrule (Open owner actions, 10). Open, each waiting on the
  owner (Open owner actions, 8, 9 and 11): `storage.pool-balance` step 1 on the Storage page in a
  browser; `shares.per-share-protocol-options` with a Mac (a Time Machine backup) and a Windows
  computer (Previous Versions); `apps.rk3588-hardware-acceleration` step 1's load on the RKMPP
  cores and its look in a browser, with Jellyfin installed again, and step 2, Immich's NPU, whose
  open parts the next bullet names.
- That evening (Log): `apps.compose-stacks` (ADR-0381, ADR-0382), merged into `main` by #86 with
  the record of its device check: an app of several containers, Immich's server, database, Redis and
  machine learning, its images pinned by tag and digest and its database's password generated on
  its pool, and Immich's machine learning on the RK3588's NPU (step 2 of
  `apps.rk3588-hardware-acceleration`). The branch merged with main ce5f86c (41ad495) runs on the
  dev device, Immich installed. Its device check passed: before a reboot the install (3661.938 MiB
  on the SD card with the images' pull), the containers, mounts, password file and network, stop,
  start and restart in order, an uninstall that keeps the data and a reinstall that uses the same
  database, 0.547 MiB in 30 idle minutes, the six models of Immich's machine learning on the NPU
  and RKMPP in Immich's server; after a reboot from the API the containers with those checks, the
  models on the NPU again, then the pool's disk awake while Immich runs (ADR-0381's cost), and at
  23:55 GET /apps/installed with one Immich and its three companions running. `apps.compose-stacks`
  passes. The owner registered Immich's administrator and chose RKMPP in Immich by 2026-11-17;
  open: photos and Immich's jobs (Open owner actions, 13) and a look at the App Store's hint (14),
  for `apps.rk3588-hardware-acceleration` step 2, which stays false with step 1's open parts (11).
- On 2026-11-17 (Log): Immich searches with the CLIP model nllb-clip-base-siglip__v1, which
  understands Russian, and the seventh batch on the branch `mvp.batch-7`, merged into `main` with
  its record: an install as a background job whose progress the App Store shows
  (`apps.install-progress`, ADR-0384), groups of users with share access (`shares.group-access`,
  ADR-0385), two-factor authentication required after a grace period
  (`auth.mandatory-two-factor`, ADR-0386), each volume's use against its quota with alerts and a
  history (`storage.volume-quota-usage`, ADR-0387), and backend tests in parallel workers with a
  commit that reuses a green run (`testing.per-test-temp-paths`, `ci.pre-commit-reuses-green-run`,
  ADR-0383): `make check` takes about 2 minutes instead of 12. Checked on the dev device across a
  reboot; the check found that deleted volumes kept their quota groups, which the monitor now
  destroys, checked again across a second reboot. All pass but `apps.install-progress`, whose
  install with Immich's pulls and App Store card wait for the owner (Open owner actions, 15).
- On 2026-11-17 that evening (Log): the eighth batch on the branch `mvp.batch-8`, merged into `main`
  with its record: a command palette on Ctrl+K (`webui.command-palette`, ADR-0393), several
  containers or shares at once (`webui.bulk-actions`, ADR-0389), memory and CPU limits per
  container of an app (`apps.container-resource-limits`, ADR-0390), SMB bound to chosen
  interfaces (`shares.smb-bind-interfaces`, ADR-0391), API tokens limited to reading or to areas
  (`auth.api-token-scopes`, ADR-0392), and the dashboard's host network and disk I/O
  (`system.dashboard`, ADR-0388). All pass, with `auth.api-token-last-used-throttle`'s device step.
- In the night of 2026-11-17 to 18 (Log): the ninth batch on the branch `mvp.batch-9`, merged
  into `main` with its record: the web UI's session in an HttpOnly cookie that counts only from
  its own origin and with the tab's session key (`auth.token-storage`, ADR-0397, superseding
  ADR-0009), copies and moves into share folders with the modes of Repair permissions
  (`files.group-writable-copies-and-moves-in-shares`, ADR-0396), a dashboard that each user
  arranges (`webui.dashboard-layout`, ADR-0395), and the container stats endpoints removed
  (`system.no-container-stats-endpoints`). All four pass.
- On the morning of 2026-11-18 (Log): the tenth batch on the branch `mvp.batch-10`, merged into
  `main` with its record: the supervisor's log as one JSON object per line, silent while it idles
  (`architecture.structured-logging`, ADR-0398), an hour of the host's resource history in RAM
  with the host's processes for administrators only (`system.resource-monitor`, ADR-0399), a check
  that fails for raw controls and colours in the web UI's pages (`webui.design-system-lint`,
  ADR-0400), and every time of the API with its offset (`architecture.times-with-time-zones`,
  ADR-0401, superseding ADR-0237 and ADR-0300). All four pass.
- On 2026-11-18 (Log): the eleventh batch on the branch `mvp.batch-11`, merged into `main` with
  its record: audit entries for the administrator's actions that ADR-0359 had left open, the
  firewall, the login history's clear, pools, images, power and UPS settings, notification
  channels and the off-device settings among them (`security.audit-admin-actions`, ADR-0402,
  superseding ADR-0359), an App Store that offers only the apps whose images publish the host's
  platform, riscv64 included (`apps.catalog-platforms`, ADR-0403), a stored certificate that Caddy
  serves, with its local CA as the fallback (`security.custom-certificates`, ADR-0404, superseding
  ADR-0357), and a dark, light or system theme for each user (`webui.light-theme`, ADR-0405). All
  four pass; the catalog's check on the BPI-F3 is the owner's.
- On 2026-11-18 (Log): the twelfth batch on the branch `mvp.batch-12`, merged into `main` with
  its record: a supervisor tmpfs of 192 MiB on every board and a backup's database of at most
  48 MiB (`deploy.tmpfs-for-small-boards`, ADR-0406), power settings that shut the host down at a
  set time or when idle and arm the RTC to wake it (`system.scheduled-power`, ADR-0407), an
  update in place of an installed app with a rollback to its earlier image
  (`apps.update-in-place`, ADR-0408), and security keys (WebAuthn) as a second factor
  (`auth.security-keys`, ADR-0409, superseding ADR-0239). Three pass; the update in place waits
  for pinned catalog images. A real scheduled shutdown and wake and a hardware key in a browser
  are the owner's.
- On 2026-11-18 (Log): the thirteenth batch on the branch `mvp.batch-13`, merged into `main` with
  its record: every catalog image pinned by a version tag and digest, with a weekly audit of moved
  tags and Readarr removed (`apps.pinned-catalog-images`, ADR-0410), a disk added to a pool or
  removed from it in a host unit after typed confirmations and checks of the profile and the space
  (`storage.pool-add-and-remove-disks`, ADR-0411), check.sh's load check that fails for a UNOS
  container it could not measure (`system.idle-load-budget`, ADR-0412), and an install.sh without
  mdadm and five other unused packages (`deploy.no-unused-mdadm`, ADR-0413). The pinned images
  pass; the others wait for a spare disk, the owner's BPI-F3 and a fresh install, and
  transmission's real update for a pull that completes on the device's network.
- On 2026-11-18 to 19 (Log): the fourteenth batch on the branch `mvp.batch-14`, merged into `main`
  with its record: SMART of a disk attached after the supervisor started, through a node the
  supervisor makes in its own /dev (`storage.hot-plugged-disks-smart`, ADR-0414), BookStack,
  Paperless-ngx and Linkwarden with their database or broker as companions
  (`apps.catalog-companions`, ADR-0415), the code paths of floating images removed
  (`apps.no-floating-image-paths`, after ADR-0410), and the system, security, logs and NFS
  routers on the host only through host.py with the firewall as the host's ufw, turned on only
  with a revert armed (`architecture.host-command-layer`, `security.host-firewall`, ADR-0416).
  The host-command layer and the firewall pass; the others wait for a USB disk, the owner's
  install of the three apps and a pull of transmission that completes.
- On 2026-11-19 (Log): the fifteenth batch on the branch `mvp.batch-15`, merged into `main` with
  its record: the API documentation of every route, field, example and error status, with a test
  that derives the errors from the code (`architecture.openapi-descriptions`, ADR-0417), iSCSI
  targets of volumes with mandatory CHAP (`shares.iscsi-targets`, ADR-0418), every data path from
  `UNOS_DATA_DIR` (`architecture.configurable-data-dir`, ADR-0419), and remote access through
  WireGuard with local names for apps (`system.remote-access`, ADR-0420). The documentation and
  the data directory pass; iSCSI waits for a kernel with LIO's file backstore, remote access for
  the owner's router and a client outside the LAN. The checks that waited for the owner and
  need no hardware of theirs ran too, at the owner's word ("everything you can do without me, do
  it yourself"): `apps.manage-installed`, `storage.edge-cases`, `shares.rollback-era-safeguards`,
  `shares.nfs-exports-parser` and `apps.docker-calls-off-event-loop` pass; Paperless-ngx and
  Linkwarden installed and ran, BookStack's pull and transmission's update failed at lscr.io.
- On 2026-11-19 to 20 (Log): the seventeenth batch on the branch `mvp.batch-17`, merged into
  `main` with its record: ruff's rules that find bugs (`ci.wider-ruff-rules`, ADR-0425), the
  catalog's LinuxServer images pulled from ghcr.io instead of lscr.io
  (`apps.linuxserver-images-from-ghcr`, ADR-0426), the web UI on Svelte 5 in runes mode and
  composed of area components (`webui.svelte-5`, ADR-0427), the web UI redesigned at the owner's
  word (`webui.redesign`, ADR-0428), and TRIM for every pool, NVMe drives of a K1 included
  (`storage.nvme-trim-on-k1`, ADR-0429). The ghcr.io images, the update in place and the floating
  paths pass. The owner's BPI-F3 moved to its eMMC and one M.2 NVMe drive (Log).
- On 2026-11-19 (Log): the sixteenth batch on the branch `mvp.batch-16`, merged into `main` with
  its record: off-device copies to a restic REST server and over SFTP through the host's ssh
  (`backup.off-device-copies-over-sftp`, ADR-0422), the workflows' actions pinned by commit and the
  audit's tools by hash, with wheels only outside the images (`ci.actions-pinned-by-sha`,
  `ci.pinned-check-tools`, ADR-0423), and bonds, VLANs and bridges with a revert of the uplink
  (`system.network-bonding-and-vlans`, ADR-0424). The off-device copy passes; the bond waits for a
  second network card, the pins for Dependabot and the owner's `make audit`. The triage labels
  exist and the merged branches are gone, on GitHub and in the local worktrees.
- On the device the NFS server runs (nfs-server, rpcbind.socket and rpcbind, enabled since
  2026-11-08, ADR-0303) and exports nothing for now; over IPv6 only LAN sources reach it
  (ADR-0301). avahi-daemon and wsdd2 run (network discovery), and nmbd is disabled since
  2026-11-09 (ADR-0275), which `scripts/dev/check.sh` checks.
- The CI workflow is off on GitHub (ADR-0230): `make check` is the only gate, and
  `scripts/dev/linux-tests.sh` runs the Linux-only tests. The weekly Audit workflow stays on.
- The local venvs still hold the pip versions from before #25; `make setup` updates them (the
  network). The device has not had them yet either: the next supervisor deploy rebuilds the pip
  layer.
- The main checkout's working tree is at #22 (9a28743), far behind `main`: its `scripts/dev/` are
  old (its check.sh, for one, wants nmbd), so device work runs from a worktree of `origin/main`.
- The main checkout is set up: `supervisor/.venv`, `.venv-lint` and `webui/node_modules` were
  copied from environments verified against the pins (with `setup.sh`'s stamps), and the git
  hooks are on. Only Playwright's Chromium 1200 is missing: the workstation's network filter
  blocks the download for agents, so `make e2e-local` needs `E2E_CHROMIUM_PATH` (the cached
  `chromium_headless_shell-1234`).
- Feature work happens in worktrees (ADR-0210) from `origin/main`:
  `git worktree add --no-track -b <id> ../unos-<id> origin/main`. A worktree without
  `AGENTS.md` or `.claude/settings.json` is a stop.
- Next, in this order:
  1. `deploy.digest-pinned-base-images` (#80, ADR-0305) is deployed and its supervisor builds
     reused the cached base; the entry passes once a build shows that after the tag's arm64 image
     has moved upstream, and Dependabot has proposed a digest.
  2. The host-level entries still open (the feature-pass skill's table): after the re-check of
     2026-11-08 after a reboot (Log), 12 more passed, `deploy.health-checks-and-start-order`
     after the reboot of 2026-11-09 (ADR-0307; Log), and `shares.smb-conf-and-status` and
     `shares.edit-and-delete` with the steps checked by hand before and after the reboot of
     12:22 UTC that day (Log), and `files.group-writable-entries-in-shares` on the scenario
     checks around that reboot (Log), and `shares.samba-runtime-writes` and
     `storage.scrub-needs-mounted-pool` on the checks around the reboot of 18:41 UTC, and
     `storage.scrub` again on the scenario check scrub around the reboot of 21:12 UTC (Log), and
     on 2026-11-11 `system.telegram-from-the-device`, `backup.restore` and
     `backup.off-device-copies`, and that evening `deploy.container-logs-off-the-system-disk`,
     `shares.read-only-guest-share`, `shares.self-activation`,
     `shares.restore-keeps-smb-with-proof` and `backup.no-old-backup-import` on the checks around
     the reboot of 19:26 UTC, and `storage.commands-time-out-cleanly`, `system.temperatures`,
     `files.delete-to-trash` and `backup.edge-cases` on the checks around the reboot of 23:22 UTC,
     and on 2026-11-12 `files.upload-edge-cases` and `backup.drop-unused-include-options` on the
     checks around the reboot of 04:35 UTC, `storage.restore-copies-deletable` on the checks
     around the reboot of 13:11 UTC, `shares.keep-every-replaced-copy` on those around the
     reboot of 14:45 UTC, and `shares.repair-in-background`, `backup.streaming-restore`,
     `webui.no-http-polling` and `auth.user-rename` on those around the reboot of 19:37 UTC,
     and on 2026-11-16 `storage.disk-spindown` on those around the reboots of 14:03 and 14:42
     UTC (Log); `shares.repair-permissions` waits for its ACL step before and after a reboot
     (setfacl is on the device since 2026-11-15, and the step passed once after a reboot) and `shares.acl-aware-parent-check` for the owner's word
     on ADR-0193; the others need a device check first or, as `storage.pool-health`, a step the
     re-check could not show.

## Dev device

State on 2026-11-20, evening. A Banana Pi BPI-W3 (ArmSoM W3, Rockchip RK3588, 8 GB), running
Armbian's community image for `armsom-w3`, upgraded on 2026-11-15 from 26.2.0-trunk.151 to
26.11.0-trunk.133 (Debian 13, kernel 6.1.172-vendor-rk35xx);
its address and logins are in `dev.env` (ADR-0212). This journal called it a UGREEN board, after
the UGREEN firmware on its eMMC, until the owner set that right on 2026-11-15 (ADR-0376).

- Flash (ADR-0225): the system runs from the SD card `mmcblk1` (SanDisk, 32 GB). The soldered
  eMMC `mmcblk0` (Samsung, 32 GB) keeps the UGREEN firmware the owner put there: 0 MiB written
  since boot, its own wear estimate 0 to 10%. Nothing writes to it.
- SSH by key only, root by the workstation's key `~/.ssh/unos_dev_ed25519`; root's and the UNOS
  administrator's passwords were rotated and live only in `dev.env` (ADR-0226).
- Runs a64f891e of the branch `mvp.batch-18`: every page on the shared components and the
  design check without a baseline (ADR-0438), deployed on 2026-11-21 at the owner's request after
  `make check` and the full E2E (SD MiB sync 6.098, build-supervisor 4.766, build-webui 19.445,
  up 5.945, check 0.348; total 36.602). Both containers healthy, `post-up check: OK`, check: OK.
- Before it, 9daf179a of the branch `mvp.batch-18` (the eighteenth batch: loading without flicker,
  ADR-0430; every setting in one place, ADR-0431; the shared components, ADR-0432; area E2E per
  feature and the full suite once per batch, ADR-0433; the data, sharing, System and apps pages,
  ADR-0434 to ADR-0437), deployed on 2026-11-20 at the owner's request after `make check` and the
  batch's full E2E (1166 passed) on that commit (SD MiB sync 7.469, build-supervisor 15.824,
  build-webui 19.148, up 5.934, check 1.289; total 49.922). Both containers healthy,
  `post-up check: OK`.
- Before it, 69dfae5 of the branch `mvp.batch-18` (the eighteenth batch, in progress: loading without
  flicker, ADR-0430, and every setting in one place, ADR-0431), deployed on 2026-11-20 at about
  13:04 UTC at the owner's request before the batch's other UX changes (SD MiB sync 6.012,
  build-supervisor 14.750, build-webui 17.590, up 6.184, check 0.254; total 45.496). Both
  containers healthy, `post-up check: OK`; `GET /security/login-history?mine=true` answers.
- Before it, e00008b of the branch `mvp.batch-17` (the seventeenth batch, ADR-0425 to ADR-0429), merged
  into `main` with its record, whose commits on top of it change no runtime file. Its backend was
  deployed on 2026-11-19 in three steps (fc42813: sync 9.027, build-supervisor 18.461, up 3.293
  SD MiB; d907d94 with the web UI: sync 6.348, build-supervisor 15.016, build-webui 17.820, up
  3.461; e00008b: sync 2.523, build-supervisor 15.047, up 2.797), a reboot from the API at
  18:10:24 UTC. transmission runs the catalog's pin from ghcr.io, its `transmission.previous` on
  lscr.io's `latest`; BookStack's data folders stay on the pool after its uninstall.
- Before it, 988bf19 of the branch `mvp.batch-16` (the sixteenth batch, ADR-0422 to ADR-0424), merged
  into `main` with the record of its device check, whose commits on top of it change no runtime
  file. Deployed on 2026-11-19 (SD MiB sync 4.562, build-supervisor 15.062, build-webui 20.875, up
  5.719), a reboot from the API at 14:41:15 UTC (all containers healthy by about 14:43). No
  off-device copy is configured and no virtual interface of UNOS is there; `/etc/unos` is an empty
  folder that the check's kept interfaces left.
- Before it, 82346c1 of the branch `mvp.batch-15` (the fifteenth batch, ADR-0417 to ADR-0420, and its
  fixes), merged into `main` with the record of its device check, whose commits on top of it change
  no runtime file. 15cf31d was deployed on 2026-11-19 at 07:50 to 08:01 UTC (SD MiB sync 6.215,
  build-supervisor 17.715, build-webui 21.367, up 6.465), a reboot from the API at 08:21:50 UTC
  (all containers healthy by 08:23:04), and the iSCSI fix at 09:58 to 10:00 UTC (sync 0.527,
  build-supervisor 15.168, up 3.270). avahi-utils is installed since that check (53.121), and
  WireGuard's `/etc/wireguard/unos-wg.conf` and key are kept with remote access off; ufw stays
  installed and inactive, its files now holding 21 rules of UNOS (the iSCSI ones added by the
  check's Enable). Paperless-ngx's and Linkwarden's data folders stay on the pool after their
  uninstall; their images are removed. Transmission still runs its image of `latest`: its update
  failed a fourth time at the pull.
- Before it, 977b364 of the branch `mvp.batch-14` (the fourteenth batch, ADR-0414 to ADR-0416, and the
  fix of the firewall's FORWARD policies), merged into `main` with the record of its device check,
  whose commits on top of it change no runtime file. cbe437c was deployed on 2026-11-18 at 22:11
  to 22:13 UTC (SD MiB sync 4.234, build-supervisor 14.828, build-webui 20.387, up 5.898, check.sh
  0.254), a reboot from the API at 22:38 UTC (all containers healthy by 22:41), and the fix on
  2026-11-19 at 00:24 UTC (sync 0.457, build-supervisor 13.652, up 3.305). ufw 0.36.2-9 was
  installed then, inactive, with LOGLEVEL off. The device's users are `admin` and `test` (since
  2026-01-24).
- Before it, e5d531a of the branch `mvp.batch-13` (the thirteenth batch, ADR-0410 to ADR-0413),
  merged into `main` with the record of its device check, whose commits on top of it change no
  runtime file. Deployed on 2026-11-18 at 18:57 to 18:58 UTC (SD MiB sync 4.348, build-supervisor
  14.379, build-webui 20.816, up 5.883, check.sh 0.660). A reboot from the API at 19:12 UTC (all
  containers healthy by 19:14).
- Before it, 09e792a of the branch `mvp.batch-12` (the twelfth batch, ADR-0406 to ADR-0409), merged
  into `main` with the record of its device check, whose commits on top of it change no runtime
  file. Deployed on 2026-11-18 at 14:55 to 14:56 UTC (SD MiB sync 4.902, build-supervisor 15.219,
  build-webui 20.574, up 5.945, check.sh 0.844). A reboot from the API at 15:07 UTC (all
  containers healthy by 15:09). The supervisor's tmpfs is 192 MiB; the power settings are all
  off and the RTC holds no alarm.
- Before it, f281749 of the branch `mvp.batch-11` (the eleventh batch, ADR-0402 to ADR-0405), merged
  into `main` with the record of its device check, whose commits on top of it change no runtime
  file. Deployed on 2026-11-18 at 10:06 to 10:08 UTC (SD MiB sync 4.434, build-supervisor 13.746,
  build-webui 20.094, up 6.012, check.sh 0.262). A reboot from the API at 10:15 UTC (all
  containers healthy by 10:16). Caddy serves its local CA; a stored certificate from before stays
  stored and not served.
- Before it, 5412b79 of the branch `mvp.batch-10` (the tenth batch, ADR-0398 to ADR-0401), merged into
  `main` with the record of its device check, whose commits on top of it change no runtime file.
  Deployed on 2026-11-18 at 06:22 to 06:23 UTC (SD MiB sync 7.066, build-supervisor 14.367,
  build-webui 17.977, up 5.672, check.sh 0.219). A reboot from the API at about 06:40 UTC (all
  containers healthy by 06:43). The supervisor's log is JSON lines and the API's times carry
  their offsets.
- Before it, 991cb28 of the branch `mvp.batch-9` (the ninth batch, ADR-0395 to ADR-0397), merged into
  `main` with the record of its device check, whose commits on top of it change no runtime file.
  Deployed on 2026-11-17 at about 23:10 UTC (SD MiB sync 4.445, build-supervisor 14.023,
  build-webui 17.438, up 5.461, check.sh 0.242). A reboot from the API at 23:24:54 UTC (all
  containers healthy by 23:25:53). The web UI's sessions are cookies now: a browser that held the
  old token signs in again.
- Before it, 7f5dd29 of the branch `mvp.batch-8` (the eighth batch, ADR-0388 to ADR-0393), merged into
  `main` with the record of its device check, whose commits on top of it change no runtime file.
  The batch's 320d3ec was deployed at 18:58 to 18:59 UTC (SD MiB sync 5.398, build-supervisor
  13.543, build-webui 17.879, up 5.691, check.sh 0.258), then 7f5dd29, the supervisor alone, at
  about 20:20 UTC (sync 1.258, build-supervisor 12.488, up 2.969, check.sh 0.293). A reboot from
  the API at 19:23:11 UTC (all containers healthy by 19:25:26). SMB listens on every interface
  again, no app has limits, quotas are off; three alerts of the limits check went to Telegram.
- Before it, b17889f of the branch `mvp.batch-7` (the seventh batch, ADR-0383 to ADR-0387), merged into
  `main` with the record of its device check, whose commits on top of b17889f change no runtime
  file. Deployed on 2026-11-17 at 15:31 to 15:32 UTC, the supervisor alone (SD MiB status 0, sync
  0.406, build-supervisor 12.473, up 2.898, check.sh 0.016, status 0). Before it that day: 7cca7e9
  at 12:47 to 12:49 UTC (sync 6.777, build-supervisor 13.957, build-webui 17.262, up 5.652,
  check.sh 0.270), 73f547a at 14:45 (sync 0.875, build-supervisor 12.758, up 3.082) and 9ecd228 at
  15:01 (0.711, 12.062, 2.941). Reboots from the API at 13:31:12 UTC (the boot of 13:32:28; both
  UNOS containers healthy by 13:33:21, check.sh OK with 12.9 MiB on the card since the boot) and
  15:22:55 UTC (the boot of 15:24:14; check.sh OK at 15:30). Quotas are off on the pool `data`, as
  before the checks. Immich's four containers run, its CLIP model nllb-clip-base-siglip__v1 (only
  that model in its cache), its administrator registered and RKMPP chosen by the owner. The SD card
  holds 60 dangling images, the layers of the supervisor's builds, and about 60 MB of blobs of
  cancelled pulls in containerd's content store; pruning them is the owner's call.
- Before it, 41ad495 of the branch `apps.compose-stacks` (ADR-0381, ADR-0382): its feature commits and a
  merge of main ce5f86c, which holds the sixth batch. The branch is merged into `main` by #86 with
  the record of its device check, whose commits on top of 41ad495 change no runtime file. Deployed
  on 2026-11-16 at 21:38 to 21:40 UTC (SD MiB check.sh 0, status and sync --dry-run 0, sync 3.371,
  build-supervisor 11.934, build-webui 16.762, up 5.352, check.sh 0.227); both containers healthy
  after 7 s, check.sh OK: 112.8 MiB written to the SD card since the boot of 14:42 UTC. Immich
  installed on the pool `data` at 21:40 to 21:45 UTC, uninstalled and installed again at 21:52 to
  21:53 UTC, at the owner's word (Log, 2026-11-16, Immich). A reboot from the API at 22:24:02 UTC
  (the boot of 22:25:22; both UNOS containers healthy by 22:26:14, Immich's four by 22:26:35, with
  RestartCount 0 each; check.sh OK at 22:27, 13.4 MiB on the card since the boot). At 23:55 UTC,
  read only: GET /apps/installed listed Immich once with its three companions running, and
  transmission (0); at 00:03:56 UTC the boot id was still the one of 22:26:01, with RestartCount 0
  in Immich's four containers and transmission (0); at 00:09 UTC `deploy.sh status` found the
  runtime files equal to the record's tree (0).
- Before it, cc9482e of the branch `mvp.batch-6` (the sixth batch, ADR-0377 to ADR-0380, since
  merged into `main` as ce5f86c), whose runtime differs from the branch's head only in a comment of
  `deploy/uninstall.sh`, deployed on 2026-11-16 at 19:19 to 19:21 UTC (SD MiB check.sh 0.117,
  status and sync --dry-run 0, sync 1.652, build-supervisor 11.859, build-webui 16.672, up 6.102,
  check.sh 0.234); both
  containers healthy after 6 s. Then `scenario.sh smart` (14 steps, 0.328 MiB) and `balance` (11
  steps, 0.234) OK, `deploy.sh status` equal (0), no Traceback or Killed in the supervisor's log
  (0.016), and check.sh OK (0): 71.6 MiB written to the SD card since the boot of 14:42 UTC,
  14.70 MiB an hour. The batch's earlier trees that day: 57be517 at 01:02 UTC (sync 5.805,
  build-supervisor 12.461, build-webui 16.402, up 5.805), 434d410 at 06:53 UTC (1.996, 11.531,
  16.211, 5.578) and 5d79302 at 13:39 UTC (2.312, 12.051, 16.527, 5.906). Reboots from the API at
  04:26:13, 07:17:10, 14:03:25 and 14:42:26 UTC (both containers healthy by 04:27:26, 07:17:56,
  14:04:38 and 14:43:37; check.sh OK after each, with 9.6, 8.5, 9.2 and 8.1 MiB on the card since
  the boot). The checks and their results are in the Log (2026-11-16).
- sda, the pool's disk (WDC WD6400AACS-00G8B0, behind the JMB58x), as the batch found it: no idle
  timeout of UNOS and no `/etc/udev/rules.d/90-unos-disk-spindown.rules`, and its own automatic
  offline data collection on (read at 19:21 UTC). The smart check's Off at 19:21 UTC (its step
  12), like UNOS's Off at 17:48 UTC before it, ran `hdparm -S 0`, so it has no standby timer until
  its next power cycle. smartd (smartmontools) is inactive and disabled, as `deploy/install.sh`'s
  stop_smartd leaves it: it was active and enabled at 01:04 UTC on 2026-11-16 and was turned off
  during that night's measurements of sda's timer. The Jellyfin of the RK3588 checks is
  uninstalled, the folders `apps/jellyfin` and `media` that its install made on the pool are
  removed, and its image is no longer on the device (check.sh's image list at 19:19 UTC). The
  balance checks' fills are removed. From 22:28 to 22:48 UTC sda had UNOS's shortest idle timeout,
  5 minutes, while Immich ran, and never went to standby (Log); then UNOS's Off again, as found,
  and the rule file is gone. Saving that timeout turns the disk's own offline data collection off
  and Off turns it on again (ADR-0378): both answers had no warning, and the collection was not
  read after them.
- The owner had Armbian upgraded on 2026-11-15 ("update Armbian on the W3 to the current one"):
  a copy of `/boot`, `/etc/fstab`, `/etc/apt` and the package list on the workstation (the
  worktree's `tmp/`), `apt-get update` (166.512 MiB on the card), then `apt-get full-upgrade` in a
  systemd unit of its own, keeping changed configuration files: 136 packages upgraded and 9 new,
  none removed, among them Armbian 26.11.0-trunk.133 with kernel 6.1.172-vendor-rk35xx, Docker
  29.9.3, containerd 2.4.2, Samba 4.22.11, systemd 257.13 and OpenSSH 10.0p1-7+deb13u4 (3155.180
  MiB on the card, downloads and the restart of Docker included). `armbianEnv.txt` and
  `boot.scr` stayed as they were; the owner's device tree overlay, which no package owns, was
  copied into the new kernel's dtb directory. A reboot from the API at 16:02 UTC (the boot of
  16:03:11; both containers healthy by 16:03:33; check.sh OK). Then hdparm, nvme-cli and acl
  installed (63.137 MiB): the first two are in `deploy/install.sh`'s list and were missing, acl
  gives the scenario checks setfacl. `scenario.sh all` OK at 16:17 UTC, 157 steps without a skip
  (20.047 MiB).
- Before batch 6, main e80e2d5 (the merge of #81, the BPI-F3's change, ADR-0369 to ADR-0375),
  deployed to this device at 12:52 to 12:55 UTC on 2026-11-15 (SD MiB sync 2.520, build-supervisor
  440.910: the pip layer built again for #81's pip 26.3, build-webui 18.504, up 6.473, check.sh
  0.453); both containers healthy after 7 s, check.sh OK: unos-supervisor 0.6% of a core,
  unos-webui 0.0%, soc-thermal 42 C, first passive trip 75 C. Then `scenario.sh all` OK (10 areas,
  156 steps, one skipped for want of setfacl; 20.109 MiB), a reboot from the API at 13:39:55 UTC
  (the boot of 13:40:11; both containers healthy by 13:40:40; check.sh OK), and `scenario.sh all`
  OK again (20.125 MiB). Then the checks by hand of `deploy.arm64-bpi-w3` (its notes) before and
  after a second reboot from the API at about 14:21 UTC (the boot of 14:21:23; both containers
  healthy by 14:21:51; check.sh OK): all passed.
- Before it, the tree of main 7827340 (the fifth batch, ADR-0363 to ADR-0368), deployed as 5deee90 at
  20:14 UTC (SD MiB sync 2.941, build-supervisor 13.473, build-webui 19.176, up 6.012, check.sh
  0.016), then `scenario.sh shares`, `restore` and `smb` OK. Before it, the integration tree
  b9aaea5, deployed at 19:33 UTC and rebooted from the API at 19:37:03 UTC (the boot of 19:37:58;
  both containers healthy by 19:38:50): `scenario.sh shares`, `restore`, `smb` and `backups` OK
  before and after.
- Before it, the tree of main a9adcb1 (the fourth batch, ADR-0360 to ADR-0362), deployed as 2bb76a8 at
  15:23 UTC (SD MiB sync 2.062, build-supervisor 12.875, build-webui 19.156, up 6.492, check.sh
  0.016), then `scenario.sh smbconf` (17 steps) and `smb` OK. Before it, the integration tree
  d149c3a, deployed at 14:42 UTC and rebooted from the API at 14:45:53 UTC (the boot of 14:46:49;
  both containers healthy by 14:47:40): `scenario.sh smbconf`, `smb` and `files` OK before and
  after.
- Before it, the tree of main b01cf92 (the third batch, ADR-0356 to ADR-0359), deployed as 0a5294c at
  13:29 UTC (SD MiB sync 1.617, build-supervisor 13.117, build-webui 19.270, up 6.020, check.sh
  0.016). Before it, the integration tree 8150936, deployed at 13:08 UTC (sync 3.000,
  build-supervisor 13.484, build-webui 19.344, up 6.188), and rebooted from the API at 13:11:00
  UTC (the boot of 13:11:56; both containers healthy by 13:12:50): `scenario.sh volumes` and
  `files` and the checks by hand of the batch OK before and after, but for the line the Log
  names.
- Before it, main 641f2a5 (the second batch, ADR-0350 to ADR-0355), deployed on 2026-11-12 at 04:27
  UTC (SD MiB sync 3.898, build-supervisor 13.688, build-webui 18.734, up 6.281, check.sh
  0.016), and rebooted from the API at 04:35:30 UTC (the boot of 04:36:49; both containers
  healthy by 04:37:17; 8.7 MiB on the SD card from the boot to check.sh): `scenario.sh all`
  (147 steps passed, 1 skipped) and the checks by hand of the batch OK before and after.
- Before it, main 405d318 (the batch of ADR-0342 to ADR-0349), deployed at 23:44 UTC (SD MiB sync
  1.000, build-supervisor 12.754, build-webui 19.102, up 5.977, check.sh 0.016); after it
  `scenario.sh files` OK (1.148 MiB) and the upload and audit checks by hand. Before it, the
  integration branch's tree 8ea5a08, deployed at 23:06 UTC (sync 4.035, build-supervisor 13.840,
  build-webui 18.914, up 5.953), and rebooted from the API at 23:22:13 UTC (the boot of 23:23:10;
  both containers healthy by 23:23:59; 8.7 MiB on the SD card from the boot to check.sh):
  `scenario.sh all` (147 steps passed, 1 skipped) and the checks by hand OK before and after,
  but for what the Log names. Samba's `[global]` has `check password script = /bin/false`.
- Before it, main 96db310 (ADR-0338: the access log keeps refused and failed requests only), its
  supervisor deployed at 15:16 UTC (SD MiB sync 1.129, build-supervisor 12.336, up 3.250, check.sh
  0.016); `main` d6e98c0 differs from it at runtime only in a docstring of
  `supervisor/src/shares/smbconf.py`, so it was not deployed again. Rebooted from the API at
  19:25:58 UTC (the boot of 19:26:54; both containers healthy by 19:28:12): `scenario.sh all`
  from d6e98c0 (146 steps passed, 1 skipped) OK before and after, and `check.sh` OK after.
  Before it, main 27b840b (its tree is the branch backup.off-device-copies at c926016, deployed at
  13:08 UTC: SD MiB sync 1.012, build-supervisor 12.898, build-webui 18.852, up 5.742, check.sh
  0.016), with restic 0.18.0 of Debian 13 in an image layer of its own since 09:34 UTC (81.227
  MiB on the SD card that once). Rebooted from the API at 10:18:07 and at 12:51:45 UTC (the boots
  of 10:19:25 and 12:53:02): `check.sh` and `scenario.sh all` (101 steps) OK before and after
  both. Telegram alerts leave through the host's network (ADR-0336); the owner's Telegram channel
  received the test notification and the alerts of the checks (Log, 2026-11-11).
- Before it, on 2026-11-10: main bcc4eff, deployed in three steps, and rebooted from the API at 22:00:26
  UTC (the boot of 22:01:43 UTC; ssh answered by 22:02:17): `check.sh` OK after it, with the
  capped log options, Samba's lockout policies, uvicorn's `--limit-concurrency 128` and Caddy's
  431 for a 22 KB header block all kept, and `scenario.sh smb` OK, 28 steps (3.418 MiB).
  - 20:51 UTC, main 4b08419 (`system.alert-events`): sync 1.746, build-supervisor 12.695,
    build-webui 18.801, up 5.656, check.sh 0.016 MiB; no ERROR or Traceback in the log.
  - 21:22 UTC, main c1328ab (`security.server-limits`): sync 1.211, build-supervisor 12.090,
    build-webui 18.535, up 5.637, check.sh 0.031 MiB.
  - 21:58 UTC, main bcc4eff (`shares.smb-login-protection`): sync 1.652, build-supervisor
    12.184, up supervisor 3.344, check.sh 0.016 MiB; then `scenario.sh smb` OK, 28 steps
    (3.895 MiB).
  - Upload speed from the workstation (10GBASE-T) to the device's 2.5 GbE port,
    `scripts/dev/upload-speed.py`: 256 MiB at 239 MiB/s (2.01 Gbit/s), 4 GiB at 79 to 105 MiB/s,
    the write rate of the pool's 7200 rpm disk (sda, WDC WD6400AACS); 0.09 to 0.19 MiB on the SD
    card for its sign-in and sign-out.
- Before them, main 70dfec4 (System > General sets the hostname and the time zone), its web UI
  deployed at 20:13 UTC: SD MiB sync 0.973, build-webui 18.367, up webui 2.988. For the reboot
  check of `system.hostname-and-time-zone` the zone was set to Etc/GMT-3 (0.137 MiB) and the
  hostname to unoscheck-name (0.148 MiB), and the device was rebooted from the API at 20:14:04
  UTC (the boot of 20:15:21 UTC); both were kept on the host and in the API, then put back
  (0.074 and 0.102 MiB), and smbd, wsdd2 and avahi-daemon were restarted to take the old name
  again (0.105 MiB); `check.sh` OK. Before it, main e6a44d5 (the time zone and hostname on the
  host), deployed on 2026-11-10 at 19:25 UTC: SD MiB sync 1.129, build-supervisor 12.488, up 3.219 (the supervisor recreated without
  the bind mount of /etc/hostname), check.sh 0.016. Before it, main f68c7c1 (session revocation
  and log rotation), deployed on 2026-11-10 at 18:50 UTC:
  SD MiB sync 2.395 (41 files), build-supervisor 12.266 (pip's layer cached), build-webui
  18.480, up 5.938, check.sh 0.016; the UNOS containers' logs now show json-file
  `{"max-file":"3","max-size":"10m"}` (transmission keeps `{}`, created before). The first
  sign-in after the start, with its session row, and a GET: 0.086 MiB. Rebooted from the API at
  the owner's word ("do everything yourself", in translation): requested at 18:51:26 UTC, the
  boot of 18:52:42 UTC, ssh answered by 18:53:15, both containers healthy, `check.sh` OK with
  the log options kept, and `scenario.sh all` OK again, 88 steps: files 0.543 MiB, volumes 0.566,
  smart 0.098, backups 0.512, smb 3.199, smbconf 2.516, scrub 0.645. Before it, main 91ebf48 was
  deployed at 17:55 UTC: both images built from its runtime
  files, the supervisor on the base that ADR-0305 pins (pip's layer cached). SD MiB: sync 5.074 (128 files written; deleted: the
  Caddyfile copy that `deploy/` had, and `webui/src/lib/api/monitor.ts`), build-supervisor 14.082,
  build-webui 18.727, up 6.066, check.sh 0.016. Then `scripts/dev/scenario.sh all`: every area
  OK, 88 steps: files 16 (0.867 MiB), volumes 7 (0.582), smart 5 (0.125), backups 8 (0.445),
  smb 27 (3.207), smbconf 15 (2.363), scrub 10 (0.629). Before the deploy the SD card had 44.5
  MiB written in 20.7 hours
  since its boot (2.15 MiB an hour).
  The deploys' backup and rollback copies are in the main
  checkout's `tmp/dev/`. The device has no `unos` unit: `deploy.sh up` starts its containers, and
  at boot dockerd does, by their restart policy. Rebooted from the API twice on the morning of
  2026-11-08, for the reboot checks of the IPv6 filter, by the owner at about 22:57 UTC that day
  for the re-check of ADR-0018 (the boot of 22:59:48 UTC; it answered again at 23:00), and from
  the API at the owner's word ("permission granted") at 07:16:57 UTC on 2026-11-09 (the boot of
  07:17:13 UTC; ssh answered by 07:17:34), and at 12:22:52 UTC that day for the SMB steps
  checked by hand, at the owner's word ("Yes, check") (the boot of 12:23:08 UTC by `uptime -s`;
  ssh answered by 12:23:24, both containers healthy by 12:23:37), and from the API at 18:41:15
  UTC that day for the device checks of that evening, at the owner's word ("I allow
  everything", in translation) (the boot of 18:42:31 UTC; ssh answered by 18:42:47, both
  containers healthy by 18:42:59), and at 21:12:45 UTC, at the same word, for the checks of main
  eceead5 (the boot of 21:14:01 UTC; ssh answered by 21:14:17, both containers healthy by
  21:14:29). The prune before the reboot of
  07:17 UTC deleted the 11 dangling images (the old builds' layers) and reclaimed only 134.9 kB,
  where this file had said about 351 MB.
- Containers: unos-supervisor and unos-webui (both healthy), transmission (web login, data on the
  pool `data`), and Immich's four since 2026-11-16 (ADR-0381): immich, immich-database,
  immich-redis and immich-machine-learning, on the network `unos-app-immich`, running and healthy
  where they have a health check (22:26 UTC), with its folders in `apps/immich` of the pool `data`
  (library, model-cache with the machine learning's models, postgres with the database's data and
  its password file, redis). Immich has no administrator yet (Open owner actions, 12). Images
  (check.sh at 21:40 UTC on 2026-11-16): deploy-supervisor:latest (`3c6accde...`, 41ad495) and
  `:rollback` (`3847c5c5...`, cc9482e), deploy-webui:latest (`a855e354...`) and `:rollback`
  (`2276488b...`); 56 dangling images (the old builds' layers); Immich's four, pulled by digest
  at 21:40 to 21:45 UTC (3661.938 MiB on the SD card with the install): immich-server v3.4.1
  (`7c489add...`), immich-machine-learning v3.4.1-rknn (`b7baeb12...`), Immich's postgres
  14-vectorchord0.4.3-pgvectors0.2.0 (`bcf63357...`) and valkey 9 (`418652cf...`);
  lscr.io/linuxserver/transmission, alpine (the
  final backup of `deploy/uninstall.sh`) and caddy:2-alpine (the web UI's base: Caddy 2.10.2 of
  January 2026, `sha256:f2b257f2...`). The slice-2 rollback images and files were deleted on
  2026-11-07.
- Docker 29.1.5 with the containerd image store. The supervisor's base, python:3.11-slim
  `sha256:65bd3d9f...` (Python 3.11.18), is in BuildKit's build cache, not an image: the pins of
  ADR-0305 are these two digests. The supervisor builds of 2026-11-08, 22:28 UTC, and
  2026-11-09, 07:41, 18:24 and 21:06 UTC, started from it: every step of the first CACHED, of the
  others all but `COPY src/`.
- The audit log is `/data/audit/audit.jsonl` (ADR-0224); the old `/data/audit_log.json` was
  deleted.
- The Scheduler has no tasks of the checks; its history keeps their runs, which deleting a task
  leaves: three of `unos check keep 3` (2026-11-07), one of `unos check command` (2026-11-08)
  and four of `unos check scrub` (2026-11-09, two of them failed with "Scrub is already
  running"), and two of each run of the scenario check `scrub`, named `unoscheck<6 hex digits>`
  (one failed with the 409's text, one "Scrub started").
- Storage: the pool `data` (btrfs on /dev/sda) at /mnt/pools/data, root:root 755 (ADR-0220),
  with 1.7 GiB used of 597 GiB at 19:21 UTC on 2026-11-16, before Immich's install put its
  folders in `apps/immich` (not read since). The share DATA is served from
  /mnt/pools/data/shared (root:unos-smb 2770); no account has access to it yet. The regular
  account `test` stays for checks of a regular user's view.
- Scenario checks (ADR-0304): `scripts/dev/scenario.sh all` passed on 2026-11-08 at about 17:15
  UTC, at 22:33 UTC after the deploy and at 23:03 UTC after the reboot, and on 2026-11-09 at
  07:43 UTC after the deploy of main 12483dd, and left none of its users, shares, volumes,
  folders or backups; `scripts/dev/scenario.sh smbconf` (ADR-0309) passed at 13:22 UTC that
  day and, after its review's fixes, at 13:46 UTC, and left the device as it found it, and
  `scripts/dev/scenario.sh all` (six areas) at 13:58 UTC, after the reboot of 12:22 UTC, at
  18:25 UTC after the deploy of main 31ac73b and at 18:43 UTC after the reboot of 18:41 UTC, and
  with the area scrub at 21:09 UTC after the deploy of main eceead5 and at 21:14 UTC after the
  reboot of 21:12 UTC. The
  pool's trash `/mnt/pools/data/.unos-trash` (made at 00:05 UTC that night) is empty. The first
  run's short self-test of sda completed without error, as the two before it; sda's SMART status:
  PASSED.
  The RAM rootfs of `scripts/dev/linux-tests.sh` (560 MiB in `/dev/shm`), made again after the
  boot of 2026-11-09, 07:17 UTC, is gone since the reboot of 12:22 UTC; every reboot removes it,
  and `linux-tests.sh` prints the owner action that makes it again.
- Network: global IPv6 from a Hurricane Electric tunnel prefix. Since 2026-11-08 the services
  answer over IPv6 only the LAN (link-local, unique-local and the LAN's global prefix), and the
  BitTorrent port 51413 everyone: the table `ip6 unos_ipv6_inbound`, loaded at boot by
  `unos-ipv6-inbound.service` (ADR-0301, `scripts/dev/ipv6-filter.sh status`). IPv4 is not
  filtered on the device. Samba admits only private, unique-local and link-local addresses.
  smbd runs unconfined by AppArmor (ADR-0162). The NFS server runs (ADR-0303), with no export.
- SD writes on 2026-11-16 (Immich; the deploy above): Immich's install with the pull of its four
  images 3661.938 MiB, its second install with the images there 6.059; stop 2.254, start 2.785,
  restart 5.023, the uninstall 3.695; 30 idle minutes 0.547; the reads and probes of the checks
  0 to 0.895 each, `scenario.sh hardware` 0 and 0.125, the reboot call 0.027, sda's idle timeout
  set and put back 0.105 and 0.074 and its 20 minutes of reads 0.219 (the Log).
- SD writes on 2026-11-16 (the sixth batch; the deploys above): `scenario.sh all` from 01:05 UTC
  20.160 MiB (files 0.551, volumes 0.859, smart 0.340, backups 0.441, smb 3.148, smbconf 2.906,
  shares 3.145, scrub 0.543, balance 0.234, offsite 0.527, restore 7.465, hardware 0) and from
  14:45 UTC, after the reboot of 14:42, 21.832 MiB (0.137, 0.914, 0.512, 0.426, 5.074, 2.816,
  3.055, 0.594, 0.234, 0.531, 7.539, 0); `scenario.sh smart` and `smb` 0.492 and 5.336 MiB from
  06:54 UTC, and 0.414 and 5.371 from 13:42 UTC; `smart` and `balance` 0.328 and 0.234 from 19:21
  UTC. Jellyfin's install, its image pulled onto the card, 1262.047 MiB; its uninstall 1.117. The
  other steps by hand, each at most 1.418 MiB (a full balance across a restart of the
  supervisor): the Log.
- SD writes on 2026-11-08: the deploy of main 78769b4 31.836 MiB (sync 1.527, build-supervisor
  6.094, build-webui 18.539, up 5.676, backup 0); the scenario checks 5.946 MiB at 17:15 UTC
  (files 0.418, volumes 0.938, smart 0.098, backups 0.512, smb 3.980, of which the smb check's
  account took 0.957 to make and 0.785 to delete), 5.259 MiB at 22:33 UTC (0.426, 0.641,
  0.125, 0.430 and 3.637) and 5.393 MiB at 23:03 UTC (0.441, 0.734, 0.105, 0.449 and 3.664);
  60.1 MiB in the 11.1 hours since that morning's boot, by 22:34 UTC; the boot of 22:59:48 UTC
  9.7 MiB by 23:00:29.
- SD writes on 2026-11-09: the web UI's deploy 3.265 MiB (sync 0.812, `up webui` 2.453, its
  backup 0); the read-only probes before it (the unit and the containers 0.031, busybox's wget
  0.016, 0 and 0, the image list 0.266) and after it 0.023, `check.sh` 0.055 and 0; a probe at
  02:55 UTC 0. Around the reboot: the old boot 36.0 MiB in its 8.3 hours, by 07:15 UTC;
  `check.sh` 0.039, the prune 5.371; the boot of 07:17:13 UTC 9.5 MiB by 07:21:37, the RAM
  rootfs's 1.148 among them; `check.sh` 0.023; the reads of the re-check 0.016, 0.141, 0, 0.016
  and `check.sh` 0; `linux-tests.sh` 0.039. The deploy of main 12483dd 17.731 MiB (sync 1.633,
  build-supervisor 12.836, up 3.262; `check.sh` 0.023 before it and 0.016 after it,
  `deploy.sh status` 0.016, `sync --dry-run` 0); then the database's files read 0.016, 30
  seconds idle 0 and 0, a sign-in right after the start 0.145, one in the middle of the log
  0.082, a call with a cached token 0.020, a restart of the supervisor 1.125, `check.sh` 0.016
  and `scripts/dev/scenario.sh all` 4.606 (files 0.434, volumes 0.559, smart 0.098, backups
  0.449, smb 3.066). The SMB steps checked by hand: the reads before them (`check.sh` 0.023,
  `deploy.sh status` 0, the Samba files 0.031, the status, the shares and the users 0.020
  each); two attempts that stopped on errors of the session's script, 0.016 and 0.230 (the
  second with its hand edit of smb.conf and its restore); the check before the reboot 1.082 (a
  share's create 0.242, the PATCH with a new folder 0.203, the delete with the share list read
  before it 0.219); `check.sh` 0.016 before the reboot and 0.016 after it, the reboot call
  0.020, the boot 8.5 MiB by 12:23:37 (the reboot script's polls over ssh among them, not
  measured on their own), the first call after it with its sign-in 0.133, the share list
  0.020; the check after the reboot 2.308, the restart of the supervisor's 1.230 among them;
  `check.sh` 0.016 at the end, 11.0 MiB since the boot. `scripts/dev/scenario.sh smbconf`
  2.621 MiB (15 steps; the new user 0.133, the share's create 0.250, the PATCHes 0.195 and
  0.184, the Delete 0.207, the restart of the supervisor 1.277; the user's delete 0.137); after
  the review's fixes, with the regular user `test` instead of a new one, 2.391 MiB (the create
  0.242, the PATCHes 0.176 and 0.191, the Delete 0.207, the restart 1.305).
  `scripts/dev/scenario.sh all` at 13:58 UTC 6.994 MiB (files 0.434, volumes 0.594, smart 0.098,
  backups 0.500, smb 2.973, smbconf 2.395); the audit log's search 0.027; the measurement of
  passdb.tdb's writes at 14:24 UTC 1.496 MiB (a temporary account with SMB made, five failed
  sign-ins, a password change, the account deleted). The deploy of main 31ac73b 39.168 MiB
  (`check.sh` 0.031, `status` and `sync --dry-run` 0, sync 2.059, build-supervisor 12.359,
  build-webui 18.457 after a first try that stopped at once, up 6.246, `check.sh` 0.016);
  `scripts/dev/scenario.sh all` 7.601 MiB before the reboot (files 0.871, volumes 0.570, smart
  0.117, backups 0.457, smb 3.180, smbconf 2.406) and 7.173 after it (0.555, 0.543, 0.098,
  0.465, 3.074, 2.438); the scrub steps by hand 0.220, 0.270 and 0.254 in three runs before the
  reboot and 0.285 after it; `check.sh` 0.016 before the reboot, 0.023 right after the boot and
  0.016 at the end; the reboot call 0.020, the boot 9.0 MiB by 18:42:59. Afterwards: a scrub of
  data with its status read every half second 0.031 (18:52 UTC); the Scheduler's tasks and runs
  read 0.020, and a scrub with btrfs's status read in the container 0.035 (19:06 UTC). The
  deploy of main eceead5 39.270 MiB (`check.sh` 0.023, `status` and `sync --dry-run` 0, sync
  2.176, build-supervisor 12.609, build-webui 18.688, up 5.758, `check.sh` 0.016);
  `scripts/dev/scenario.sh all` 7.613 MiB before the reboot (files 0.426, volumes 0.566, smart
  0.117, backups 0.398, smb 3.047, smbconf 2.504, scrub 0.555) and 7.746 after it (0.574, 0.523,
  0.098, 0.473, 3.027, 2.508, 0.543); `check.sh` 0.016 each time; the reboot call 0.027, the boot
  8.6 MiB by 21:14:29.
- SD writes on 2026-11-07: the audit log's deploy wrote 4.6 MiB (sync), 11.8 MiB (build of the
  supervisor; the base came from the build cache) and 2.6 MiB (up); deleting the old audit file
  0.06 MiB. A sign-in writes 0.156 to 0.168 MiB (0.172 to 0.195 MiB before), an API call with a
  cached token 0.020 MiB, the idle device 16 KiB a minute. Earlier that day: the slice-2 deploy
  about 45 MiB, the transmission image pull about 177 MiB.

Open owner actions:

1. GitHub: `main` stays without branch protection on the Free plan (ADR-0229), and the CI
   workflow is off (ADR-0230; "Enable workflow" in the Actions tab turns it on). The classic
   token the device used before 2026-11-05 can be revoked only on github.com (Settings,
   Developer settings, Tokens).
2. `make setup` in the owner's own terminal, where the network filter may allow it: Playwright's
   Chromium 1200, and the pip versions of #25 in the venvs of each checkout.
3. Who gets access to the share DATA (an SMB account for a regular user).
4. Sign in as the dev device's regular account `test`: main eceead5, deployed on 2026-11-09 at
   21:07 UTC, checks passwords as given (ADR-0315), so if its password is longer than 72 bytes it
   no longer signs in, and an administrator sets a new one on the Users page.
5. Done by the agents on 2026-11-19 at the owner's word: the supervisor image's root filesystem
   in the dev device's `/dev/shm` (about 1.5 SD MiB, the command that `scripts/dev/linux-tests.sh`
   prints). A reboot clears it; the agents make it again before a run of the Linux-only tests.
6. A second microSD card for the dev device, for a fresh `deploy/install.sh` on the BPI-W3 with
   Armbian's `armsom-w3` image (`deploy.arm64-bpi-w3`, step 1).
7. An NVMe drive for the BPI-W3's M.2 Key M slot, for an NVMe pool before and after a reboot
   (`deploy.arm64-bpi-w3`).
8. `storage.pool-balance`, on the dev device's Storage page in a browser (the agents do not sign
   in there as its administrator): step 1 of its notes' steps by hand, a full balance of `data`
   with its progress, Pause, Resume and the 90% note, and the page's parts of steps 2 and 3 (the
   reloaded page during a balance, the card's Last balance).
9. `shares.per-share-protocol-options`: a Mac on the LAN makes a Time Machine backup to a share
   with Time Machine on, and a Windows computer lists and opens a share's Previous Versions in File
   Explorer; then the Mac's list after a reboot (steps 3 to 5 of its notes' steps by hand).
10. `storage.disk-spindown`: a look in a browser on the dev device, no condition of the entry's
    steps: the Storage page with sda asleep, and GET /storage/disks a minute later, to record
    whether the page's pool lists wake it (step 6 of its notes' steps by hand). And two judgements
    of its pass (Log, 2026-11-16) for the owner to confirm or overrule, which sets it back to
    passes false: that this look is no condition of it (the Storage page itself is the E2E
    specs'), and that the reboot checks on 5d79302 stand for cc9482e, whose `read_rule` reads the
    lines that UNOS writes as before.
11. `apps.rk3588-hardware-acceleration`: step 1's open parts, with Jellyfin installed again at the
    owner's word (its image is a pull of about 1.3 GB onto the SD card): the load on the decoder
    and encoder cores in `/proc/mpp_service/load` while ffmpeg runs, with `load_interval` set first
    (step 4 of its notes' steps by hand), and the owner's look at the App Store's card and details
    of the installed Jellyfin, with RKMPP chosen in Jellyfin's dashboard (step 6). Step 2's open
    parts are 13 and 14.
13. Immich with photos (`apps.rk3588-hardware-acceleration`, step 2's (4) of its notes' steps by
    hand): upload about 30 photos (faces, text, one HEVC video), run Smart Search, Face Detection
    and OCR in Administration > Jobs, record their durations and search for dog; while the jobs
    run, the agent reads the NPU's load, the log's recognition lines, check.sh's thermal margin and
    `docker stats` (`apps.compose-stacks`'s (7), whose peaks go to
    `apps.container-resource-limits`).
14. RKMPP in Immich (`apps.rk3588-hardware-acceleration`, the owner's part of step 2's (5)): the
    owner chose it (Immich's system-config reads ffmpeg.accel rkmpp on 2026-11-17); left: confirm
    that the App Store's hint names that menu, Administration > Settings > Video Transcoding
    Settings > Hardware Acceleration, as Immich does.
15. `apps.install-progress`, the steps of its notes that wait for the owner: (3) Immich installed
    from the App Store in a browser on the LAN with its four images pulled (after an uninstall that
    keeps its data and a removal of its images: a pull of about 3.6 GB onto the SD card), the card
    with each image's bar and layers, a reload of the page and a second tab during the pulls, and a
    second install refused with 409 meanwhile; (3b) Cancel install on the card during the pulls;
    (4) a restart of the supervisor during Immich's wait for its database. The agent reads the job's
    API, the SD MiB and the audit log meanwhile.
16. The BPI-F3's old SATA SSD (the pool's devid 1 until 2026-11-19): it still holds a full copy of
    the pool as it was; the owner keeps or wipes it. The board's open checks of
    `deploy.riscv64-bpi-f3` and `storage.nvme-trim-on-k1` need this tree deployed there and a
    dev.env for the board with its UNOS administrator (the owner's).
17. A kernel with LIO's file backstore (`target_core_file`) on a board, for the device check of
    `shares.iscsi-targets`: neither the BPI-W3's vendor kernel nor the BPI-F3's has one.
18. `system.remote-access`, step 1: a port forward of UDP 51820 on the router to the dev device,
    remote access turned on, and a phone or laptop outside the LAN that imports a peer's
    configuration and reaches the web UI; a Mac's `dns-sd` for an app's name.
20. A second network card on the dev device (a USB NIC), for a real bond of
    `system.network-bonding-and-vlans`'s step 1, and `make audit` in the owner's terminal for
    `ci.pinned-check-tools` (pip cannot reach the index from the agents' sessions).
21. Before the next deploy of the web UI: `caddy:2-alpine@sha256:003a9780…` (Caddy 2.11.9) pulled
    onto the dev device, which `scripts/dev/deploy.sh build-webui` asks for before it builds on a
    new base (ADR-0446, ADR-0305).
19. Done on 2026-11-19: the pulls from lscr.io failed on the dev device's network, those from
    ghcr.io of the same images do not; the catalog takes LinuxServer's images from ghcr.io
    (ADR-0426). BookStack's login page after an install is still to read (one install, about 1
    GB on the SD card).

## Log

### 2026-11-21: The in-app updater, built up to its release installer (ADR-0443)

- `system.in-app-updater` (branch `system.in-app-updater`, from `mvp.batch-19`), `passes` false.
  The cause first: the backup before an update is `copy_database` (with the commits still in
  `unos.db-wal`; the base's `shutil.copy2` lost them, shown by a test on the base tree) into
  `/var/lib/unos/backups/in-app-<time>/`, which compose now mounts into the supervisor, and a
  rollback puts it back through the open database with the restore's `_replace_database`. Then
  Install Update (images, backup, switch, a health check of 300 s, an automatic rollback), Rollback,
  the automatic install at its interval, the progress over `/ws/v1/events` and System > Updates
  wired to them, shown only where the supervisor can install.
- Open for the owner (ADR-0443, the entry's notes): where a release's images come from while GHCR
  has none, and how the supervisor replaces its own container. Until then the release installer
  is `Undecided`: Install Update, Rollback and the automatic install answer its reason, and the tab
  names `update.sh`. The compose mount makes the entry host-level: its device check waits for an
  installer and a release.
- Gates: `TEST_WORKERS=4 make check` exit 0 (10797 passed, 124 skipped, 1 xfailed). E2E on the
  stand-in: `updates.spec.ts` 14 passed, with the Updates views of `mobile-layout.spec.ts`,
  `information-architecture.spec.ts` and `times.spec.ts` 23 passed after the review's fixes; before
  them the seven specs that visit the tab ran 314 tests, 312 passed, and the 2 failures (Russian
  Users cut off by the boot screen, and this change's own view waiting for an English heading,
  fixed) passed on a rerun.
- Review in a fresh context: 7 should-fix and 6 nits, all fixed: a rollback offered after update.sh
  installed another version (now only while the newest backup is of the version that runs), no
  deadline on the installer's steps (health checks cut off at 300 s, an hour for images, 700 s a
  switch), the rollback's restore without the restore's post-restore step (now under smb_lock with
  it), a stale next look and a layout shift on the tab, the race of two installs and the compose
  mount untested, corrupt backups, text-only lifespan tests, the automatic job cancelled at a stop,
  a dev build that could install its own release (downgrade) or an unknown version, and texts.
- Seen, not changed: `test_api_times.py` fails after `test_state_files.py` in one worker process
  on the base tree too (`/api/v1/health/raid-monitor/status` and a pool `tank`), an order
  dependence of those two files.

### 2026-11-21: Changes to existing tables ship as Alembic migrations (ADR-0444)

- `architecture.database-migrations`, at the lead's word on the owner's ("do it yourself", "decide
  yourself", in translation): Alembic 1.20.1 pinned in `supervisor/requirements.txt` and installed
  into the worktree's `supervisor/.venv` with the pip step of `make setup` (pip waited about a minute
  per new connection: the workstation's IPv6 to PyPI's file host times out, IPv4 answers).
  The baseline `0001` of today's schema (IF NOT EXISTS) in `supervisor/src/migrations/versions`;
  `init_db` brings unos.db to the head first in the lifespan, in one SQLite transaction; no
  create_all in `supervisor/src`; a restore upgrades the backup's copy before the swap and refuses
  an unknown revision. ADR-0444 supersedes ADR-0022 and ADR-0319 in part. `passes` stays false
  until the deploy (the entry's notes say what the device must show); the deploy rebuilds the
  supervisor image from its pip layer on.
- Review (ADR-0017, a fresh subagent), 9 findings, all fixed: a test of test_smb_models.py left
  expecting no alembic_version; a hostname in this entry read as a feature id; CHECK constraints
  and collations that autogenerate does not compare (now each table's clauses against
  create_all's); the dev device's smb_state of ADR-0319's older layout taken as the baseline
  silently (now a warning names it, and the baseline's SHA-256 is pinned); several rows of
  alembic_version in a backup; the restore's upgrade after the swap (now of the copy, before it);
  a deferred BEGIN (now none at the head, BEGIN IMMEDIATE otherwise); the deploy's pip layer
  rebuild not stated; decided-by and ADR-0022's partial supersession.
- Gates: `TEST_WORKERS=3 make check` passed: 10786 passed, 124 skipped, 1 xfailed, the lint and
  the frontend. A run before it hung in `test_smb_startup.py` TestFirstRequest at the lifespan's
  shutdown, under a load average of 85 from other worktrees' gates; that file passes alone and
  after `test_migrations.py`, and the rerun passed.

### 2026-11-21: The whole history is scanned for secrets by gitleaks pinned by digest (ADR-0445)

- `ci.secret-scan-of-history`: `make secret-scan` (`scripts/secret-scan.sh`) and the Secret Scan
  job of the CI workflow run gitleaks v8.30.1 from its image pinned by digest, without the
  network, over every commit that HEAD reaches, merges by their remerge-diff, with gitleaks' rules
  and two of `.gitleaks.toml` (sshpass's password, a literal assigned to a secret's name), and
  `scripts/published-passwords.py`, which finds the two passwords once published by their SHA-256.
  Findings show rule, file and commit only. The first local run: gitleaks read 635 commits and
  found 55 findings in 23 pairs of commit and file, allowed by commit and path with their reasons
  (the rotated passwords only in `CLAUDE.md` of da193b8b); the digest check read 839 commits, its
  16 pairs allowed. Step 3 of the entry names the allowlist instead of a rewritten history.
- Reviewed in a fresh context: 9 findings, all fixed (gitleaks' commit count, which skips
  deletions, renames, modes and binaries, replaced by its ERR lines; the guard's secret names in
  the rule; merges scanned; tests with the real image; the digest check over the history; the
  entry's ADRs; the template's exact text; no file-wide skip; a doc's wording).
- `passes` stays false while the CI workflow is off (ADR-0230).

### 2026-11-21: Both images are scanned weekly by trivy pinned by digest (ADR-0446)

- `ci.container-image-scanning`: `make image-scan` (`scripts/image-scan.sh`) and the Image
  vulnerabilities job of the weekly Audit workflow build the supervisor's and the web UI's image
  and scan each with trivy 0.76.2 from its image pinned by digest, from an archive and without
  the network: a HIGH or CRITICAL vulnerability with a fixed version fails, minus the entries of
  `.trivyignore.yaml`, each with its reason and expiry. The first local run failed with 143
  findings (Debian's kernel headers of a cached apt layer, Alpine 3.22's packages, Caddy 2.10.2's
  Go modules, the vendored packages of setuptools 79.0.1). The scan now builds without the cache,
  as update.sh does; `caddy:2-alpine` moved to its digest of 2026-11-21 (Caddy 2.11.9 on Alpine
  3.24) and the pip layer pins setuptools 84.2.1, after which it passed with 3 entries ignored
  (pip's SBOM; Caddy 2.11.9's x/net and Go, until 2026-12-21). `python:3.11-slim` keeps its pin:
  its newest digest changed only the riscv64 image.
- Reviewed in a fresh context: 6 findings, all fixed (trivy as the calling user, which Linux
  needs without capabilities; findings exit 3, apart from trivy's own errors; the build without
  cache and the base pin kept; the owner's pull of the Caddy base, Open owner actions 21; the
  ignore file's date check; the reason the SBOM entry has no path).
- Not deployed: the new Caddy base and the setuptools pin rebuild both images on the dev device
  at the next deploy, hundreds of MB on its SD card (ADR-0214). `passes` stays false until the
  job has run on GitHub.

### 2026-11-21: mypy checks supervisor/src in make lint (ci.python-type-check, ADR-0447)

- mypy 2.4.1, hashed in `.github/lint-requirements.txt` and in `.venv-lint`, runs in `make lint`
  over `supervisor/src` with `mypy.ini`, against `supervisor/.venv` (stubs of croniter, docker and
  psutil in `requirements-dev.txt`); the CI Lint job installs those packages and runs `make lint`.
  The code meets it, no module exempted: `Field(default=...)`, `Mapped[...]` models with the same
  tables, narrowed Optionals. `passes` stays false: the Lint job's failure on a type error cannot
  be seen while CI is off on GitHub (ADR-0230).
- Review in a fresh context: no blocker; fixed: the supervisor's unhashed packages now go into a
  venv of their own after the audit in CI, reasons on the older `type: ignore`s, a split table
  row and stray blank lines, docs (AGENTS.md, supervisor/AGENTS.md, Dependabot, CHANGELOG), the
  ADR's claims, `MYPYPATH` in the green-run rules and a test case for `mypy_path`. Left: the test
  cannot see a missing `python_version`, which the Python of `.venv-lint` (3.11) hides.

### 2026-11-21: Every page on the shared components; the design check keeps no baseline (ADR-0438)

- At the owner's word (the design was left half done), two builders moved the data and sharing
  pages (873f6b63) and the apps, containers, System, Account, sign-in and layout pages (692746cc)
  onto `webui/src/lib/components/ui` (ADR-0432): every raw checkbox and radio, the hand-made
  selection bars (BulkBar), long dialogs and details (Sheet), menus, tabs, loads, pills and
  single-question dialogs (confirm()), and success notices as toasts. Each reviewed in a fresh
  context (11 and 10 findings, fixed but for the ones in their commits). Merged as 39e67336 and
  58b87177.
- a0f852f3 (ADR-0438): the design check's baseline is gone, and success Alerts, spinners and raw
  `<details>` fail unless a `design-ignore` gives the reason; the Button tooltip got the token
  `surface-inverse` instead of a theme.spec exception. At the owner's word ("remove this line from
  everywhere") the Live/Disconnected line left the dashboard and the sidebar, with its spec
  assertions. `make check` 10765 passed; full E2E 1163 passed, 2 failed (a class `times.spec`
  reads, restored, and a sign-in timeout of shares.spec that passed alone), 14 skipped.
- Deployed at the owner's request to the dev device and the BPI-F3 (Dev device).

### 2026-11-20: A seventeenth batch: ruff, ghcr.io, Svelte 5, the redesign, TRIM; the BPI-F3 moved

- A diagnosis first: the transmission image of the catalog's pin came from ghcr.io in 10 s
  (147.820 SD MiB) where every pull from lscr.io had failed. Three builders in parallel worktrees
  from main 2ac1cbb (ruff's rules, the LinuxServer images on ghcr.io, Svelte 5), then, at the
  owner's word ("together with Svelte, refactor and redesign the interface"), a design proposal
  with a mockup shown to the owner, its implementation on top of Svelte 5, and TRIM for NVMe
  drives of a K1 after the BPI-F3's check below. Each reviewed in a fresh context by its own
  builder, which fixed its findings. Recorded, not fixed: the updater's rollback reports success
  when its build or up failed, and 51 findings of ruff's ASYNC rules (`ci.wider-ruff-rules`); the
  two catalog tags that moved upstream (traefik v3.7, Linkwarden's postgres 16.16-alpine) need a
  re-pin; the redesign keeps six confirmation dialogs as their pages had them, Button's touch
  size for coarse pointers only, and the Security split and Disk Health's move for later
  (`webui.redesign`); ADR-0370 and the F3's other docs still describe its SATA pool.
- Fixed at the merge: an image's removal answered 500 after 60 s on the SD card while Docker
  removed it (3b0ba0b: it waits up to 600 s); BookStack's disk estimate was a quarter short; the
  ML probe's test collided with a timing of 0.012 s; a stray `node_modules/node_modules` link
  loaded Playwright twice; the deploy tree's `webui/node_modules` was Svelte 4's (`npm ci`). The
  builders' E2E port prefixes 71 and 72 were above 65535. `make check` 10707 passed, 124
  skipped, 1 xfailed; the full E2E run 1100 passed, 14 skipped.
- Device check of fc42813 before and after a reboot from the API at 18:10:24 UTC: no lscr.io
  image in the catalog; BookStack installed from ghcr.io (968.223 SD MiB) and uninstalled;
  transmission updated to the pin, rolled back and updated again. The new web UI's pages and
  scripts answer 200 on the device.
- The owner's BPI-F3, at the owner's word ("the F3 goes onto one M.2 SSD, the system onto the
  eMMC, ..., the SD wiped", in translation): the system copied by `armbian-install` to the eMMC
  (its built-in check of OpenZFS's versions hung on the board's broken IPv6 until wget was made
  IPv4-only; it wrote the SD's root UUID into extlinux.conf, dropped the pool's fstab line and
  copied no `/mnt`: fixed by hand), booted from the eMMC with the SD out; the SD wiped and the
  pool's apps (52 MB), Docker's volumes with UNOS's database and the needed images (5.4 GB) saved
  to it, checked by sha256 read back from the card; the owner put a Kingston A1000 M.2 NVMe drive
  in place of the SATA adapter; its exFAT partition (a 16 GB installer folder) wiped at the
  owner's word; the pool `data` made on it (btrfs, one disk) with UNOS's fstab entry and restored;
  TRIM checked (256 MiB, all free space, a 4 GiB write with a parallel fstrim, a warm reboot: no
  NVMe error) and turned on (`discard=async`, `fstrim.timer`); the SD erased with blkdiscard.
  The board's UNOS is still the older version of 2026-11-13.

### 2026-11-19: A sixteenth batch: off-device copies over REST and SFTP, pinned actions and tools, virtual interfaces

- Three builders in parallel worktrees from main 4575c68, each reviewed in a fresh context by its
  own builder, which fixed its findings. The lead had the pins' builder leave the supervisor image
  alone: `--no-binary=nut2` goes on the command lines, so the image's pip layer is not rebuilt on
  the SD card (about a hundred MB), and pip's default already takes the pinned wheels there
  (ADR-0423). Recorded, not fixed: the audit with wheels only could check another version than the
  image builds of a package that is not pinned and has no wheel (ADR-0423); NetworkManager and
  ifupdown hosts, the inert boot unit after an uninstall, and Docker's macvlan networks on a card
  that becomes a port (`system.network-bonding-and-vlans`); the private key is not in the device
  check's log scan (`backup.off-device-copies-over-sftp`).
- Merged as ADR-0422 to ADR-0424 in that order. `make check` 10631 passed, 124 skipped, 1 xfailed;
  the full E2E run 1093 passed, 14 skipped. By the lead at the owner's word: the triage labels
  made on GitHub, its setting that deletes a merged pull request's branch turned on, the 86 merged
  remote branches deleted, 170 clean local worktrees and 12 local branches of merged work removed.
- Device check of 988bf19 (SD MiB per step in each entry's notes), before and after a reboot from
  the API at 14:41:15 UTC: a REST server and a second sshd on the device's LAN address as copy
  targets, each saved, run with its check and restore test, restored from and refused at a changed
  certificate or host key; a bridge without ports and a VLAN on the uplink, its revert, kept, back
  after the reboot and deleted, with every LAN check OK; `scenario.sh all` (all areas but balance,
  whose step 10 failed twice: after Resume the balance of 13 to 14 chunks finished before Cancel
  took effect, the check's timing on a pool with little data, not shown to be UNOS's, open); the
  audit chain unbroken over 3213 entries. One fault of the check's own tooling:
  `scripts/dev/restic-rest-server.py` refused restic 0.18's POST without a Content-Length; fixed
  with a test.

### 2026-11-19: A fifteenth batch: API documentation, iSCSI, the data directory, remote access; the owner's open checks

- Four builders in parallel worktrees from main 9061fb0, each reviewed in a fresh context by its
  own builder, which fixed its findings. Recorded, not fixed: the derivation of a route's errors is
  a floor, the plain-dict answers are documented by example only, and the code weaknesses the
  documentation found are described as they are (`architecture.openapi-descriptions`); nothing
  tears iSCSI targets down at a host's shutdown, and the 3260 rule is added only at the firewall's
  Enable or Reset (`shares.iscsi-targets`); a container is reachable at its published ports by its
  own address where Docker's FORWARD rules come first, and SMB bound to chosen interfaces leaves
  the tunnel out (`system.remote-access`); `/run/samba` stays fixed, and step 2 says so now
  (`architecture.configurable-data-dir`, ADR-0419). The routes of iSCSI and remote access were
  brought to ADR-0417's documentation at the merge.
- Merged as ADR-0417 to ADR-0420 in that order. Fixed at the merge: the command palette lacked
  System > Remote Access; the tests of the live statistics used psutil's private named tuples,
  which the image's psutil 7.2.2 lacks (8 failures on Linux, none on the workstation's psutil
  5.9.8). `make check` 10380 passed, 124 skipped, 1 xfailed; the full E2E run 1072 passed, 14
  skipped.
- Before the batch's deploy, at the owner's word, the checks that waited for them and need no
  hardware of theirs, on main 9061fb0, before and after a reboot from the API at 05:57:39 UTC:
  Paperless-ngx and Linkwarden installed with their companions (3524.414 and 3029.160 SD MiB),
  checked, rechecked after the reboot and uninstalled with their data kept, their images removed;
  BookStack's install failed at Docker's pull of an lscr.io blob (587.672) while curl fetched the
  same blob in 0.3 s; GET /api/v1/health within 0.062 s during the pulls and a stop of
  transmission; a bind mount's rename and move answered 409; the NFS parser against exportfs; the
  supervisor's root filesystem in /dev/shm again and the Linux-only tests (test_share_repair.py 68
  passed; 8 failures of psutil's tuples, fixed above); transmission's update failed a fourth time
  at the pull (148.309). Found and fixed: Linkwarden's start wrote Prisma's cache into its layer
  (now a tmpfs), and the estimates of the system disk were 3% and 8% short (3ab6dde).
- Device check of 15cf31d, before and after a reboot from the API at 08:21:50 UTC (SD MiB per step
  in each entry's notes): the schema on the device; the data directory where it was; remote access
  on, a peer in a network namespace through the tunnel and not without it, the LAN unreachable
  through it, with the firewall on, revoked, off, and back after the reboot; app names announced
  and served over HTTPS; the NFS parser again; the Linux-only tests 106 passed; `scenario.sh all`
  (all 12 areas, 23.505 MiB); the audit chain unbroken over 3072 entries; every LAN check OK. One
  fault of the device and one of UNOS: the device's vendor kernel has LIO's core and iSCSI modules
  but no backstore, so no target can be served, and a create there made its file, secret and rows
  before failing with 500. Fixed with a test that failed first (82346c1: 409 before any change)
  and rechecked on the device after its deploy.
- The owner's BPI-F3, switched on by the owner at about 09:15 UTC: its pool `data`, the btrfs
  RAID1 on its two SATA SSDs (ADR-0372), does not mount because its second SSD is not there (the
  adapter's second port has no link): `btrfs device ready` fails, so Docker, whose data root is on
  the pool, and UNOS stay down by design. docker.socket retried the start every 15 s, which wrote
  0.133 SD MiB in two idle minutes; stopped until the next boot, 0.016. Its thermal zones read 70
  to 97 C within ten seconds at a load of 0.1 with the fan at 3 and 4 of 4, too fast a swing for a
  real temperature. Its kernel (6.18.45-current-spacemit) has no LIO.
- The BPI-F3 again, after the owner reconnected its disks and rebooted it (about 10:27 UTC): the
  SSD of the pool's devid 1 on the adapter's second port, the first port without a link, the
  second SSD (devid 2) seen on neither. At the owner's word ("Mount it degraded") the pool was
  mounted by hand at 10:51 UTC with `degraded,noatime` (0.000 SD MiB), outside UNOS and its fstab
  entry, which stays as it was: the mount does not survive a reboot, by the owner's choice ("Leave
  it for now") over converting the pool to one disk. Docker and UNOS started (0.992): both of
  UNOS's containers and the Home Assistant and Frigate stack healthy, /api/v1/health 200. A scrub
  of devid 1 found no errors in 13.82 GiB (38 s, 0.148), and its error counters are 0. Under a
  load of about 2 the thermal zones read 39 to 64 C with the fan at 1 and 2 of 4, cooler than at
  idle in the morning. `scripts/dev/check.sh` on the board (0.023): its idle CPU and the
  containers' within budget, and two problems: the inbound IPv6 filter (ADR-0301) was never put on
  the board, and the K1's top and gpu zones have their first passive trip at 40 C, which drives
  the fan, not the CPU's clock, so the check's warning for them is a false one. Fixed the same day
  (ADR-0421, superseding ADR-0374): the margin counts only the zones that bind a cooling device
  other than a fan, and every zone is a problem 5 C before its critical trip; reviewed in a fresh
  context, whose findings (cluster1_thermal unwatched, user_space, an unreadable type) are fixed;
  check.sh then shows on the BPI-F3 only its missing IPv6 filter, and on the BPI-W3 check: OK.

### 2026-11-18: A fourteenth batch: hot-plugged SMART, companions, no floating images, host commands and firewall

- Four builders in parallel worktrees from main 140adad, five entries, each reviewed in a fresh
  context by its own builder, which fixed its findings. Recorded, not fixed: UNOS's firewall rules
  come after the host's own; an address outside the LAN gets a warning before Enable, not a
  refusal; Disable is not refused while a revert runs; the AST check does not look inside
  run_in_container's argv; with ufw on, containers that reach the host's ports through
  host-gateway (Open WebUI to Ollama, Zigbee2MQTT to Mosquitto) may be blocked
  (`security.host-firewall`, `architecture.host-command-layer`); the companions' containers make
  small writes into their layers at a start, and Mosquitto's template binds an empty folder over
  its config (`apps.catalog-companions`); a ValueError at an install ends the job as a generic
  failure, and ADR-0408's sentence on floating apps is stale (`apps.no-floating-image-paths`);
  `scenario.sh smart` runs outside the wrapper (`storage.hot-plugged-disks-smart`); ADR-0002 still
  names lm-sensors. Flaky: test_ws_stats's read of the listed processes, and a hang in a `make
  check` run that a rerun passed.
- Merged as ADR-0414 to ADR-0416 in that order. The host-command layer's SMART half is ADR-0414's:
  smartctl stays in the container and makes the host's node. A disk removal already holds a
  scheduled shutdown (ADR-0407's btrfs jobs). `make check` 10075 passed, 124 skipped, 1 xfailed;
  the full E2E run 1029 passed, 14 skipped.
- Device check of cbe437c (SD MiB per step in each entry's notes): before a reboot from the API at
  22:38 UTC, the host's time zone, hostname, scheduled reboot and its cancel, journal and NFS
  through the API; ufw installed by the check, Enable with its revert, Keep, a rule added and
  deleted, the revert of an Enable that was not kept, and access from the LAN at every step;
  SMART and a short self-test of sda; the companions in the catalog, read-only; transmission
  offered its pinned version. Transmission's update failed at the pull a third time (lscr.io,
  55.539 MiB of partial layers). After the reboot, with ufw on and a failsafe unit armed: ufw
  active with its rules, access and check.sh OK, the failsafe removed, Disable; the same reads;
  `scenario.sh all` (all 12 areas, 23.426 MiB); the audit chain unbroken over 2814 entries. One
  fault of UNOS: Disable put back ufw's own FORWARD policies, so IPv6's stayed DROP where it had
  been ACCEPT. Fixed with a test that failed first (977b364: off4 and off6, ADR-0416; `make check`
  10085 passed) and rechecked on the device on 2026-11-19 after its deploy: Disable and the
  revert both put back DROP and ACCEPT. Not run: a USB disk attached while UNOS runs, the install
  of the three apps, a real update and rollback.

### 2026-11-18: A thirteenth batch: pinned catalog images, pool disks, load check, no mdadm

- Four entries built in parallel worktrees from main f0511fc, each reviewed in a fresh context
  by its own builder, which fixed its findings. Recorded, not fixed: the code paths of floating
  images are unreachable now (a new entry, `apps.no-floating-image-paths`), Traefik keeps its line
  tag `v3.7` since `v3.7.19` lacks riscv64, and the space estimate errs high on arm64
  (`apps.pinned-catalog-images`); a removal cannot be cancelled from UNOS, a scheduled scrub can
  start during one, an uneven layout can still run out of space midway (btrfs keeps the disk), and
  a replace, a profile conversion and a removal from a degraded pool are not covered
  (`storage.pool-add-and-remove-disks`); the 15% budget is compared on the printed average
  (`system.idle-load-budget`); ADR-0002 still names lm-sensors (`deploy.no-unused-mdadm`).
- Merged as ADR-0410 to ADR-0413 in that order; only the feature-pass table and the changelog met,
  joined by hand. A disk removal already holds a scheduled shutdown (ADR-0407's btrfs jobs).
  `make check` 9837 passed, 123 skipped, 1 xfailed; the full E2E run 1010 passed, 14 skipped.
- Device check of e5d531a (SD MiB per step in each entry's notes): before a reboot from the API
  at 19:12 UTC, check.sh's CPU and thermal lines, no md array, the pinned catalog with Immich
  offered nothing and transmission 4.1.4-r0-ls370, the update plans read with no pull, and the
  pool disks' refusals with no unit and no audit entry. Transmission's update was tried twice and
  failed at the pull both times (Docker's timeout awaiting response headers on lscr.io's blobs,
  63.582 and 14.516 MiB of partial pulls); it was never stopped. After the reboot: the same
  answers; `scenario.sh all` (every area passed, smart's sleep steps ran; 22.8 MiB in all); the
  audit chain unbroken over 2687 entries. No fault of UNOS. Not run: an add or removal that moves
  data (no spare disk), the dashboard tabs on the BPI-F3, a fresh install on old md disks, and a
  real update and rollback.

### 2026-11-18: A twelfth batch: tmpfs for small boards, scheduled power, update in place, security keys

- Four entries built in parallel worktrees from main 145632f, each reviewed in a fresh context
  by its own builder, which fixed its findings. Recorded, not fixed: restic's cache of an S3 store
  is unbounded in /tmp, and the 48 MiB limit of a backup's database holds on every board
  (`deploy.tmpfs-for-small-boards`); the power card reads the plan without polling, a damaged
  settings file leaves an alarm set before, a SMART self-test holds nothing, the UPS's
  low-battery shutdown stays with `system.ups-monitoring` and the Wake-on-LAN flag is unused
  (`system.scheduled-power`); the rollback relies on the current catalog's copy marks, the
  registry reads have no overall timeout and images built reproducibly are never newer
  (`apps.update-in-place`); turning two-factor authentication off removes the keys without the
  password, a registration refused after the touch leaves the credential in the authenticator,
  and an administrator cannot reset a regular user's two-factor authentication (ADR-0284)
  (`auth.security-keys`).
- Merged as ADR-0406 to ADR-0409 in that order. Joined by hand: the power plan's running jobs
  name an app's update and rollback (findings app_update and app_rollback, from the install jobs
  of ADR-0408), in the backend, the card and both catalogs. `make check` 9696 passed, 123
  skipped, 1 xfailed; the full E2E run 995 passed, 14 skipped.
- Device check of 09e792a (SD MiB per step in each entry's notes): before a reboot from the API
  at 15:07 UTC, the tmpfs and the database size, the power settings armed far ahead and put back
  (the RTC's alarm matched the plan, nothing logged a minute), the update's refusal paths with no
  container changed, and a software security key of a temporary regular user, audited with no
  secret. After it: the tmpfs, the alarm and the refusals as before; `scenario.sh all` (every
  area passed, smart's two sleep steps skipped while Immich uses the pool; about 23 MiB in all);
  the audit chain unbroken over 2577 entries. No fault of UNOS. Not run on the device: a real
  shutdown and wake, a real update and rollback (transmission floats on latest until
  `apps.pinned-catalog-images`), and a hardware key in a browser.

### 2026-11-18: An eleventh batch: audit of the rest, image platforms, served certificate, light theme

- Four entries built in parallel worktrees from main 4ed4ab3, each reviewed in a fresh context
  by its own builder, which fixed its findings. Recorded, not fixed: an Unmount that fails after
  the umount, an off-device copy whose second file write fails and the image that Docker pulls
  for a created container write no entry (`security.audit-admin-actions`); Readarr's develop tag
  publishes an empty index, so Readarr is unavailable on every platform, and 6 of the 67 apps
  publish riscv64 (navidrome, prometheus, zigbee2mqtt, pihole, traefik, gitea); a weekly job of
  the Audit workflow checks the record (`apps.catalog-platforms`); Ed25519 and P-521 keys are
  refused, OCSP stapling is off and the page has no upload form (`security.custom-certificates`);
  System is the default theme, the E2E suite runs in the light theme that Playwright's browser
  gives, and an account that may only set up 2FA gets 403 for the theme (`webui.light-theme`).
- Merged as ADR-0402 to ADR-0405 in that order; the audit's and the certificates' changes met in
  the audit schema, its catalog and the audit page and were joined by hand. `make check` 9388
  passed, 123 skipped, 1 xfailed; the full E2E run 969 passed, 14 skipped.
- Device check of f281749 (SD MiB per step in each entry's notes): before a reboot from the API
  at 10:15 UTC, the theme as a temporary regular user; the safe audit steps (power and UPS
  settings, a disabled webhook channel, the login history's no-op) with no URL in any entry; the
  catalog with readarr alone unavailable and its install refused with no pull; a generated
  certificate served across a restart of the web UI. After it: the certificate, the theme, the
  entries and the catalog kept; an uploaded leaf with its chain, a broken served.pem that fell
  back to the local CA with one log line, then the local CA again and the check's certificates
  deleted; `scenario.sh all` (every area passed, smart's two sleep steps skipped while Immich uses
  the pool; 22.7 MiB in all); the audit chain unbroken over 2454 entries. No fault of UNOS. Not
  run on the device: the firewall, mounts, container creates, pulls, suspend and the off-device
  settings; also left: the catalog's check on the BPI-F3 and the owner's look at the light and
  dark tokens.

### 2026-11-18: A tenth batch: JSON logs, resource history, design lint, times with offsets

- Four entries built in parallel worktrees from main b5b0060, each reviewed in a fresh context
  by its own builder, which fixed its findings. Recorded, not fixed: INFO is the default level,
  set by UNOS_LOG_LEVEL in /etc/unos/secrets.env; the emergency CLI writes through `_say()`; the
  four commit handlers of `shares/accounts.py` log with their traceback and rely on
  hide_parameters and the masking, which may mask more than a secret
  (`architecture.structured-logging`). The tab draws its charts only after the first stats
  message, and regular users no longer get processes or block device names
  (`system.resource-monitor`). The lint runs in `make frontend`, not `make lint`, and does not
  see arbitrary values, `[&_svg]` variants or `.ts` modules; Button defaults to type="button" and
  the warning button is orange now (`webui.design-system-lint`). A schedule's next_run stays in
  the host's zone; boot_time is a time now and last_boot comes from the uptime; ADR-0298
  (proposed) still names ADR-0237; the walk of GETs cannot reach those that need the host
  (`architecture.times-with-time-zones`).
- Merged as ADR-0398 to ADR-0401 in that order. MonitorTab kept the resource monitor's rewrite,
  on which the design check then passed (58 files); the time walk's floor came down to the 55
  GETs left after six monitor routes went (5412b79). `make check` 9073 passed, 123 skipped; the
  full E2E run 945 passed, 14 skipped.
- Device check of 5412b79 (SD MiB per step in each entry's notes): before a reboot from the API
  at about 06:40 UTC, every supervisor log line JSON, a sign-in silent and a wrong password one
  access line, no secret in the log; the users' times and 58 time-like fields with offsets, the
  audit chain unbroken over 2216 entries (185 older than the chain carry none); the resource
  history, a 1 GiB direct read on the pool seen at 65.7 MiB/s, the processes' users as ps has
  them, a regular user's socket without processes, 600 s idle at 0.086 MiB with the container log
  unchanged. After it: no point when the health answered and 6 a minute later, the log and the
  chain again, `scenario.sh all` (every area passed, smart's two sleep steps skipped while Immich
  uses the pool; 22.2 MiB in all). No fault of UNOS. Not run: an hour's 360 points (the smbconf
  scenario restarts the supervisor), the tab in a browser, the owner's look at the new tokens.

### 2026-11-18: A ninth batch: session cookie, copies into shares, dashboard layout

- Four entries built in parallel worktrees from main e8384c3, each reviewed in a fresh context
  by its own builder. Findings fixed: the token that earlier versions left in localStorage stayed,
  and the browser also sends the cookie to HTTPS apps on other ports of the NAS's name, which the
  session key now closes; Sign Out Again could end a newer session (`auth.token-storage`, two
  reviews); a folder swapped in at a copy's name would have been filled and set to 2770, and modes
  were taken before the source was opened (`files.group-writable-copies-and-moves-in-shares`, two
  passes); the stat cards' height, Refresh during a save, focus after Hide, Show and Reset, a
  failed save unannounced (`webui.dashboard-layout`). No blocking finding for
  `system.no-container-stats-endpoints`. Recorded, not fixed: only Chromium runs the cookie's
  specs; a 401 to the sign-out counts as signed out; a tab that misses another tab's sign-in goes
  to the login page; the legacy token's session is not ended but expires within 24 hours; the Vite
  dev server over plain HTTP cannot sign in; two identical concurrent layout PUTs may both write
  and audit; ADR-0388, ADR-0346 and ADR-0006 still describe the removed stats routes.
- Merged as ADR-0395 (dashboard layout), ADR-0396 (copies into shares) and ADR-0397 (session
  cookie). The full E2E run of the merge found one spec that expected a single 401 where the
  dashboard's Refresh now sends two requests; fixed in 991cb28. `make check` 8935 passed; the full
  E2E run 948 passed, 14 skipped, then `roles.spec.ts` 30 of 30 with the fix.
- Device check of 991cb28 (SD MiB per step in each entry's notes): `scenario.sh smb` 40 of 40 and
  `files` 17 of 17, the session cookie's checks through Caddy, the removed endpoints' 404s and the
  layout's write and audit before a reboot from the API at 23:24:54 UTC; `scenario.sh all` after it
  (every area passed, smart's two sleep steps skipped while Immich uses the pool), the layout kept
  and deleted, the 404s again; `linux-tests.sh tests/test_files_security.py -k 'default_acl or
  share'` 42 passed. No fault of UNOS. Not run: Firefox and Safari against the cookie.

### 2026-11-17: An eighth batch: palette, bulk actions, app limits, SMB interfaces, token scopes

- Six entries built in parallel worktrees from main 05d7bf5, each reviewed in a fresh context
  (the owner then asked to save tokens: the builders of `shares.smb-bind-interfaces`,
  `apps.container-resource-limits` and `auth.api-token-scopes` ran that review themselves and fixed
  its findings, and no second review followed). Findings fixed: regular users got the host's
  interfaces on the stats socket and Disk I/O counted zram, loop, dm and md (`system.dashboard`);
  a full /data lost the audit entries of a bulk delete, Restart selected restarted stopped
  containers (`webui.bulk-actions`); the palette's Russian layout, held key and hand-written
  dialogs were untested (`webui.command-palette`). Recorded, not fixed: the palette's
  `test_command_palette.py` and the SMB interfaces' E2E spec were written after their code, and
  the palette's builder once ran pytest directly; the dashboard still sends regular users the
  process list and block device names.
- Merged as ADR-0388 to ADR-0393, after main's PRs #88 (the Audit Log's long names on a phone),
  #89 (the smart check skips the sleep step while apps use the pool) and #90 (the sideways balance
  dialog test waits for the list to rest), all three merged into main that day. #89's ADR-0388 took
  the number 0394 in the batch's merge of main. `make check` 8752 passed; the full E2E run 926
  passed, 14 skipped, no failure.
- Device check of 320d3ec (SD MiB per step in each entry's notes): `scenario.sh smb` and `shares`
  before a reboot from the API at 19:23:11 UTC, `scenario.sh all` after it (187 steps passed, smart's
  sleep steps skipped), and every entry's steps before and after the reboot. One fault of UNOS:
  removing a memory limit of a running container answered 500, since Docker 29.9 with runc 1.5
  keeps the swap limit for MemorySwap -1 and runc refuses a memory limit above it; fixed in
  7f5dd29 (a swap limit twice the new memory limit; the fake Docker refuses -1 as the device did,
  and the tests failed before), deployed, and both removals then passed. Not run: a reinstall
  with a limit (transmission's image tag floats, so its install pulls) and the bulk actions'
  container steps on the device (the E2E specs show them).

### 2026-11-17: The balance dialog's check on a phone held sideways no longer depends on timing

- `webui.balance-dialog-sideways`, seen while `webui.audit-log-long-token-name` was built: "On a
  phone held sideways › Dialog: balance a pool fits the screen" of `mobile-layout.spec.ts`
  (`storage.pool-balance`) failed alone, 10 times of 10, and with `audit.spec.ts`, but passed in
  two full runs. The cause was the test's, not the page's: the view clicked the option "Every
  chunk (full balance)" while Select's list still flew in (150 ms). Playwright's click tried
  again, and the try that found the option at rest, the fourth, forced `scrollIntoView` with
  `block: 'start'`, which scrolled the dialog's overlay to its end (109 px at 667 x 375); the
  overlay kept that when the dialog grew, and the title was out of view. A finger scrolls
  nothing there. How many tries the list takes depends on timing, the likely reason for the
  passes in the full runs: on main 5623bc1 a full run failed the test the same way. Now
  `settled()` waits until the list has no animation left, and the first try taps the option
  without scrolling. Not host-level: no device check.
- Test first (ADR-0186): on main the test failed 3 of 3 alone and in a full run with
  Playwright's log. With the change it passed 10 of 10 alone, `mobile-layout.spec.ts` with
  `audit.spec.ts` passed (174), `make check` passed and `make e2e-local` passed (842, 14 skips on
  listed conditions); after the review's wording changes the three views of the balance dialog
  passed 10 times each. The review in a fresh context found no defect; fixed: the comment and
  the ledger's notes said that every later try scrolls (only the one that finds the element at
  rest does) and counted the tries one short, and the wait had no message. Kept as it is: the
  entry's category is `webui`, not `testing` as for the keyboard upload's flaky test, after the
  branch's name and the check of the web UI it fixes. Outside this change: in the full run on
  main, `security.spec.ts`'s "a failed reload of the certificates shows no stale list, and a
  reload clears its error" failed once (its row not found); it passed in the run with the change.

### 2026-11-17: The smart check skips the disk's sleep while apps use the pool

- `scripts/dev/scenario.sh smart` no longer fails its wait for the pool's disk to sleep when apps
  keep it awake (ADR-0394): after the seventh batch's reboot `scenario.sh all` failed only that
  step (sda active after 1980 s, 13 reads and 649 writes), caused by Immich, transmission and
  others with their data on the pool (ADR-0381's cost), which hid any real fault of
  `storage.disk-spindown`. Before the wait, after each of its rounds and once more after the last
  power read, the check now reads the disk's reads and writes and each running container's on it
  from its cgroup's io.stat (cgroup v2: the device runs Docker with the systemd driver on it),
  through `docker ps` and `docker inspect` under `timeout` alone. A disk that the Storage page
  shows active after the wait is a SKIP that names the containers when, in the wait's last 660 s
  (two rounds: longer than sda needs unused to sleep), the kernel counted reads or writes on it
  and containers other than UNOS's own (`unos-*`) did too, and UNOS's own did not; so is the next
  step, the SMART reads of the sleeping disk, and those reads are a SKIP when they find the disk
  awake after such a container used it. Every other case fails as before. Tests first in
  `supervisor/tests/test_dev_scenarios.py` (they failed on main with the device's FAIL).
- The review in a fresh context (three rounds) found these faults, all fixed with tests: an app's
  read or write anywhere in the 1980 s made the SKIP, so an app that wrote once early hid a fault
  that kept the disk awake for the 1650 s after it (now only the last 660 s count, and the power
  read that restarts the timer, or the scans turned on again, still fail after an early write); a
  state of `unknown` was skipped too (now only `active`); the SMART-read step turned any failure
  into a SKIP, an HTTP 500 among them (now only the disk found awake, or reads and writes
  counted); a failed `docker inspect` read like a host without that container, whose lifetime
  counts then looked like a use during the wait, at first for all of them and then, keyed by ID
  in one reading and by name in the next, for one (now every container is known by its name from
  `docker ps`, and one without a PID has no count); UNOS's own writes beside an app's made a SKIP
  (now a FAIL that names both); an app that wrote until shortly before a pass of the SMART monitor,
  which reads a disk used since its last read and so restarts its timer (ADR-0378), made a FAIL
  that blamed UNOS (a window of 1320 s, tried for it, hid that fault again in a run whose apps
  went quiet 990 s before the end; now the FAIL names the apps' use in the 660 s before and the
  monitor's pass, which a second run clears, since the check cannot see the monitor's passes); an
  app's write between the last reading and the last power read
  went unseen (now a reading follows it); and the inspects could outlast the device call's 300 s
  (now 120 s at most). Fixed too: UNOS's own containers are every `unos-*`, as the supervisor's
  `is_system_container` says, and the texts of ADR-0394, the CHANGELOG, the ledger's notes and the
  feature-pass row say what the code does. Left as is: ADR-0394 changes ADR-0378's device check
  without superseding the whole ADR, in the way of ADR-0291 and ADR-0298, since every other rule
  of ADR-0378 stays; a container started again whose new counts are above its old ones is counted
  short, and one that ran only between two readings is not seen (ADR-0394 says so).
- On the device, read only: the helper listed every container with its counts, and in two minutes
  Immich's database sent sda 22 writes while sda completed 42 (0.016 MiB); its final form, names
  from `docker ps`, again listed all seven containers (0.000 MiB). `scripts/dev/scenario.sh smart`
  with the first version: OK, 12 steps passed and 2 skipped, step 10 a SKIP after 1980 s with 0
  reads and 623 writes on sda, naming immich-database (335 writes) and immich-redis (68), the
  device as found (SD MiB 0.758 in all, 0.508 for the wait). With the final code: OK, 12 passed
  and 2 skipped, step 10 a SKIP after 1980 s with 0 reads and 688 writes on sda, naming
  immich-database (96 writes) and immich-redis (10) in the last 660 s; the timeout Off again and
  the offline data collection on, as found, and the device as the check found it (SD MiB 0.000 to
  0.113 for the other steps, 0.746 for the wait with its 16 readings, 0.309 for the restore,
  1.207 in all). The device's runtime is unchanged: the check runs on the workstation.

### 2026-11-17: The Audit Log fits a phone with an API token's name of 64 characters

- `webui.audit-log-long-token-name`, found while `auth.mandatory-two-factor` was built: on a
  375 px screen the Audit Log scrolled sideways when an entry named an API token of 64
  characters, the most the server takes, with no place to break. The supervisor writes the name
  into the entry's details ("Created API token: <name>"), which the page showed without
  breaking, and the user (a name tried at sign-in, up to the 128 characters the audit log keeps)
  and a full IPv6 address did not break either. Now the details break (`break-words`) and the
  user and the address break anywhere (`break-all`), as ADR-0302 asks and as the resource and
  the browser already did. Not host-level: no device check.
- Test first (ADR-0186): the view "Audit Log with long names" of `mobile-layout.spec.ts` serves
  three entries as the server writes them, the token's creation and deletion and a failed
  sign-in, each from a full IPv6 address. On the page of main 05d7bf5 it failed in both
  languages (the page scrolled 615 px in English, 678 px in Russian). A fresh-context review
  found that the view did not wait for the deleted entry and the failed sign-in, that the spec
  bounded the sign-in name at 64 characters (the audit log keeps 128), and fakes unlike the
  server's answers (the id, the hash chain, no browser): all fixed. `min-w-0` on the user and
  address items, which changed nothing, is gone. Kept as it is: an action the page does not know
  shows as it comes, without breaking. The server writes only the actions of `AuditAction`, and
  the longest older one (ADR-0359) fits; only a line edited in the file, which the page marks
  Chain broken, could carry a longer one.
- `make check` passed, and so did `make e2e-local` (842, 14 skips on listed conditions), both
  before and after the review's changes. Outside this change: "On a phone held sideways ›
  Dialog: balance a pool" of `mobile-layout.spec.ts` (`storage.pool-balance`) passes in the full
  run but fails when the spec runs without the others. It failed with `audit.spec.ts`, 10 times
  of 10 alone, and 3 of 3 alone with main's spec and Storage page: the dialog's title is out of
  view once Every chunk is chosen.

### 2026-11-17: A seventh batch: install progress, groups, required 2FA, quota use; faster gates

- Immich's smart search, at the owner's word: its CLIP model is nllb-clip-base-siglip__v1, which
  understands Russian, instead of the English-only ViT-B-32__openai (Immich's system-config,
  `machineLearning.clip.modelName`), and the search index was rebuilt to 768 dimensions; Immich's
  server stopped once during the rebuild with "could not open relation with OID 17716" (a race of
  Immich's) and came up clean at Docker's restart. Both nllb models load from their rk3588 files on
  the NPU; `scripts/dev/immich-ml-probe.py --clip-model nllb-clip-base-siglip__v1 --language ru`
  answered the picture in 46.19 s first (the models' load), then 0.373 s, the text in 0.049 s, and a
  Russian "photo of a dog" against the English one at a cosine of 0.988 (0.090 MiB). ViT-B-32 was
  then deleted from the model cache on the pool, at the owner's word: only nllb's 3.4 GB are left.
- The branch `mvp.batch-7` (from main dd95822) holds six entries, each built test-first in a
  worktree of its own by a builder, then reviewed in a fresh context (ADR-0017) as soon as its
  builder finished, its findings fixed, and merged; the ADRs that the builders all numbered 0383
  were renumbered at the merges: `testing.per-test-temp-paths` and
  `ci.pre-commit-reuses-green-run` (ADR-0383), `apps.install-progress` (ADR-0384),
  `shares.group-access` (ADR-0385), `auth.mandatory-two-factor` (ADR-0386) and
  `storage.volume-quota-usage` (ADR-0387); and `scripts/dev/immich-ml-probe.py`'s
  `--clip-model`, `--language` and `--text` (f577ef2), since Immich on the dev device searches
  with nllb-clip-base-siglip__v1.
- The gates are faster (ADR-0383): backend tests run in pytest-xdist workers, each process with
  temporary paths of its own, without the machine-wide lock; on the workstation the backend
  tests take 112 s instead of 719 s, `make check` 132 s instead of 746 s and `make check-fast`
  60 s instead of 324 s. A passing `make check` or `make check-fast` records the working tree's
  state, and the pre-commit hook skips `make check-fast` for a commit that holds all of that
  tree. A second review recorded a failing tree as green in several ways (a TEST_WORKERS value
  that reached pytest's command line unquoted, another makefile, tools from the environment, a
  write into `tmp/stop-check` by hand); each is closed and tested (cffcae9), and
  `.claude/settings.json` gained the deny rules `Write(//**/tmp/stop-check/**)` and
  `Edit(//**/tmp/stop-check/**)` (a tightening only). Disclosure: that entry's builder used
  `git commit --no-verify` once, in a scratch clone of its own without hooks, which skipped
  nothing there; the guard forbids the flag to agents.
- The reviews' findings, all fixed before the merges: the install job's waiting GET held
  uvicorn's shutdown back from the lifespan (both images and the stand-in now run uvicorn with
  `--timeout-graceful-shutdown 3`), installs could not be cancelled and a stalled pull held the
  one install slot (Cancel install, a 10-minute read timeout on Docker's pull stream), at most 4
  GETs wait at once (63056a1); a group's rename did not raise its shares' versions, a restore
  took a group whose name breaks the rule, the account page showed the wrong level of duplicate
  grants (955f46a); a stored 2FA requirement that cannot be read could fail requests, a start
  ahead of the clock could skip the grace period, a restore did not say that it turned the
  requirement on or off (a320b04); counts that need a rescan moved the quota alerts, points
  ahead of the clock stopped the history, a restart forgot the levels reached, a quota of 0
  bytes was not full (cf7b656).
- `make check` passed after each merge (about 2.5 minutes, 8144 tests and more), and the full
  E2E run on the stand-in passed before the deploy: 840 passed, 14 skipped, 16.1 minutes. The
  balance dialog of mobile-layout.spec.ts, flaky on main before the batch, passed in it. The 2FA
  builder found that the Audit Log page overflows on a phone for an API token of 64 characters, a
  bug from before the batch.
- The deploy of 7cca7e9 on 2026-11-17 at 12:47 to 12:49 UTC (SD MiB status 0, sync 6.777,
  build-supervisor 13.957, build-webui 17.262, up 5.652, check.sh 0.270, status 0). The device
  check (12:53 to 14:28 UTC; each step's SD MiB in the ledger's notes), with a reboot from the
  API at 13:31:12 (the boot of 13:32:28, both UNOS containers healthy by 13:33:21, check.sh OK
  with 12.9 MiB on the card since the boot): `scenario.sh volumes`, `shares`, `smb` and
  `hardware` passed before the reboot (0.875, 3.176, 5.031, 0.059); the install job through its
  API with restic-rest-server, a cancel during mosquitto's pull, a restart of the supervisor
  during heimdall's pull with a GET waiting, and Immich uninstalled and installed again without
  a pull, the same database and settings (`apps.install-progress`); the ML probe with
  nllb-clip-base-siglip__v1 and `--language ru`, the models loaded from their rk3588 files and
  none downloaded (0.066); the group's access over smbclient (`shares.group-access`); the 2FA
  requirement read, off (`auth.mandatory-two-factor`); quotas enabled on `data` (btrfs quota
  enable and rescan -w, inconsistent 0), `scenario.sh volumes` with the 1 GiB quota, and a volume
  of 100 MiB filled to 84, 92 and 97 MiB, each with one alert at the next pass (80% failed to
  send to Telegram, a ConnectTimeout from the device's network, recorded as failed; 90% and 95%
  sent), then down to 50 MiB, the problem gone and nothing sent (`storage.volume-quota-usage`).
  After the reboot: the group, the apps, the 2FA requirement and the quota history kept, and
  `scenario.sh all` with 185 steps passed and one FAIL, smart step 10: sda stayed awake for 1980
  s with 13 reads and 649 writes, which Immich, transmission and restic on the pool cause
  (ADR-0381's cost), not UNOS; the check put the disk's settings back as found.
- Two faults found by the device check. (1) A deleted volume's quota group was never destroyed:
  Linux 6.1.172 refused `btrfs qgroup destroy` as busy right after every delete, and the code
  only logged it, so the scenario runs left 14 stale groups, one with the 1 GiB limit. The group
  can be destroyed only once btrfs has dropped the subvolume and committed its counts (the device
  showed it after `btrfs subvolume sync` and `btrfs filesystem sync`). Fixed in 68c5724: each
  list of a pool's volumes by the volume usage monitor, after a change of the pool's generation,
  destroys the groups whose subvolume is gone (ADR-0387 rewritten in place); the tests failed on
  the old code. Its review in a fresh context found that a group that stays busy (counts that need
  a rescan, a deleted subvolume still open) would bring a destroy at every pass, each starting a
  transaction that moves the generation, so the disks would never sleep: 9ecd228 destroys a group
  only once two lists in a row found it without its subvolume and sysfs reads 0 for its counts, and
  gives a group up after three refused destroys. A second review found that the two lists could
  straddle a remount after which btrfs gives the id to a new subvolume: b17889f counts them only
  within one quota tree (the inode of sysfs's `qgroups` folder) and stops every pool's destroys
  after one that hangs. Not fixed, accepted in ADR-0387: another btrfs mounted over the pool's
  mountpoint between the RAID monitor's read and a destroy could lose a group of the same id that
  counts nothing. The re-checks on the device, quotas on: 73f547a destroyed a deleted volume's group
  and the four that `scenario.sh volumes` left at the next pass; 9ecd228 left a deleted volume's
  group, reading 0, after the first pass and destroyed it at the second, did the same with the
  four groups of `scenario.sh volumes` (0.984), and after a reboot from the API at 15:22:55 UTC (the
  boot of 15:24:14) destroyed the group of a volume deleted just before it by the second list after
  the start, with no warning in the log; b17889f, deployed at 15:31 UTC, kept a deleted volume's group
  after the first pass and destroyed it at the second, of the id 257 that btrfs had given out
  again after the reboot (the highest live subvolume is 256): the reuse that ADR-0387 guards
  against. Each step's SD MiB is in the entry's notes. check.sh 23 s after that boot found the
  containers still starting, and OK at 15:30. (2) A GET that waits for an install's end gets 500
  at a stop of the supervisor, with an "Exception in ASGI application" error in the log, when uvicorn cancels it
  at its graceful bound: ADR-0384's choice, now named among its consequences; the web UI never
  waits that way.
- Left on the device: containerd's content store on the SD card holds about 60 MB of blobs of
  the cancelled pulls (no image record; pruning them is the owner's call), the
  supervisor's `/data/volume_usage.json` (306 B), the notification history's 3 alerts and the
  audit log's entries of the checks, and 60 dangling images (the layers of the supervisor's
  builds). Quotas are off on `data` again (btrfs quota disable), as before the check.
- Passing now: `shares.group-access`, `auth.mandatory-two-factor` (not host-level; the
  requirement stays off on the dev device, the owner's choice) and `storage.volume-quota-usage`,
  with `testing.per-test-temp-paths` and `ci.pre-commit-reuses-green-run` (passing since their
  merge). `apps.install-progress` stays false: Immich's install with its images pulled and its Cancel during the pulls, the App
  Store's card with a reload and a second tab, a second install refused during the pulls and a
  restart during Immich's wait for its database wait for the owner (Open owner actions, 15).

### 2026-11-16: Immich of four containers on the dev device, its machine learning on the NPU

- `apps.compose-stacks` (ADR-0381, ADR-0382), built test-first on its branch: an app of several
  containers installs, lists and changes as one app through the Docker API, with images pinned by
  tag and digest, a database password generated on its pool and checks of the host ports and the
  system disk's space before any pull. Immich is its server, database, Redis and machine learning,
  the last on the RK3588's NPU with the -rknn image (step 2 of
  `apps.rk3588-hardware-acceleration`). The branch was merged with main ce5f86c (the sixth batch)
  as 41ad495. Before the deploy `make check` passed (8048 passed, 118 skipped, 1 xfailed), and so
  did the E2E specs apps, apps-stacks, apps-hardware, appstore and storage on the stand-in (121
  passed).
- Device check (the dev device, kernel 6.1.172, RKNPU driver v0.9.8, the pool `data` on sda; times
  UTC; each step's SD MiB; the deploy's MiB in Dev device), the pulls, the installs, the app
  actions and the reboot at the owner's word, run by the agent through the API. On 2625f84 at
  20:07, read only: check.sh OK (0.016), 24206 MiB free on the system disk, port 2283 free, 3
  Docker volumes, and the NPU's render node at its stable name (0.031). Then the deploy of 41ad495
  at 21:38 to 21:40.
- The install at 21:40: the catalog gave the server rk3588-video and the machine learning
  rk3588-npu with the -rknn image (0), `scenario.sh hardware` passed its 7 steps (0), and the
  install answered 200 in 299 s with the three companions, the machine learning with `/dev/dri` and
  the server with the five video nodes (3661.938 MiB, the four images' pull included). All four
  containers ran, healthy where they have a health check, by 21:46:24, with pong, 3.4.1 and GET
  /apps/installed listing Immich (0.230); docker inspect, the password file, the network and the
  listeners as the entry's notes ask, and docker diff naming only the mount points `/data`,
  `/run/secrets` with its file and `/cache` and their parent `/run` (0.895). The probe of Immich's
  machine learning answered 200 to every request: the picture first in 32.17 s (the models'
  download into the new model-cache on the pool), then a median of 0.281 s, the text first in
  1.49 s, then 0.025 s (0.129); the NPU's load during a second probe Core0 61 to 75%, Core1 6 to
  18%, Core2 0 to 4%, in 10 of 10 reads (0.016); RKMPP in Immich's server, a decode with
  `-hwaccel rkmpp` and an hevc_rkmpp encode, exit 0 (0).
- That run printed 7 FAILs, all faults of the check script, not of Immich, as its recheck showed:
  (a) the machine learning's image, tested for "rknn" in its Config.Image, which Docker fills with
  name@digest without the tag (the image's RepoTags name
  ghcr.io/immich-app/immich-machine-learning:v3.4.1-rknn, and its digest is the -rknn one that
  `supervisor/src/apps/catalog.py` pins); (b) the six "runs on the NPU" checks, which read the log
  line by line, while Immich's machine-learning log (Python's rich) wraps each record at 80 columns,
  so that a line holds "Loaded RKNN model from" and the model's path follows on the next lines. The
  script was fixed: it reads the tag from the RepoTags of the container's image, and it takes the
  colours off the log and joins each record's continuation lines before it picks the RKNN lines. Its
  recheck at 21:49 to 21:50 passed all 24 checks (0.730, 0): the -rknn image, and all six models
  loaded from their rknpu/rk3588 files (CLIP's visual and textual ViT-B-32__openai, buffalo_l's
  detection and recognition, PP-OCRv5_mobile's detection res736 and recognition), with no failure
  line. The grep of the log in `apps.rk3588-hardware-acceleration`'s notes had the same fault, and
  the log's colours, which rich puts into a path, could hide a failure line from it: like the
  script, it now takes the colours off and joins each record before the grep (tried on the
  workstation on a log that rich wrote with the settings of Immich's handler, not yet on the
  device).
- Its life, 21:50 to 21:53: stop (2.254), start (2.785) and restart (5.023) answered 200, docker
  events showed the starts database, redis, machine-learning, immich and the stops in reverse, and
  all four were healthy again after the start and after the restart; GET /audit/logs had the entries
  and transmission still ran (0.016); the uninstall left no container or network of Immich and kept
  its folders and the password file, 600 root 64 (3.695); the second install answered 200, healthy,
  with no 'password authentication failed', and used the same database: its version_history's first
  record (2026-11-16 21:46:29 UTC, 3.4.1) and its administrator state were the same before and after
  (6.059). The entry's notes ask that the administrator still signs in; the owner had not registered
  one (isInitialized false at 21:52 and at 21:53), so the check compared the database's own
  first-start record instead.
- 30 idle minutes, 21:53 to 22:24: 0.547 MiB. docker stats then: immich 577 MiB and 0.06% of a
  core, immich-machine-learning 144.9 MiB and 0.41%, immich-redis 5.664 MiB and 0.92%,
  immich-database 70.42 MiB and 0.00%, unos-supervisor 103.2 MiB and 0.24%, transmission 14.7 MiB
  and 0.08%, unos-webui 12.57 MiB and 0.00%.
- A reboot from the API at 22:24:02 (0.027), the boot of 22:25:22, check.sh OK (0.016): all four
  running and healthy by 22:26:35, RestartCount 0 each, pong (0); docker inspect, the password
  file, the network and the listeners as before (0.773); `scenario.sh hardware` 7 steps (0.125); a
  probe answered 200, its first call in 4.25 s, the medians 0.270 s and 0.024 s, with the six
  models loaded from `/cache` on the NPU and no failure line (0.055); no Traceback or Killed in the
  supervisor's log, and check.sh OK (0). After the reboot `scenario.sh hardware` read GET
  /apps/installed for the apps' names, which it compares at its start and its end and does not
  log; the logged read of 23:55 below shows the listing.
- The pool's disk, 22:28 to 22:48: sda got the shortest offered idle timeout, 5 minutes (0.105),
  with Immich running; 40 reads 30 s apart found it active/idle each time, with writes between every
  two reads (its count of completed writes rose from 112 to 467, its reads not), so that no stretch
  without I/O came near the 5 minutes (0.219); then Off again, as found, and the rule file gone
  (0.074). This is ADR-0381's cost: the pool's disk stays awake while Immich runs.
- GET /apps/installed at 23:55:53, read only, in the boot that the reboot started (the boot id,
  logged at 00:03:56, the same as at 22:26:01, with RestartCount 0 in Immich's four containers and
  in transmission, 0 MiB): 200, two apps, immich and transmission, both running; Immich once,
  running and healthy, with the five video nodes, its companions database (running, healthy), redis
  (running, no health check) and machine-learning (running, healthy, `/dev/dri`), each by its
  image's digest, and none missing.
- `apps.compose-stacks` passes, its verified_by now `test_app_stacks.py`, `test_apps.py`,
  `test_audit_storage_and_apps.py`, `apps-stacks.spec.ts` and `apps.spec.ts`: every step of its
  notes' device check passed on the dev device, but for the owner's items and (6)'s sign-in, named
  below, and its optional (3), the pull measured alone: (1) to (8) before the reboot, but for (7)'s
  pool disk, (5)'s one Immich with its three companions and (8)'s listing of the apps installed
  before, which were shown after it, and (9) after it. (9)'s GET /apps/installed by the read of
  23:55, which also shows the one Immich with its three companions running that (5)'s logged
  listing, cut at 1500 characters after the companion database, did not (so after the reboot only),
  and transmission listed and running beside it, as (8) asks of the apps installed before. A port
  conflict and a failed install, of steps 2 and 3, are the backend tests' (`test_app_stacks.py`,
  `test_apps.py`), not the device check's. The owner's items of its notes, the administrator's
  registration and docker stats while Immich's jobs run, are no condition of its three steps, nor is
  (6)'s administrator's sign-in, for which the database's own first-start record and no 'password
  authentication failed' stand: a judgement the owner may overrule (Open owner actions, 12).
  `apps.rk3588-hardware-acceleration` stays false, with `test_app_stacks.py` and
  `apps-stacks.spec.ts` added to its verified_by: step 2's (1), (2), (3), the server's part of (5)
  and (6) passed, and open are (4), the owner's photos and Immich's jobs, (5)'s RKMPP chosen in
  Immich and the names of the App Store's hint, and step 1's open parts (Open owner actions, 11, 13
  and 14). Of its (2), the reads of `/proc/device-tree/compatible` and `/dev/dri` inside the
  container were not run (the models loaded from their rk3588 files, and the log has no 'Could not
  read /proc/device-tree' line), and the log of (6)'s probe was not searched for a download.
- Review findings: the branch's history names the findings of its reviews in a fresh context as
  fixed (d587d32: an install that started every companion before it waited for any, and a rollback
  that removed the containers without a stop; 3071f26: the tests from the review of the web UI
  stage) and records none left unfixed. The first review of this record (9c3714f) in a fresh context
  found a false sentence (that nothing read GET /apps/installed after the reboot), the grep that
  could miss a failure line, and inexact wording (a sampling interval given as the longest stretch
  without I/O, health after each action where it was read after the start and the restart, the Now
  bullet's before and after the reboot, the listing of (5), a confirmation by hand that no log
  records, the sda and pool bullets' older reads): all fixed in b2c9dce. The review of the pass
  (e2a5c86) in a fresh context found claims wider than the device check (every condition of the
  three steps, before and after the reboot), (6)'s administrator's sign-in not named among the
  owner's items, (8)'s "still list" without its evidence, "merged into `main`" before the pull
  request, a boot id that no log held, `make check` named for a tree it had not run on, this bullet,
  the Dev device bullet's wording, the Now bullet's order and the state's time: all fixed in the
  next commit. The review of that fix in a fresh context found its "(1) to (8) before the reboot"
  false for (7)'s pool disk and for the listings of (5) and (8), shown after the reboot, (6)'s
  sign-in missing from the steps left aside, the order of steps 2 and 3, a count that read as a
  second count, and a `deploy.sh status` without a log: fixed in the same commit, with `make check`
  run again on its final tree.
- Open, the administrator first: Immich has no administrator, and until the owner registers one,
  anyone on the LAN who opens port 2283 can register as its administrator (Open owner actions, 12).
  Immich stays installed on the dev device. `make check` passed on the tree of this record's last
  commit.

### 2026-11-16: A sixth batch: the RK3588's video hardware, disk idle timeouts, a pool's balance, share options

- Four entries, each built test-first on a branch of its own on 2026-11-15, merged into the branch
  `mvp.batch-6` (not into `main`) with their ADRs numbered in merge order: Jellyfin gets the
  RK3588's video hardware for RKMPP through a hardware profile, unprivileged
  (`apps.rk3588-hardware-acceleration`, ADR-0377); data disks get an idle timeout kept in UNOS's
  udev rule, and nothing UNOS does periodically wakes a sleeping disk (`storage.disk-spindown`,
  ADR-0378); a pool's balance runs in a host unit, by default with a usage limit of 20% and a full
  balance behind a confirmation, with pause, resume and cancel (`storage.pool-balance`, ADR-0379);
  a share can require encryption, serve Time Machine and list its snapshots as Previous Versions
  (`shares.per-share-protocol-options`, ADR-0380, superseding ADR-0086 and ADR-0161). The merge of
  the disk branch renumbered the hardware entry's own ADR in its adr list, set right in d70dd12.
- Reviews in a fresh context before the device, the findings fixed with tests: the hardware profile
  applies only with Rockchip MPP's node, and a Jellyfin installed without it says so on its card
  and after the install; a balance's pause and cancel kept apart (the kernel pauses a balance with
  both asked), and a shutdown pauses a balance instead of cancelling it; the RAID monitor, the
  monitoring endpoints, the trash expiry and the SMART monitor leave sleeping disks asleep, and
  `install.sh` turns smartd off, whose reads every 30 minutes restart a disk's standby timer; the
  share options' checks at the render and the import, avahi-daemon's restart, and an smb check that
  asks Avahi itself.
- Device check (the dev device, kernel 6.1.172, the pool `data` on sda; times UTC; each step's SD
  MiB; the deploys' MiB in Dev device). The integration tree 57be517 at 01:02: `scenario.sh all`
  from 01:05 (20.160 MiB) passed 10 areas and failed two steps. `smart`: sda still active 990 s
  after its 5 minutes, with no read or write; the power read of GET /storage/disks, `smartctl -n
  standby -i`, sent an awake disk IDENTIFY, which restarted its timer (smartd also ran then).
  `smb`: after the last snapshot's Delete, Samba 4.22's smbclient reports smbd's empty list of
  shadow copies as NT_STATUS_INVALID_NETWORK_RESPONSE. Fixed on the branch: the power read is
  `hdparm -C` (97bc1a4; its reviews: EPC and SCSI power conditions, a hung read bounded, c9a2b68
  and 434d410), and the smb check proves Previous Versions with two snapshots and the SHA-256 of
  the version it opens (8921320, 784fb5f, 677610a). By hand then: Samba's vfs modules fruit,
  shadow_copy2 and btrfs installed (0.016), the 19 tests of `test_smbconf_testparm.py` passed with
  the device's testparm (0; the cycle script's FAIL line there looked for a summary line that
  pytest's `-q -q` leaves out), `storage.disk-spindown`'s step by hand (2), the rule's line with
  `-S 240` and udevadm test's RUN for sda (0.105), and `storage.pool-balance`'s (2), a full balance
  running on across `docker restart unos-supervisor` (1.418), and (3), pause, resume and cancel
  with their audit entries (0.047).
- RK3588 at 04:21 on that tree, at the owner's word for the image: Jellyfin installed (1262.047
  MiB, the image's pull), its container unprivileged with the five nodes, Docker's masks without
  `/sys/firmware` and AppArmor unconfined (1.137), ffmpeg in it decoding with rkmpp and encoding
  with hevc_rkmpp at 15.4x and h264_rkmpp at 16.9x, where libx265 ran at 0.267x (0.227), and OpenCL
  on the Mali-G610 (0); the same after a reboot from the API at 04:26:13 (0.121, 0.016, 0), then
  the uninstall and the folders `apps/jellyfin` and `media` removed (1.117).
  `/proc/mpp_service/load` showed no load: it wants `load_interval` set first, which the check did
  not write, so step (4)'s load on the decoder and encoder cores was not shown.
- 434d410 at 06:53: `scenario.sh smart` (12 steps, 0.492 MiB) and `smb` (37 steps, 5.336) OK; step
  18 recorded smbd's NT_STATUS_INVALID_DEVICE_REQUEST for a volume without `.snapshots`, which the
  user guide now names (cc8aba3). A reboot from the API at 07:17:10: `storage.pool-balance`'s step
  by hand (5) failed, the full balance idle after the boot (5 chunks, 2 balanced a minute before
  the reboot; 0.133), and `storage.disk-spindown`'s (3) and (4) failed: sda, with the rule's 20
  minutes, still active after 22 minutes (0.164; the step read no I/O counts, and from 07:44 to
  08:13 the kernel's counts since the boot stayed at 182 reads and 22 writes).
- The finding of that reboot: sda's own automatic offline data collection ("Auto Offline Data
  Collection: Enabled", suspended by each command of the host and resumed later) kept it out of
  standby for hours, with `-S 240`, `180` and `120` through 35, 27 and 18 minutes of watching; with
  `smartctl -o off` and `-S 120` it slept 661 s later (10:29 to 10:40), and it was then restored
  (ADR-0378). Fixed: a Save with a timeout turns the collection off and records that in the rule,
  and Off turns it on again for a recorded disk alone (7f33ede; its review: an unclear answer
  recorded, the old state put back when the rule cannot be written, 5d79302); the smart check reads
  it, proves it off and restores it.
- 5d79302 at 13:39: the Save of sda's 20 minutes turned its collection off and recorded it (0.398);
  `scenario.sh smart` (14 steps, 0.414) and `smb` (37, 5.371) OK. Then 20 GiB of zeros into the
  pool (0.070), sda's own timer off (`hdparm -S 0`, so that only the rule could set it), a full
  balance started, and a reboot from the API at 14:03:25: sda's collection still off and recorded
  (0); sda in standby 1201 s after its last read or write, with `hdparm -C` a minute apart, so udev
  applied the rule's 20 minutes at boot; GET /storage/disks standby, Disk Health STANDBY, and sda
  still asleep after that read (0.168). `storage.pool-balance`'s (5) failed again: idle after the
  boot (0.145); the kernel log says the balance ended at 14:04:32, about a minute after the boot
  (the cycle's log kept no earlier kernel line of that boot), as the kernel's resume with usage=90,
  which passes over full chunks, ends it (0ccb107's measurement; the reboot of 14:42 showed that
  resume line).
- The finding of that reboot: on every resume, the kernel's at a mount and Resume Balance's, Linux
  6.1's update_balance_args() (fs/btrfs/volumes.c) gives each chunk type without a usage filter
  usage=90, so a paused full balance goes on only through the chunks less than 90% full. No
  behaviour change: the card's note of a paused balance and the full balance's warning (en, ru),
  the user guide, ADR-0379 and the entry's notes say so, with the way to rewrite every chunk:
  cancel it and start a new full balance (0ccb107, 26921a6).
- With 256 MiB files in the pool, every second removed (chunks about half full; 0.078), a full
  balance and a reboot from the API at 14:42:26: the kernel log said `balance: paused` before it
  and `balance: resume -dusage=90 -musage=90 -susage=90` at the mount, no unit `unos-balance-data`
  was loaded, GET answered running at 6 of about 31 chunks, and Cancel ended it (0.160). Then
  `scenario.sh all` OK, 12 areas, 187 steps (21.832 MiB), no Traceback or Killed in the
  supervisor's log, check.sh OK (30.1 MiB since the boot).
- `storage.disk-spindown`'s step by hand (7): smartmontools inactive and disabled, as found (it was
  active and enabled at 01:04, and was turned off during that night's measurements of sda's timer);
  sda got 1 hour at 15:17:52 (0.145) and was in standby at the step's one read at 17:47:55
  (0.023): nothing restarted its timer in the hour before that read, so no hourly check of UNOS
  kept it awake, as the step asks (one read does not show that nothing woke it earlier in the 2.5
  hours); then its timeout went back to 0, as found at 01:04 (0.309): the rule removed and the
  collection on again.
- The uninstallation (c6b2fa5, then its reviews' fixes 93a4c81, 19593be and cc9482e; 1040834 a
  comment): `deploy/uninstall.sh`'s remove_disk_idle_timeouts runs `hdparm -S 0` on the disks of
  UNOS's rule and turns their recorded collection on again, then removes the rule, keeping the
  records of the disks it could not undo, each tool bounded and its output in files of its own, and
  a stop in the step leaves the rule whole. It has not run on a host: only the stub tests of
  TestUninstallScript in `test_deploy_scripts.py` show it.
- The final tree cc9482e at 19:19 to 19:21, with sda as found (timeout 0, the collection on, no
  rule; 0): `scenario.sh smart` (14 steps, 0.328 MiB) and `balance` (11 steps, 0.234) OK,
  `deploy.sh status` equal, no Traceback or Killed in the supervisor's log, check.sh OK (71.6 MiB
  since the boot of 14:42). The reboot checks ran on 5d79302; cc9482e differs from it at runtime in
  `read_rule`'s reading of lines that UNOS does not write, the balance card's texts and
  `deploy/uninstall.sh`. `read_rule` reads the lines that UNOS writes as before, udev applies the
  rule at boot, and the uninstallation is no step of `storage.disk-spindown`, so its pass rests on
  the reboot checks of 5d79302, without a reboot of cc9482e (Open owner actions, 10). 1040834 and
  this record change no runtime behaviour.
- Review findings left unfixed, with the reason: (a) the balance card's 90% sentence shows under
  every paused balance, also one with a usage limit, where it does not apply; it is worded as a
  condition ("When a paused full balance goes on"), so it is not false, and telling the kinds apart
  needs the supervisor to report the balance's filters (`btrfs balance status -v`), a behaviour
  change left for later. (b) The uninstaller: when Docker cannot be reached, its backup step says
  the data volume is gone (as after an earlier run) although the daemon is only down; before this
  change it printed a false "Backup created" in that case. (c) The uninstaller: a failed `mktemp
  -d` also skips `hdparm -S 0`, on purpose: the tools' output must go to files, so that a stuck
  tool cannot hold the script's output, and the end names the command. (d) The uninstaller: no test
  asserts that hdparm and smartctl read from /dev/null (the tests already run with standard input
  at /dev/null). (e) A byte-for-byte copy of the rule in the backup folder was rejected: nothing
  would read it, while the records-only rule is read by a new installation and by another run. From
  the branch's history: no test checks that the ADR an entry's notes open with is in its adr list
  (d70dd12: no rule of ADR-0203 asks it, and four older entries would fail it); and the pages that
  list the pools still scan every disk when an administrator opens them (ADR-0378, Consequences),
  which the browser look of `storage.disk-spindown`'s step by hand (6) would observe on the device.
- A review of this record in a fresh context corrected it: the reason for `test_storage.py` in
  `storage.disk-spindown`'s verified_by (the SMART monitor's read of a disk in standby leaves it
  asleep; GET /health/full without a scan of the disks is `test_health_monitor.py`'s), its notes'
  HUP sentence, which 1040834 made false, what step (7)'s one read shows, and the I/O counts of
  07:17 and the 14:03 balance's resume, which the cycle's logs do not show; and
  `apps.rk3588-hardware-acceleration`'s step 1, whose step by hand (4) did not show the load on the
  decoder and encoder cores either. Its doubt of `storage.disk-spindown`'s pass (step (6) not run,
  no reboot of cc9482e) is the owner's to settle (Open owner actions, 10).
- Passing now: `storage.disk-spindown`, with `test_storage.py` and `test_files_security.py` added
  to its verified_by. Open, waiting on the owner (Open owner actions, 8 to 11):
  `storage.pool-balance` (its step by hand (1), and the page's parts of (2) and (3), in a
  browser), `shares.per-share-protocol-options` (a Mac's Time Machine backup, Previous Versions on
  a Windows computer), and `apps.rk3588-hardware-acceleration` (step 1's load on the decoder and
  encoder cores and its look at the App Store and Jellyfin's dashboard, with Jellyfin installed
  again at the owner's word; step 2, Immich's NPU, once `apps.compose-stacks` is merged, and the
  Immich administrator account). `GLOSSARY.md` gained Protocol options. `make check` passed on
  this record.

### 2026-11-15: Armbian on the dev device upgraded to 26.11.0-trunk (kernel 6.1.172)

- The owner asked to "update Armbian on the W3 to the current one". The board tracks Armbian's
  nightly repository for `armsom-w3` (beta.armbian.com); its package lists dated from January.
  The upgrade stayed on that repository and on the vendor kernel branch (the kernel choice is the
  owner's, ADR-0376): 26.2.0-trunk.151 to 26.11.0-trunk.133, kernel 6.1.115 to 6.1.172 (Dev
  device has the steps, versions and SD writes). The owner's overlay lived in the old kernel's dtb
  directory, which `/boot/dtb` no longer points to after the upgrade: it was copied over, and
  `docs/INSTALL.md` now says so for any overlay of one's own.
- After the reboot: the RK3588's video, GPU and NPU nodes, the JMB58x and the RTL8125 are there,
  the pool mounted with UNOS's options, check.sh OK, and `scenario.sh all` passed with its ACL
  step, which had been skipped for want of setfacl.

### 2026-11-15: Notes, a ledger entry and guard tests for the BPI-W3 (ADR-0376)

- The owner asked to describe support for the BPI-W3, the dev device, by analogy with the BPI-F3's
  #81, then to do it, after merging #81 and asking for the dev device to be brought up to `main`.
  `main` e80e2d5 was deployed to the device first (Dev device): its supervisor's pip layer built
  again (440.9 MiB on the SD card), and check.sh passed. Nothing went to the BPI-F3, which is
  switched off.
- Read on the board, without changing it: the device tree of Armbian's `armsom-w3` image; the
  eMMC with nine partitions beside two boot partitions; the pool's hard disk behind a JMicron
  JMB58x AHCI controller on a PCIe 2.0 x1 link, the M.2 Key M slot empty; only soc-thermal with
  passive trips (75 and 85 C), the six other zones with a critical trip alone; no fan among the
  cooling devices; an extra device tree overlay that turns on two PCIe controllers with nothing
  on them. The review in a fresh context took the UGREEN firmware on the eMMC (ADR-0225) for the
  board's own; the owner set that right: the device is a BPI-W3, and the firmware is the owner's.
- ADR-0376 (proposed): no code of its own for the RK3588; a section of `docs/INSTALL.md`, a line
  of `README.md`, the ledger entry `deploy.arm64-bpi-w3` and guard tests on the board's facts:
  the flash never listed or formatted, SATA and NVMe pools made without the K1's workarounds, the
  board's compatible among the RK3588 cases of the K1's NVMe rules, and check.sh against
  soc-thermal's 75 C. `GLOSSARY.md` named the dev device an ArmSoM Sige7; it names the BPI-W3 now.
- The review's other findings, fixed: btrfs turns on discard=async by default only from kernel
  6.2 and UNOS mounts pools with noatime alone, so the texts say what UNOS does under the vendor
  kernel 6.1 (no K1 workaround, fstrim.timer) instead of "with TRIM"; `install.sh`
  and the web UI's builder stage have not run on the board; `update.sh` builds both images
  without a cache; the entry's notes say what `scenario.sh all` covers and give the checks by hand.
- The device check: `scenario.sh all` before and after a reboot from the API, then the checks by
  hand of the entry's notes before and after a second one (Dev device; the results in the
  entry's notes); all passed. The entry stays open: step 1 needs
  a second microSD card, and a pool made on the board a spare disk.
- Three reviews in a fresh context; the last found only wording, fixed. `make check` passed on the
  final commit (7416 tests), which was merged as main dfe81f5.

### 2026-11-13: The idle load of UNOS within a budget on the BPI-F3 (ADR-0374, ADR-0375)

- The owner found the board at about half its CPU and memory and 71 C without a fan, and asked
  that such load never happen again. Most of it was a test video that Frigate decoded and searched
  in a loop (its camera `test` is now off in the stack's config, on the pool), the rest UNOS's:
  each open dashboard tab took a stats sample every two seconds in the event loop, 0.4 s of the
  K1's CPU each (the list of 311 host processes), the walk for upload leftovers went through
  Docker's data root on the pool, and the healthcheck started a Python every 10 seconds, 0.5 s of
  CPU each. ADR-0374 (d76096a) shares one sample between the tabs, in a worker thread, with the
  process list at most every 10 s, leaves Docker's data root out of the walk, and gives
  `scripts/dev/check.sh` a section that fails for a container of UNOS above 15% of a core or a
  thermal zone within 5 C of its first passive trip. ADR-0375 (07f9c47): the healthcheck asks
  with bash over `/dev/tcp` (0.01 s) and the process list reads the ten it shows in full only
  (0.41 s to 0.13 s).
- Measured on the board with the cgroup's CPU counter, `install.sh` of 07f9c47: unos-supervisor
  3.5% of a core with no dashboard open (its process 2.9%, spread over the event loop and four
  worker threads; before, peaks of 40 to 46% every ten seconds), 5.6% with two simulated tabs
  (two threads that call `shared_system_stats` every two seconds in the container; before, with
  one real tab, 15.5% on average and peaks of 58.8%); unos-webui 0.3%; load average 0.2; the
  thermal zones 45 to 47 C.
- Review in a fresh context: no finding. Not done: the same measurement with two real tabs,
  which needs the owner's sign-in, so `system.idle-load-budget` does not pass yet.

### 2026-11-13: UNOS deployed on the BPI-F3, with Home Assistant and Frigate on its pool

- At the owner's request ("deploy UNOS on the board, then the custom Home Assistant and Frigate
  containers by the rules of UNOS; do everything yourself"), on the board that ran the stack of
  github.com/und3f1ned/bpi-f3-frigate-ha. `install.sh` from a bundle of this branch set up the
  host and built the supervisor on the board (about 35 minutes); the web UI's build crashed in
  Node.js 22 twice, also with `--no-maglev` (nodejs/node#66845), so ADR-0373 superseded ADR-0371:
  on a K1 the web UI's image comes from another machine, and `install.sh` and `update.sh` build
  only the supervisor there. Its image was made from static files built on the workstation, and
  `install.sh`'s own functions started UNOS and its unit.
- Storage (ADR-0372, the owner's decisions): the stack's data (15 GB with Docker's) was copied to
  a USB disk and checked with checksums, the md RAID1 of the two SATA SSDs was taken apart (fstab,
  mdadm.conf and the initramfs follow; the old files are kept as `*.k1-before-unos`), UNOS's
  `create_pool` made the pool `data` (btrfs RAID1, with TRIM), and Docker's data root (the
  subvolume `.docker`) and the apps' data moved to it. The stack runs with a `compose.override.yaml` (data in `apps/<app>`, log
  rotation). Downtime of the cameras: 15:44 to 16:04 and 16:26 to 16:40 MSK.
- Checked on the board: Frigate's detector on the NPU about 24 ms, its camera at 5 fps; Home
  Assistant answers; UNOS's health 200; the pool healthy; `fstrim` trimmed the pool;
  `cpu_temperature` is cluster0's. After the reboot of 16:43 MSK everything came back by itself
  (the pool 203 s into the boot, Docker at 220 s) with 3.2 MiB written to the microSD card.
- Review in a fresh context: no blocker. Fixed: a rollback of `update.sh` on a K1 gives
  `deploy-webui:latest` back to the previous image, so it now says to load the new one again; the
  subvolume of Docker's data root is `.docker`, which the Storage page neither lists nor deletes
  as a volume (it was `docker`); the steps of `docs/INSTALL.md` for a fresh K1 (a first run of
  `install.sh` before the image, `scp` and `docker load -i` instead of a pipe into sudo, QEMU's
  binfmt, the manual build and upgrade).
- `install.sh` of 6007166, run again on the board at 17:37 MSK, went to its end on the K1 path: the
  supervisor from the build cache, no web UI build beside the loaded image, UNOS and its unit up.
- Not done: the administrator account of UNOS and the board's dev.env (passwords, the owner's),
  so `scripts/dev/scenario.sh` did not run and `deploy.riscv64-bpi-f3` does not pass yet. The USB
  disk keeps the checked copy of the old data. The board's sshd allows root to sign in with a
  password (Armbian's setting); root's key login of `scripts/dev/` was added.

### 2026-11-13: On the K1 only NVMe drives go without power saving and TRIM (ADR-0370)

- The owner's BPI-F3 showed that NVMe, not TRIM, is the K1's problem: its NVMe drive hung under
  load and, after a warm reboot, never became ready again, and the slot's power is not switched by
  the K1's PCIe driver. The board now runs two SATA SSDs in an md RAID1 behind a JMB582
  M.2-to-SATA adapter, with TRIM and `fstrim.timer`. At the owner's request ADR-0370 supersedes
  ADR-0369: `install.sh` no longer turns `fstrim.timer` off on a K1 (its udev rule touches NVMe
  drives only), a pool is made with `--nodiscard` and mounted with `nodiscard` only when an NVMe
  drive is among its devices, and its fstab entry then keeps fstrim off it with
  `X-fstrim.notrim` (util-linux 2.41 on the board knows it); `docs/INSTALL.md` advises SATA
  behind an M.2-to-SATA adapter or USB SSDs and advises against NVMe.
- Checked as well: a pool's disk missing at boot leaves nothing on the system disk, since the
  mountpoint of a pool that is not mounted is immutable and the supervisor mounts the pool at its
  next start (ADR-0034); UNOS does not move Docker's data root, and `docs/INSTALL.md` now tells a
  K1 host that moves it to make Docker require that mount, as the stack does with
  `RequiresMountsFor=`. `install.sh` installs mdadm, which UNOS does not run, and Debian's mdadm
  writes the arrays of old NAS disks into mdadm.conf: the new entry `deploy.no-unused-mdadm`.
- Review in a fresh context: no blocker. Fixed: the ledger's steps, which wanted an NVMe pool to
  survive a reboot on a board without one (the NVMe behaviour stays with the backend tests), the
  mount again after a busy unmount, which now cannot fail on reading the pool's devices and keeps
  TRIM off on a K1 whose devices it cannot read, the claim that the supervisor mounts a late disk
  by itself, the advice to move Docker's data off the card without a guard, NVMe filesystems that
  UNOS does not manage, and the tests of the import and of that mount again on a K1. Recorded in
  ADR-0370: an NVMe drive below device mapper, or missing from a degraded pool, is not seen.
- The board was only read over ssh. Open as before: every step on the board (ADR-0018).

### 2026-11-13: UNOS for the Banana Pi BPI-F3 (SpacemiT K1, riscv64), built but not yet on the board

- At the owner's request, `deploy.riscv64-bpi-f3` (ADR-0369, proposed): `install.sh` accepts
  riscv64 and takes Docker from the distribution there; the supervisor's pip layer installs pip
  26.3 and on riscv64 compiles five packages with a Rust toolchain it removes again; the web UI's
  builder runs Alpine 3.22's Node.js 22 everywhere, since the `node` image has no riscv64 build;
  on a SpacemiT K1 a udev rule turns off the NVMe drives' APST and ASPM L1, `fstrim.timer` goes
  off, and pools are made and mounted without TRIM; the dashboard shows the K1's
  `cluster0_thermal`. The K1's VPU and NPU serve apps, not UNOS: they went into the new entry
  `apps.spacemit-k1-hardware-acceleration`, and the App Store's platforms into
  `apps.catalog-platforms`.
- Checked on the workstation: both images built for linux/riscv64 under QEMU from the pinned
  bases (whose indexes list riscv64), the supervisor's pip layer in about 25 minutes; the riscv64
  supervisor answered `/api/v1/health` with 200, and the arm64 images built as well. The udev rule
  passed `udevadm verify` (systemd 257). The board was only read over ssh. `make check` passed.
- Review in a fresh context: the first version turned NVMe power saving off in a unit before
  `local-fs-pre.target`, as the stack on the board does; the board's journal showed the drive
  appearing 63 ms before that target, so it became a udev rule, and the reviewer then found that
  the controller's APST setting appears only after identify, so the rule runs the script when the
  drive's disk appears. Fixed as well: the docs, the ADR and the changelog that still named the
  unit, a `udevadm settle` that could stop the installation, the trim decision read twice in one
  Create, the ledger's notes, "tested" where nothing ran on the board, a wrong Dockerfile comment
  and the Docker packages `uninstall.sh` names. Recorded in ADR-0369: a pool mounted before UNOS
  took it over keeps its options until it is mounted again, and `nodejs~22` and `pip==26.3` are
  not fixed by a digest.
- Open: every step on the board (it runs another stack; making it a device for UNOS is the
  owner's call), then the device and reboot checks (ADR-0018).

### 2026-11-12: A fifth batch: a repair job, a streaming restore, no polling, renamed users

- Four entries, each test-first by an agent of its own: Repair permissions as a background job
  with progress over the events socket, one at a time and a stop (`shares.repair-in-background`,
  ADR-0363, superseding ADR-0155 and ADR-0006); a restore that checks and extracts the archive in
  one streaming pass, half the room in /tmp (`backup.streaming-restore`, ADR-0364); no page polls
  the API, with a check that fails for one (`webui.no-http-polling`, ADR-0365 to ADR-0367); and
  renaming a user with its SMB account and uid (`auth.user-rename`, ADR-0368). The repair and
  polling branches both added messages to the events socket; the merge keeps both. The ADRs were
  renumbered in one pass; the repair entry's adr list line, the same in two branches, was then
  corrected by hand.
- Review, a reviewer per branch and a skeptic per pair: one major finding, fixed: after a restart
  of the supervisor the Shares page kept a dead repair showing with only Stop. Fixed too: a share
  deleted or renamed during its repair, an audit entry for a repair a stop cut short, the
  scrubs after a reconnect, a stronger polling check, a file named unos.db/. in an archive, and
  for renames each name of a uid recorded for restores, a stale row of the new name, and the
  login history of a renamed user. `make check` passed on the fixes, and 298 E2E tests of the
  pages touched. Merged as main 7827340.
- Device check of the integration tree b9aaea5 around the reboot from the API at 19:37:03 UTC:
  `scenario.sh shares` (3.945 and 3.227 MiB), `restore` (7.648 and 7.367), `smb` (32 steps
  now, with the rename: 3.988 and 3.812) and `backups` (0.406 and 0.383) OK before and after.
  After the deploy of the fixes (5deee90, 20:14 UTC): `shares`, `restore` and `smb` OK; that
  restore measured 48.664 MiB, 35.5 of them in its read-only step 2 while the deploy's writes
  still went on, and a run on the idle device right after it 7.262 MiB, as before.
- Passing now: `shares.repair-in-background`, `backup.streaming-restore`,
  `webui.no-http-polling`, `auth.user-rename`. The Linux-only tests of the repair walk wait for
  the rootfs of `scripts/dev/linux-tests.sh` in the device's RAM (the owner's).

### 2026-11-12: A fourth batch: hot-plugged disks on the Storage page, a dialog stack, every smb.conf copy kept

- Five entries were taken; two were built already: `auth.api-token-last-used-throttle` waits only
  for its device measurement, which needs an API token on the device (a credential, the owner's,
  ADR-0024), and `apps.no-legacy-default-pool` only had one stale test comment, reworded. New:
  the Storage page reloads its lists when the stats stream's block devices change
  (`storage.hot-plugged-disks`, ADR-0360), open dialogs form a stack whose top one alone answers
  Escape and under which the others are inert (`webui.dialog-stack`, ADR-0361), and every replaced
  smb.conf and import backup is kept in a dated copy, the newest 10 of each kind
  (`shares.keep-every-replaced-copy`, ADR-0362, superseding ADR-0085, ADR-0092 and ADR-0309).
  SMART of a hot-plugged disk got an entry of its own, `storage.hot-plugged-disks-smart`.
- The merges were plain and renumbered in one commit; the device cycle ran on the integration
  tree while the review ran. Review, one reviewer per branch and a skeptic: no blocker; fixed:
  the Storage page's loads numbered so a late answer cannot overwrite a newer one, a device
  reload leaving the error alert, the dialogs under the top one made inert, a depth per dialog,
  only regular files counted as copies, no copy written when any kept one has the same bytes,
  and the smbconf check making two copies to show that the first stays. `make check` passed on
  the fixes, and 272 E2E tests of the pages touched. Merged as main a9adcb1.
- Device check of the integration tree d149c3a around the reboot from the API at 14:45:53 UTC:
  `scenario.sh smbconf` (2.988 and 2.477 MiB), `smb` (3.617 and 3.148) and `files` (0.148 and
  0.125) OK before and after. After the deploy of the fixes (2bb76a8, 15:23 UTC): `smbconf` OK
  with its 17 steps (3.469 MiB) and `smb` OK (3.070 MiB). No second reboot for the fixes.
- Passing now: `shares.keep-every-replaced-copy`. Open: `storage.hot-plugged-disks` and
  `storage.hot-plugged-disks-smart` (a spare disk, the owner's), `webui.dialog-stack` (the Users
  page in a browser on the device), `auth.api-token-last-used-throttle` (its measurement with an
  API token, the owner's).

### 2026-11-12: A third batch of four, checked on the device across a reboot, faster

- Four branches, each test-first by an agent of its own: the NUT password write-only and upsc
  on the host (`system.ups-monitoring`, a safety fix, ADR-0356), the certificates' honest status
  (`security.custom-certificates`, ADR-0357), restored copies listed and deleted
  (`storage.restore-copies-deletable`, ADR-0358) and step 5 of `security.audit-admin-actions`,
  the action filter (ADR-0359). The certificates agent stopped before its commit; its staged
  work was checked (backend tests, 161 E2E tests, svelte-check, the texts) and committed through
  the hooks. To go faster, as the owner asked, the last merges were plain and renumbered in one
  commit, and the device cycle ran on the integration tree while the review's fixes were made.
- Fresh-context review, one reviewer per branch and a skeptic per pair: no blocker; fixed: an old
  0644 UPS settings file, UPS values with decimals, the certificate store made atomic under a
  lock, the country field and the dialog's errors, a stale certificate list, printable
  organizations, a copy's btrfs description forged through its name, and log_action taking only
  known actions. The device check showed one more, fixed: a self-signed certificate made with an
  organization was listed as Custom. `make check` passed on the fixes, and 202 E2E tests of the
  pages touched. Merged as main b01cf92.
- Device check of the integration tree 8150936 around the reboot from the API at 13:11:00 UTC:
  `scenario.sh volumes` (11 steps, now with a restored copy) and `files` OK before and after (SD
  MiB 1.516 and 0.910, 0.121 and 0.121); the checks by hand (0.421 and 0.644 MiB) OK but for the
  self-signed type above. After the deploy of the fixes (0a5294c, 13:29 UTC): `scenario.sh
  volumes` OK (1.559 MiB) and the checks by hand (0.969 MiB) OK; their one FAIL line read the
  key's and the certificate's stat in each other's place, a fault of the check script (the key
  is 600, the certificate 644), corrected since. No second reboot for the fixes.
- Passing now: `storage.restore-copies-deletable`. Open: `system.ups-monitoring` (no UPS, NUT an
  owner install), `security.custom-certificates` (serving stored certificates),
  `security.audit-admin-actions` (pools, containers, apps and network interfaces on the device;
  auditing the firewall, the certificates and the login history's clearing).

### 2026-11-12: A second batch of six, reviewed, merged and checked on the device across a reboot

- Six entries' steps, each test-first by an agent of its own, merged into one integration branch
  with their ADRs renumbered in merge order: the audit of pools, volumes, snapshots, containers
  and apps (`security.audit-admin-actions` steps 1 and 2, ADR-0350) and of backups, power,
  scheduled tasks and the host's settings (step 3, ADR-0351, with the new action task_run);
  steps 1, 2, 4, 5, 6 and 8 of `storage.edge-cases` (ADR-0352); step 2 of
  `shares.restore-uid-guards`, a record of every SMB uid handed out (ADR-0353); step 2 of
  `files.upload-edge-cases`, leftovers of interrupted uploads found 10 minutes after each start
  and removed on request (ADR-0354, which supersedes ADR-0167); and
  `backup.drop-unused-include-options` (ADR-0355). The two audit merges conflicted in their
  docs, the audit page's names and the entry's notes; both sides were kept.
- Fresh-context review, one reviewer per branch and a skeptic per pair: no blocker; the
  confirmed findings were fixed, among them a failed part-way change that wrote no entry (a pool
  create after mkfs, a removal after the unmount, an uninstall that stopped the app), a restart
  of UNOS's own containers, a hung realpath on a network mount, the rows from before the uid
  record, the leftovers walk's threads, memory and depth, and a stale list after a refusal. A
  second reviewer on the fixes found three more, fixed too: a leftover whose folder was replaced
  blocked every later removal, realpath killed by timeout reaches the supervisor as -9, and a
  quota limit that does not answer. Three paths that keep a new volume after a 409 or a failed
  removal still write no audit entry (ADR-0350, Consequences). `make check` passed (7049 tests
  on the last fixes) and 367 E2E tests of the pages touched on the stand-in. Merged as
  main 641f2a5.
- Device check of main 641f2a5 around the reboot from the API at 04:35:30 UTC: `scenario.sh all`
  OK before and after, 147 steps passed and 1 skipped (SD MiB files 0.715 and 0.207, volumes
  0.449 and 0.484, smart 0.035 and 0.020, backups 0.441 and 0.391, smb 2.969 and 3.031, smbconf
  2.277 and 2.371, shares 3.027 and 3.203, scrub 0.516 and 0.449, offsite 0.480 and 0.555,
  restore 7.645 and 7.652). The checks by hand (1.138 and 1.222 MiB): the audit entries of a
  volume, its snapshot, a scrub task (created, changed, run, deleted), the time zone there and
  back and a backup, and nothing for refused or unchanged requests; the reboot's system_reboot
  entry in the log after the boot, the chain intact; a subvolume whose name forges list lines
  shows no phantom volume; the smb_uids table; a leftover found, listed with its size, the
  refusals of a plain and a fresh file, and its removal; a backup's manifest without options.
- Passing now: `files.upload-edge-cases` and `backup.drop-unused-include-options`. Open:
  `security.audit-admin-actions` (step 5, and pools, containers, apps and network interfaces
  on the device), `storage.edge-cases` (step 7 with a real bind mount on the device),
  `shares.restore-uid-guards` (its crafted cases rest on the tests; no marked SMB account on the
  device now).

### 2026-11-12: Six MVP fixes, reviewed, merged and checked on the device across a reboot

- Six branches, each test-first by an agent of its own, merged into one integration branch with
  their ADRs renumbered in merge order: Samba's refusal of SAMR password changes
  (`shares.password-changes-only-through-unos`, ADR-0342), the own-account audit entries
  (`security.audit-admin-actions` step 4, ADR-0343), an unmount without Docker refused
  (`storage.edge-cases` step 3, ADR-0344, superseding ADR-0070), the backup upload and restore
  guards (`backup.edge-cases` and `shares.restore-uid-guards` step 1, ADR-0345, superseding
  ADR-0166), the CPU's temperature (`system.temperatures`, ADR-0346) and the upload idle
  timeout (`files.upload-edge-cases` step 1, ADR-0347). The agent of the last one stopped
  before its commit; its staged change was committed through the hooks by the integrator.
- Fresh-context review, one reviewer per branch and a skeptic per pair of branches, then one more
  reviewer on the fix commits. Fixed: the smb check's SAMR step would have misread Samba 4.22's
  answer (its smbpasswd prints MS-ERREF's description, not "Password restriction"); turning 2FA on
  or off is conditioned on the current state and on the secret its code was checked against, and
  a setup never replaces the secret of 2FA that is on; a move checks every item, a bind-mounted
  file too, against the mount table before any moves (ADR-0348, superseding ADR-0170); AMD's Tdie
  before Tctl; a restore refuses a database that gives one SMB uid to two accounts (ADR-0349); the
  E2E stats fakes, stale ledger notes and ADR texts. Refuted: a second gzip pass of an upload
  check, and a dated research note's sentence (given a dated remark instead). New ledger entry
  `backup.drop-unused-include-options`. `make check` passed (6852 tests, then again on the last
  fixes), the E2E specs of the pages touched passed on the stand-in (270). Merged as main 405d318.
- Device check of tree 8ea5a08 (23:06 UTC deploy) around the reboot from the API at 23:22:13 UTC:
  `scenario.sh all` OK before and after, 147 steps passed and 1 skipped (SD MiB files 1.242 and
  0.094, volumes 0.488 and 0.488, smart 0.020 and 0.020, backups 0.355 and 0.363, smb 3.000 and
  3.020, smbconf 2.219 and 2.359, shares 3.004 and 3.184, scrub 0.406 and 0.438, offsite 0.516
  and 0.512, restore 7.332 and 7.574). The smb check's new step 21 got Samba's refusal of a SAMR
  change (`smbpasswd -r`) with passdb.tdb unchanged; the restore check's backups hold no fstab.
- Checks by hand, before and after the reboot (scripts of this session, not in the repository): the command
  timeouts' scrub in a session of its own, its cancel, reset-stats, a SMART short test, Run now
  of Scrub, Snapshot and SMART Test tasks, no Killed or Not started in the log (0.480 and 0.591
  MiB); the dashboard's cpu_temperature equal to soc_thermal in sysfs; a volume's own trash, the
  409 of a snapshot, the 0700 of a trash another account made, 0 MiB for a delete, a restore and
  an expiry pass, an item kept across the reboot and an entry 31 days old gone after the pass at
  the start (0.079 and 0.161 MiB); a gzip `.enc` upload refused; a user's own password change,
  2FA on and off and sign-out in the audit log, kept across the reboot (0.713 and 0.706 MiB).
- Two FAIL lines that are no defect of UNOS: the 408 of a stalled upload reached the client only
  when it sent again, since Caddy holds an early answer while the client is silent; the
  supervisor freed the upload's file at 120 seconds, and ADR-0347 now says so. The trash check's
  comparison of the boot with the supervisor's start used the API's boot_time, which moved with
  the clock's correction after the boot; `uptime -s` (23:23:10) and the container's start
  (23:23:23) are in order.
- Main 405d318 deployed at 23:44 UTC (its runtime adds to 8ea5a08 the move check of bind-mounted
  files and the 2FA secret conditions): `scenario.sh files` OK and the upload and audit checks by
  hand OK, but for the same 408 line; `check.sh` OK. No second reboot for that difference.
- Passing now: `storage.commands-time-out-cleanly`, `system.temperatures`,
  `files.delete-to-trash`, `backup.edge-cases`. Open: `shares.password-changes-only-through-unos`
  (a Windows client, an owner action), `security.audit-admin-actions` (steps 1-3 and 5),
  `storage.edge-cases` (steps 1, 2, 4 to 6 and 8, and step 7 with a real bind mount),
  `shares.restore-uid-guards` (step 2), `files.upload-edge-cases` (step 2).

### 2026-11-11: The access log keeps refusals and failures, and the shares and restore checks pass across a reboot

- `deploy.container-logs-off-the-system-disk` (ADR-0338, superseding ADR-0176): uvicorn's access log
  keeps only requests answered 400 or above (test_authz.py's real server, which failed before).
  Before it (main b290ada, 14:42 UTC): 60 idle seconds 0.000 MiB, 100 calls of GET /auth/me with
  the cached token 0.105 MiB, one access line per call in the supervisor's json-file log. Deployed
  as main 96db310 at 15:16 UTC (SD MiB sync 1.129, build-supervisor 12.336, up 3.250, check.sh
  0.016): idle 0.016 MiB a minute, the 100 calls 0.016 MiB, docker logs with the start lines and
  no access line. After the reboot below: idle 0.016, the 100 calls 0.000 MiB; docker logs of
  unos-supervisor and unos-webui (Caddy's TLS lines) both showed their recent lines.
- The scenario check `shares` (ADR-0340) checks a sticky folder of another account (step 3 of
  `shares.acl-aware-parent-check`), Repair permissions, a user's own SMB activation with a token of
  its own, and a guest share. Its first device run (15:24 UTC, 2.859 MiB) failed at the anonymous
  share list, which names no share under restrict anonymous = 1: ADR-0339 supersedes ADR-0098 and
  says so; the check now reports that list and asserts the session to the share by its name. Then
  OK at 15:27 UTC, 25 steps and the ACL step skipped (no setfacl on the device), 2.941 MiB.
- The scenario check `restore` (ADR-0341) restores a backup it made moments before, twice, with two
  SMB users and a share: first run at 15:13 UTC (19 steps, 7.504 MiB; its two copies of the
  database removed by hand afterwards, 0.070 MiB), then OK at 15:38 UTC, 20 steps, 7.207 MiB.
- Fresh-context reviews, three lenses and two refuters per finding: `shares` 14 findings, 13 of
  them fixed in 702ef6f (among them the sticky helper's chmod order, which could have followed a
  symbolic link swapped in by the other account, and a tabbed password printed after tail()) and
  the missing Log entry by this one; `restore` 6, then 8 more on the fixes and the merge, all fixed in fc06047 (the clean-up waits
  for a restore whose answer was lost and keeps the copies of the database when what is not the
  check's changed; the API tokens compared are the relay administrator's, the only ones its API
  lists; with a guest share the refused sign-ins are left out, ADR-0339). Merged as main 1555716
  and d6e98c0 after `make check`; the E2E specs shares.spec.ts and account-smb.spec.ts passed on
  the stand-in (53).
- The reboot from the API at 19:25:58 UTC (the boot of 19:26:54, check.sh OK at 19:28): before it
  `scenario.sh all` OK, 146 steps passed and 1 skipped (SD MiB files 0.145, volumes 0.727,
  smart 0.035, backups 0.379, smb 3.250, smbconf 2.586, shares 3.078, scrub 0.410, offsite 0.520, restore
  7.215); after it the same (0.199, 0.426, 0.062, 0.395, 2.930, 2.145, 3.020, 0.406, 0.469,
  7.117). 10.5 MiB written to the SD card from the boot to check.sh.
- Passing now: `deploy.container-logs-off-the-system-disk`, `shares.read-only-guest-share`,
  `shares.self-activation`, `shares.restore-keeps-smb-with-proof`, `backup.no-old-backup-import`.
  Open: `shares.repair-permissions` (its ACL step needs setfacl, the package acl, on the device:
  an owner action), `shares.acl-aware-parent-check` (ADR-0193 waits for the owner), and copies to
  an S3 store or a second disk, which the owner left for later.
- An intermediate amend of the restore branch ran with the git hooks off by mistake; that commit
  was replaced, and the commits merged ran the hooks.

### 2026-11-11: Telegram alerts, restores and off-device copies on the device

- `system.alert-events`, device check of 2026-11-10 22:13 to 22:33 UTC (main a7aa7c9): a webhook
  channel to a listener on the device's unos-net bridge got its test at 22:13:14; a custom task
  `false` failed at 22:13:24, and the task check of 22:17 sent "Task 'unoscheck-fail' failed" once:
  the checks of 22:22, 22:27 and 22:32 sent nothing more while it lasted. The same alert went to
  the owner's enabled Telegram channel, which failed then (ConnectTimeout). The healthy pool and
  disk raised no problem. SD MiB of the setup 0.141 and 0.094, the cleanup 0.168.
- The wait of that check never ended (a bash-only test under zsh, with no deadline) and the session
  stalled for nine hours: ADR-0335 since then.
- `system.telegram-from-the-device`: Telegram answers the device's network over IPv6 only (`curl -6`
  302 in 0.2 s, `curl -4` fails), and the containers have no IPv6. After the deploy of main 53396c0
  (09:12 UTC: SD MiB sync 1.867, build-supervisor 12.512, up supervisor 3.383, check.sh 0.016) a
  test notification to the owner's Telegram channel answered "sent" (09:12:52; the history's entry
  at 09:12:58, after the container's 5 s connect timeout and the host's curl; SD 0.137 MiB), and
  the owner confirmed it arrived. After the reboot below, the first alert of the start reached
  Telegram the same way.
- `backup.restore` on the device for the first time, on the branch backup.off-device-copies
  (e663f14) at 09:36 UTC: the configuration snapshot of an off-device copy put into the backup list
  as offsite_20261111_093643.tar.gz, and the regular restore of that backup: "success", database and
  smb_config restored, and the users, SMB shares, API tokens, smb.conf (the same sha256) and Samba's
  passdb users the same before and after (SD 0.906 MiB, the copy included); at 09:37 UTC an
  encrypted backup made as the Backup & Restore tab makes it restored with its passphrase, with the
  same result (SD 0.617 MiB). Its refusal without the passphrase or with a wrong one is in
  `scenario.sh backups`. The restores ended the other sessions (ADR-0329).
- `backup.off-device-copies`, the first runs of the real restic (0.18.0-1+b4 of Debian 13, its own
  image layer: the deploy of e663f14 at 09:34 UTC wrote SD MiB sync 2.504, build-supervisor 81.227,
  build-webui 19.074, up 5.648): `scenario.sh offsite` OK, 12 steps (1.160 MiB). The fresh review's
  13 findings were fixed in 8fef386 (deployed at 11:21 UTC: sync 2.230, build-supervisor 12.949,
  build-webui 19.266, up 5.812; `scenario.sh offsite` OK, 12 steps, 1.449 MiB). Save and test
  refused a folder on the system disk and a wrong password at once, with their reasons, saving
  nothing (0.371 and 0.238 MiB); it held an S3 store out of reach for its whole 10-minute timeout,
  fixed in 38a6da5 (a connection test of 10 s, and 120 s for Save's restic): since then it answers 400 within a second.
- The reboot from the API at 10:18:07 UTC (at 10:19:08; the kernel's start 10:19:25, ssh at
  10:19:44): before it, `scenario.sh all` OK, 101 steps (SD MiB files 0.426, volumes 0.578, smart
  0.098, backups 0.457, smb 3.137, smbconf 2.457, scrub 0.629, offsite 0.801); a failing task's
  alert of 10:10:52 went to Telegram (delivered through the host's network) and to a webhook with
  no listener (failed, both attempts named). After it the first task check of the start sent the
  alert again at 10:19:51 (the problems are kept in RAM, ADR-0332), delivered to Telegram;
  `check.sh` OK; `scenario.sh all` OK, 101 steps (0.469, 0.570, 0.141, 0.461, 3.258, 2.531, 0.660,
  0.816). The supervisor was back before 10:20, so a task due then ran at its time, and the
  missed-run alert needs a reboot asked at second 54.
- The second reboot, on f4ecd22: before it a disk target, one copy (check and restore test
  passed) and a custom task due at 12:53 UTC; the reboot asked at 12:51:45 (at 12:52:45; the
  kernel's start 12:53:02, the API at 12:53:29), so the task's time fell while the supervisor was
  stopped. After it the settings and the password were kept, a second copy succeeded and both
  copies' snapshots were listed (SD 0.227 and 0.113 MiB), and the check of 13:03:29 UTC sent "Task
  'unoscheck-miss' missed its run", delivered to Telegram; `check.sh` OK; `scenario.sh all` OK, 101
  steps (0.441, 0.547, 0.125, 0.461, 3.191, 2.445, 0.641, 0.805).
- A last review of 8fef386..f4ecd22 with three lenses and two refuters per finding: eight
  findings, fixed in c926016 (restic's lock removed on a stop: SIGTERM, then SIGKILL after 5 s;
  python -B for the host-name step; the disk rule when the disks of /mnt or /data cannot be found;
  Delete Volume atomic with a copy; tests and docs). Deployed at 13:08 UTC: SD MiB sync 1.012,
  build-supervisor 12.898, build-webui 18.852, up 5.742, check.sh 0.016; `scenario.sh offsite` OK,
  12 steps (1.852 MiB). Merged as main 27b840b after `make check` passed on c926016 and the E2E specs
  of the area 181 passed.
- Passing now: `system.telegram-from-the-device`, `backup.restore`, `backup.off-device-copies`.
  `system.alert-events` stays open for the kinds that need a fault or spare disks.

### 2026-11-11: Off-device copies with restic

- `backup.off-device-copies` is built to the design of ADR-0337, which supersedes the proposal
  ADR-0041; `passes` stays false. restic, in a layer of its own of `supervisor/Dockerfile` after
  pip's, copies the configuration archive of Create Backup and the chosen pool folders to one copy
  repository: a folder on a disk mounted below /mnt that is not the system disk, or an
  S3-compatible store. Saving the settings tests the target and makes the repository there only; a
  task of the new type `offsite_copy` or Run now runs unlock, the two backups, forget with
  `--prune`, check and the restore test, one copy or restore at a time; the snapshots restore the
  configuration into the backup list (`offsite_<snapshot time>.tar.gz`) and the files into a new
  folder `restored-<UTC time>` of a pool. The secrets live in `/data/offsite_secrets.json` (0600)
  and reach restic only in its own environment. The web UI's section Off-device copy is on System
  > Backup & Restore, and the Scheduler offers the task type.
- Tests first: `test_offsite.py` (89 tests against the fake restic of
  `supervisor/tests/fake_restic.py`, which restic's absence on the workstation calls for; the
  stand-in runs it too), the state files, the routes' roles, the stand-in, the Dockerfile's layer,
  and `webui/tests/offsite.spec.ts`, which failed without the section. The scenario check gets the
  area `offsite` (`scripts/dev/scenario.py`), with its fake in `test_dev_scenarios.py`.
- `backup.off-device-copies-over-sftp` records the SFTP and REST targets that ADR-0337 leaves out.
- On the dev device, e663f14 of the branch with the real restic 0.18: `scenario.sh offsite` OK,
  12 steps, and a copy, Restore configuration and the regular restore of that backup left the
  users, shares, tokens, smb.conf and passdb the same. Its copies name the container's hostname,
  so `forget` keeps them as a group of their own beside those of `--host unos` (below).
- The review in a fresh context (ADR-0017) of e663f14 found 13 points. Each got a test that failed
  on e663f14 first (30 more in `test_offsite.py`, 2 in `test_pool_persistence.py`, 1 in
  `test_dev_scenarios.py`; the fake restic now takes locks and exits with 3), and each was fixed:
  1. An unmount of the target's disk during a run let restic's local back end make the missing
     folders on the empty mount point, on the system disk. The target, the sources and a
     restore's new folder are now held open while restic runs (Save's init too), so a plain
     umount fails as busy; Unmount and Remove Pool answer 409 naming the copy while a copy or a
     restore uses the pool. No code of UNOS unmounts lazily.
  2. `forget` and `check` failed at once beside any other lock: every step of a copy and of the
     restores waits up to 10 minutes (`--retry-lock 10m`), and the listing takes no lock.
  3. Exit status 3 of the data's backup failed the run although the snapshot was saved: now the
     other steps run, vanished files are a note, and other unreadable files fail the run at its
     end, the first 5 named with the count. The status and the Scheduler's record say which, and
     the page shows the note.
  4. Run now of a task ran as a background task of Starlette, for which uvicorn waits at its stop
     without a limit: it is a task of the scheduler now, which the stop ends and records. The
     scheduler and backup tests' clients wait for those runs (`waiting_for_runs`).
  5. `check` of an s3 store made a second cache: `--with-cache`; ADR-0337 says the cache in /tmp
     counts against the 2 GiB of the tmpfs.
  6. A target folder that could not be made answered 500: 400 with the reason, as a restore's.
  7. A failed restore of files left its empty folder: it is removed.
  8. The system disk was compared by filesystem: whole disks now (another partition of the system
     disk, btrfs with all its devices, device mapper and md), for the target and the same-disk
     warning; the sources and a restore's pool keep the filesystem rule (a pool not mounted).
  9. Forget this target left `offsite_secrets.json.bad`: it removes the `.bad` copies, and a file
     that fails validation is logged without its values.
  10. The configuration archive stayed in RAM for the whole copy: it is let go after its step.
  11. `docs/INSTALL.md` did not say that update.sh's copy of the data volume holds
      `offsite_secrets.json`: it does.
  12. The device check could start beside a task that copies: it refuses to; that no route cancels
      a copy, so one that outlives the waits fails the clean-up, is in its docstring.
  13. The snapshots named the container's hostname: every backup names `--host unos`.
  Two tests changed their setup with the code, not their assertions: the refusal of a target on
  the root filesystem sets both real checks, and the refusal of a source that is not mounted
  replaces `on_system_filesystem`, which judges the sources now.
- The device check of 8fef386 (deployed; `scenario.sh offsite` passes; Save refuses a folder on
  the system disk and a wrong password): Save and test with an S3 store out of reach
  (`https://127.0.0.1:9`) held the request and the lock of the module for restic's whole 10
  minutes, from 11:23:28 to 11:33:33 UTC, while the HTTP client gave up after 150 s and every other
  action of the copy answered 409: restic 0.18 retries a refused connection for that long. Save and
  a run now connect to an S3 store first (TCP and the TLS handshake, 10 s, nothing sent) and refuse
  one that does not answer at once, and Save's `cat config` and `init` end after 120 s with 400
  "The store did not answer within 120 s"; tests first (7, each failed on 8fef386).
- A second review of 8fef386 (the coordinator's decisions, the reviewer's reading of restic
  0.18.0) found, each with a test that failed first: (1, 2) restic tests a lock of its own host
  name by sending SIGHUP to the lock's PID, any host process with `pid: host` after a crash, and
  every installation is "unos": each restic now runs in a UTS namespace of its own with the host
  name `unos-<12 hex>` (`unshare --uts` and a Python step that writes it, restic's arguments and
  environment unchanged), and one copy repository serves one installation; (3) `check
  --with-cache` read a warm cache, so a damaged store passed: `check --no-cache` for both kinds;
  (4) a disk target whose disks cannot be found was taken by the filesystem rule: 400 "UNOS
  cannot tell which disk holds ...", logged, and a partition of an md or device-mapper device
  follows to its members (loop devices stay as they are); (5) restic leaves out a file that
  vanished before its first open without a word: the docs say so, and the fake restic does too
  unless a test asks for the reported case; (6) the scheduler tests' clients raise what a run
  raised, Forget removes a staged `.offsite_secrets.json.<hex>.tmp`, and Delete Volume answers
  409 naming the copy while it uses the volume.
- f4ecd22 on the device: `scenario.sh offsite` OK, the S3 refusal answers at once; make check
  6676 passed, the E2E specs 181. A three-lens review of 8fef386..f4ecd22 then found, each with a
  test that failed first where one could: a stop or a timeout killed restic with SIGKILL, so its
  lock, of its own host name now, stayed for 30 minutes and failed the next copy at forget: restic
  gets SIGTERM first and SIGKILL 5 s later (the fake removes its lock on SIGTERM, and its unlock
  follows restic's rule); the host-name step runs with `-B`; the system-disk rule refuses when
  the disks of only one of /mnt and /data are known; Delete Volume checks and deletes under the
  lock with which a copy names and opens its folders; tests of the fake's vanished files and of
  the stand-in's overrides. The guide says that a copy ended hard leaves the repository locked
  for up to 30 minutes.
- Open: the deploy of these fixes, `scripts/dev/scenario.sh offsite` again before and after a
  reboot (ADR-0018), a copy to a USB disk and to an S3 store, and `passes`.

### 2026-11-11: HTTP notifications that cannot connect from the container go from the host's network

- `system.telegram-from-the-device` (ADR-0336, which supersedes the proposal ADR-0046): from the
  dev device's network Telegram answers over IPv6 only, and the containers have no IPv6, so every
  Telegram alert of the device failed with ConnectTimeout. Now Telegram, webhook, Discord and
  Slack send each request from the container with a connect timeout of 5 seconds and, only when
  httpx made no connection, so that the request was not sent, once more from the host's network:
  the host's curl through `host.run_on_host_async`, the request as curl's config on its standard
  input and fixed options alone on its command line. An HTTP answer or a timeout after the
  request is never followed by a second try. curl joined `HOST_TOOLS` (`deploy/install.sh`
  installs it); e-mail is unchanged. Neither IPv6 for the compose network nor e-mail instead:
  ADR-0336 says why.
- Settled beyond the brief, with the reasons in ADR-0336: `data-raw` for the body, not
  `data-binary` (curl would read a body that starts with "@" from a file of the host);
  `--disable` (no curlrc of the host) and `--globoff` (the URL is one URL) on curl's command
  line; the URL as httpx sends it; Telegram's `"ok": true` needed on both paths (before, HTTP 200
  alone counted); no attempt from the host for a request with a NUL character or a header that
  httpx would not send; curl gets what is left of the channel's time (15 seconds for Telegram, 30
  for the others), so that a send that connects nowhere ends as soon as before; secrets shorter
  than 8 characters are not replaced in error texts.
- Tests first: 42 tests of `TestFromTheHostsNetwork` in `test_notifications.py` failed before the
  change, and so did 4 older tests that the decision changes. `FakeHost` got a curl that reads its
  config as curl does; one test sends through the workstation's real curl to a local receiver,
  and fails without `--globoff` or with `data-binary`.
- A review in a fresh context (ADR-0017) found, each fixed with a test that failed on the code
  before the fix: a header name that starts with "@" made curl read headers from a file of the
  host, and curl sent other headers that httpx refuses once connected (now such a request is not
  sent from the host; a header without a value is written `Name;`, which curl sends as such,
  where `Name:` dropped it); a service that names a webhook URL's path alone ("Cannot POST
  /<path>") showed its key, and a short header value such as "1" garbled every error text (the
  path with the query is a secret now, and values under 8 characters are left); httpcore gives
  the connect timeout to the name lookup and TCP and again to TLS, so curl's fixed 10 seconds
  could make a Telegram send last 25 (curl gets what is left of the channel's time now); a
  captive portal's whole page became Telegram's failure text (now its first 200 characters).
  Not changed, with the reason: the host's attempt runs in asyncio's default thread pool, as every
  `run_on_host_async` does, which the brief named; a loopback URL names the host on the host's
  network, which ADR-0336 states.
- Open: the deploy, the device check (a test notification to the owner's Telegram channel from
  the dev device) and the re-check after a reboot (ADR-0018).

### 2026-11-11: Every background command of an agent has a deadline

- `process.background-commands-have-deadlines` (ADR-0335): on 2026-11-10 a wait loop written for
  bash, `until [ "$(date -u +%H%M%S)" \> "222340" ]; do sleep 15; done; ...`, ran in the
  background in the Bash tool's zsh, whose `[` does not know `\>`: the loop never ended, so
  nothing woke the session, which stalled for nine hours. The guard's new rule
  background-deadline refuses a command that the Bash tool runs in the background unless it is
  one call of `timeout` with a deadline of more than 0 and at most 7200 s, after at most
  `cd DIR &&`, assignments and redirections, with nothing after it and no `$(...)`, backticks
  or `<(...)` outside single quotes; loops and pipelines go inside
  `timeout <seconds> bash -c '...'`. The SIGKILL of `-k` counts into the 7200 s, `-s` names a
  signal that ends a process, and `--foreground` is refused. The rule reads the line with the
  guard's own lexer, so a quoted heredoc may follow the call, and a line with an unbalanced quote
  passes: the shell refuses it and runs nothing. Foreground commands and the terminal tool are
  not affected.
- Tests first: on the guard of main 75 tests of `test_agent_guard.py` failed, the 53 refused
  cases of `BACKGROUND_CASES` (the loop of 2026-11-10 among them), 17 checks of the messages,
  the 3 lexer cases of `CASES`, the docstring check and the denials log check.
- AGENTS.md (Hard rules), `docs/TESTING.md` and GLOSSARY.md (Background command) state the
  rule, and the device-checks skill runs `scenario.sh all` in the background under
  `timeout 3600`.
- The review in a fresh context (ADR-0017) found commands of the outer shell that the guard's
  lexer missed, for every rule: the substitutions inside `$((...))`, a heredoc delimiter
  `<<$'EOF'` and a carriage return before `#`; and in the new rule signals of `-s` that do not
  end a command, `--foreground`, an unbounded `-k`, words of the command echoed in its messages
  and a trailing `;` refused. All fixed, with cases in both tables. Kept as they are, with the
  reasons in ADR-0335: a command that ignores SIGTERM runs on unless `-k` is given (`-k` is not
  required, since its SIGKILL would cut the clean-up of a scenario check), and a redirection of
  a FIFO waits without a deadline; the rule also refuses the rare forms `1e3`, `+60`,
  `--kill=10` and `-vk 10` that GNU timeout takes.

### 2026-11-10: SMB lockout, server limits and the upload speed pass on the device

- `shares.smb-login-protection`, `shares.samba-runtime-writes` and `security.server-limits`
  pass: the smb scenario check, with its new lockout step, passed before and after the reboot of
  22:01 UTC; the policies, uvicorn's limit and Caddy's 431 were kept after it; the upload speed
  was measured from the workstation (the Dev device section has the figures and the MiB). The
  new `scripts/dev/upload-speed.py` measures it: it signs in from dev.env without showing the
  password, uploads through Caddy into a folder of the pool, deletes it and signs out.

### 2026-11-10: Samba locks an SMB account after 5 failed sign-ins

- `shares.smb-login-protection`, step 1 (ADR-0334, which supersedes ADR-0280 and ADR-0310): the
  startup reconcile keeps Samba's account policies "lockout duration" 5 (minutes), "reset count
  minutes" 5 and "bad lockout attempt" 5 beside the password history of 3, each read at every
  start and set only when it differs (`accounts.ensure_account_policy`). After 5 wrong passwords
  in a row Samba refuses the account, the right password too, with NT_STATUS_ACCOUNT_LOCKED_OUT
  for 5 minutes. Tests first: 19 tests of `test_smb_reconcile.py` and 27 of
  `test_dev_scenarios.py` failed before the change.
- The smb scenario check: step 3 reads the four policies, the step with a wrong password expects
  passdb.tdb to change, and a new step, the last that signs in, locks the check's account and
  expects the refused right password to leave passdb.tdb as it was; the account is deleted
  while it is locked.
- `shares.samba-runtime-writes` is back to `passes: false`: ADR-0334 rewords its description and
  step 2 (a counted failure writes passdb.tdb), until the smb check passes with the lockout
  before and after a reboot. Open: the deploy and the device checks of both entries, and the
  review in a fresh context (ADR-0017).
- A fresh-context review found the threshold set even when one of the lockout's times failed,
  which would lock on the host's terms (30 minutes, or 0, which never unlocks): now the
  threshold waits for both. Also fixed: turning SMB off and on ends a lock (ADR-0334 said the
  web interface could not), the policy locks accounts that UNOS did not make too, and the
  reworded step of `shares.samba-runtime-writes` names the one write of a retry that clears an
  old count.

### 2026-11-10: Limits on open connections and header blocks

- `security.server-limits` (ADR-0333): uvicorn took any number of connections and Caddy header
  blocks up to 1 MB. Now uvicorn serves at most 128 open connections (503 beyond them), the
  supervisor at most 64 sockets and 16 waiting for their sign-in, and Caddy refuses a header
  block over 16 KB (431). Tests first: `test_request_limits.py` (both images, and a real uvicorn
  refusing over its limit and serving again), `test_ws_auth.py` and `test_deploy_scripts.py`.
  A fresh-context review found that uvicorn's limit never refuses a WebSocket upgrade while
  every socket counts toward it, so unauthenticated sockets could have filled it for every
  request: hence the socket caps. Open: the throughput over 2.5 GbE (a LAN client) and the
  device check of Caddy's 431.

### 2026-11-10: Problems reach every channel once and show in a banner

- `system.alert-events` (ADR-0332, which supersedes the proposal ADR-0047): the branch's first
  commit sent the SMART and RAID alerts through every enabled channel (`send_through`). Now
  `supervisor/src/health/problems.py` keeps the problems that are still there in RAM, by source:
  the RAID monitor's pass reports the managed pools (not mounted or no disk found, degraded,
  read-only after an error, 90% and 95% full), the SMART monitor's the disks (health FAILED, a
  failed self-test) and a new check every 5 minutes the scheduled tasks (last run failed, a missed
  run). A new problem is alerted once through every enabled channel; one that is gone is dropped
  without an alert. The events socket sends administrators the list after auth_ok and when it
  changes, and the web UI shows it above every page (`ProblemsBanner.svelte`, `problems.ts`).
- Beyond the brief, with the reasons in ADR-0332: a managed pool none of whose disks is
  connected is reported as not mounted, from the same read of the host's fstab
  (`pools_and_managed_names`); a failed self-test is a problem beside health FAILED; a missed run
  counts only the times after the task's new field `schedule_since` (set when it is created,
  enabled again or given another schedule, in writes made anyway) and after a change of the
  host's zone, and a run that still runs counts as its start; a managed pool's new-errors alert
  is left out in the pass whose new pool_degraded or pool_read_only alert names the errors. A
  socket whose user is no longer an administrator gets an empty list.
- Tests first: `test_alert_events.py` and the new loop of `test_lifespan.py` failed before.
  `test_storage.py`'s Telegram timeout test patched `send_telegram`, which the first commit took
  out of the health router, and failed on the branch: it patches `send_through` now. E2E:
  `health-banner.spec.ts` (English, Russian, a regular user), the banner's view of
  `mobile-layout.spec.ts`, and `websocket.spec.ts` expects the problems message between auth_ok
  and the stats. The first `make check` failed `test_dev_scenarios.py`'s signal test: a thread of
  the alerts' pool, left alive by a new test that ran before it, took the SIGINT that the test
  sends its process while the main thread blocks it; that test now ends a pool of its own.
- A fresh-context review found, fixed with a test that failed first: a task enabled again or
  rescheduled was reported as missed after a restart (the time it was seen so lived in RAM: now
  `schedule_since`); an NVMe disk only beyond its temperature threshold counted as failing; a
  pool hovering at 90% was alerted at each crossing (now it stays low on space until below 85%);
  a check of GET /health/raid could hand the RAID monitor another read of the pools (the read is
  now kept on the event loop as its check returns); a pool made read-only with new errors got two
  alerts; a managed pool whose state cannot be read was no longer alerted (the RAID monitor
  alerts it, once from healthy); a disk replaced at a path while asleep kept the old verdict. Not
  fixed: a host command that does not answer within its timeout fails the RAID monitor's whole
  pass, as before this change (storage.commands-time-out-cleanly); a disk that comes back at
  another path is alerted again (verdicts by path). Open: the device check of the real pools,
  disks and tasks, and a reboot.

### 2026-11-10: The hostname and the time zone pass

- `system.hostname-and-time-zone` passes: System > General's card sets both (E2E), the API
  changed both on the device, and a zone and a hostname set before a reboot were still the
  host's after it (the Dev device section has the MiB). Samba and wsdd2 take the hostname when
  they start: after a change of the name they show the old one until they restart or the host
  reboots.

### 2026-11-10: System > General sets the hostname and the time zone

- `system.hostname-and-time-zone`: the page only showed the name. Now its card Name and time
  zone sets the hostname (`PUT /network/hostname`) and the time zone, chosen from the host's
  list (`PUT /system/timezone`), with the host's message when it refuses a change. Test first:
  `system-general.spec.ts` on route fakes (the stand-in has neither hostnamectl nor
  timedatectl), in English and Russian; the phone layout spec passes (121). What is left: the
  deploy and the re-check after a reboot.
- A fresh-context review found nothing serious; fixed: an Unknown or unlisted zone showed
  "Select an option" (now the zone, or Unknown); the Select of ~600 zones had no type-ahead (the
  shared `Select` now jumps to the first option with the typed letters, with PageUp and
  PageDown and `aria-activedescendant`); a refused zone stayed selected; one failed GET hid
  both rows; the notices lacked `role="status"` and `role="alert"`; the zone's hint pushed
  Save down; a Russian calque; the spec now covers a refused hostname, typing a zone and an
  unknown zone.

### 2026-11-10: The time zone and the hostname change on the device

- Main e6a44d5 deployed: through the API the device's zone changed to Etc/GMT-3 on the host and
  in the Scheduler, and back to Etc/UTC; its hostname changed and showed at once in both GETs,
  and went back to its old name (the MiB are in the entry's notes). `system.hostname-and-time-zone`
  stays open for the controls on System > General and the reboot check.

### 2026-11-10: The time zone routes ask the host

- `system.hostname-and-time-zone` (ADR-0331), found while checking the device: the time zone
  routes of `/api/v1/system` ran timedatectl inside the supervisor container, which has no
  systemd, so the zone read as Unknown and could not be set; `PUT /system/hostname` ran
  hostnamectl there too. Now timedatectl runs on the host through `host.py`, and the duplicate
  hostname route is gone (`PUT /network/hostname` sets the host's name). Tests first:
  `test_system_time_zone.py` (all six failed before), and `api.spec.ts` on the stand-in. No page
  of the web UI sets the hostname or the zone yet: that, the device check and the reboot check
  stay open.
- A fresh-context review found, all fixed: the zone pattern refused 45 of the host's zones
  (`Etc/GMT-3`, `America/Port-au-Prince`); both GETs of the hostname read a bind mount of
  /etc/hostname, stale after a change (now `hostname` on the host, and the mount is gone); a
  failed hostnamectl fell back to `hostname NAME`, lost at the next boot (now 500); the old
  `TestTimezone` of `test_system.py` passed for either code (removed); timedatectl runs under the
  host's `timeout` (ADR-0258); the ADR's wording.

### 2026-11-10: The device after the second deploy and a reboot

- Main f68c7c1 deployed and the device rebooted from the API at the owner's word; before and
  after the reboot every scenario area passed, 88 steps (the Dev device section has the MiB).
  `deploy.docker-log-rotation` passes: `check.sh` lists the capped log options of both UNOS
  containers after the deploy and after the reboot. A sign-in with its session row wrote 0.086
  MiB. The other open host-level entries need checks by hand (their notes), spare disks, the
  install scripts on a fresh host, or the owner (the firewall, a security setting, ADR-0024).

### 2026-11-10: Docker rotates the containers' logs

- `deploy.docker-log-rotation` (ADR-0330, which supersedes the proposal ADR-0178): the
  containers' json-file logs grew without a limit on the system disk. Now the compose file caps
  the logs of `unos-supervisor` and `unos-webui` at 10 MB with 3 files, and the App Store and
  the Containers page create their containers with the same cap. Tests first:
  `test_deploy_scripts.py`, `test_containers.py`, `test_apps.py`. The device check after the
  next deploy: `check.sh` lists the options.
- A fresh-context review found the code right (docker-py 7.2.0 passes `log_config` to the
  HostConfig of `run` and `create`); fixed: an upgrade note in CHANGELOG.md, the ADR's
  consequences (the container's options override the host's defaults), `check.sh`'s comment,
  and a test of every service of the compose file.

### 2026-11-10: Sessions end on the server

- `auth.session-revocation` (ADR-0329): a JWT stayed valid for 24 hours whatever happened. Now a
  sign-in stores its session as a row of `user_sessions`, in the commit it made before, and a
  session token is valid only while its row exists. Logout ends the session on the server, a
  password change the user's other sessions, and a password set by an administrator, the
  deletion of the user, `reset-admin` and the Users page's End sessions button all of them; a
  live-data socket closes at its next check. API tokens stay as they are. Tokens of before the
  update are refused: everyone signs in once more.
- Tests first: `tests/test_sessions.py`, the ended-session case of `test_ws_auth.py` and of
  `test_emergency.py`; the specs `auth.spec.ts` (a copy of the token gets 401 after Logout) and
  `users.spec.ts` (End sessions). Database only: not host-level.
- A fresh-context review found, all fixed: a sign-in on a full `/data` answered with a token
  that did not work (ADR-0251): the session is kept in memory then; the deletes ran before a
  password's guarded commit: they go with it now; a restore brought the backup's sessions back:
  it ends every session but the restoring administrator's, who sees the result (the full E2E
  run showed the four restore specs failing without that); four more pages per sign-in: the
  table is `WITHOUT ROWID` without indexes; `two-factor.spec.ts` reused a session after Logout;
  an administrator setting their own password lost their session. E2E: 675 passed in the full
  run, then the restore, scheduler, two-factor, auth, users and backup specs, 93 passed. The SD
  MiB of a sign-in is measured after the deploy.
- The full E2E run of `security.tamper-evident-audit-log` had one failure, "uploads files from
  the keyboard" of `files.spec.ts` (no file chooser within 15 s while backend tests ran beside
  it); it passed three times alone. A flake to fix, not a regression of that branch.

### 2026-11-10: Each audit entry carries the hash of the one before

- `security.tamper-evident-audit-log`, under the owner's delegation: the audit log's reader
  skipped what it could not read, so an entry edited or removed in the file left no trace. Now
  each entry stores the hash of the one before it and its own, over the fields of its line,
  chained under the log's lock across the rotated files and processes. `GET
  /api/v1/audit/logs` says for each entry whether its place in the chain holds, and the Audit
  page marks one where it does not with "Chain broken". The newest entries have no entry
  after them: the supervisor keeps the last entry it wrote or read at the end, and a critical
  entry of the user `system` says when that is gone or changed (ADR-0328).
- Test first (ADR-0186): the 7 tests of `TestHashChain` in `test_audit_log.py` failed before.
  Four tests that size their files by entries, three in `test_audit_log.py` and one in
  `test_emergency.py`, now leave room for the two hashes.
- A fresh-context review found steps 1 and 3 met, and step 2 not for the newest entries: a
  removed or rewritten end left no trace. It also found:
  - an entry without hashes inside the chain that showed no mark;
  - a hash over every field of the model, which a new field would have broken for every older
    entry;
  - no check of the chain under concurrency;
  - a false break of a clearing when the file's order is not the order of the times;
  - the texts;
  - `make check` failing: the emergency test's size and the order of the index row.

  Fixed with the end that the supervisor keeps and the entry that reports it, the mark for
  entries without hashes after the chain, the hash over the fields of the line (a clearing's
  rewrite keeps them), chain checks of eight threads and of the other process's appends, and
  `TestTheEndOfTheChain`. The false break of a clearing is recorded in ADR-0328, with what the
  chain cannot show: removing the oldest entries, and a chain written anew while the supervisor
  was stopped. The audit tests: 131 passed. Not host-level.

### 2026-11-10: The dashboard's header fits a phone with a long host name

- `webui.mobile-layout`, a regression that the full E2E run of `apps.catalog-without-docker`
  showed: on a 375 px screen the dashboard's header did not wrap. On the stand-in, the
  workstation's host name (beast.tail.home.arpa), the live stats' time and Refresh then reached
  past the screen, and two menu checks of `mobile-layout.spec.ts` failed: the entry failed on
  main c1e8032 while the ledger said it passed. Now the header wraps and a long host name
  breaks (`break-words`), as ADR-0302 asks, and so does the host name in System > General.
- Test first (ADR-0186): the view "Dashboard with a long host name" of `mobile-layout.spec.ts`
  waits for a name with no place to break and for the time beside Refresh; it failed in both
  languages before the fix. A fresh-context review found that the view did not wait for the
  name, which the page shows after all its requests, and that `break-all` is ADR-0302's class
  for code, not names: both fixed. The phone layout and dashboard specs: 131 passed. Not
  host-level.

### 2026-11-10: The App Store lists its catalog without Docker

- `apps.catalog-without-docker`, under the owner's delegation: the Apps page loaded the catalog
  and the installed apps in one `Promise.all`, so a Docker that could not be reached hid the
  catalog too. Now each loads on its own, with a state each. The catalog is listed as soon as it
  came, a count shows only from a load that succeeded, and a failed load says so with the
  server's message, as the 503 of ADR-0323 (ADR-0327).
- Test first (ADR-0186): on the stand-in, which has no Docker, "should show app cards in
  catalog" of `appstore.spec.ts` runs without its skip. The Apps page check of
  `containers.spec.ts` expects the catalog and the message; both failed before.
- A fresh-context review found both steps met, and five points:
  - the catalog still waited for Docker's answer, under one loading flag;
  - counts of 0 during a refresh;
  - a spec that matched the Installed tab with its count;
  - a catalog error without its name;
  - the record.

  Fixed with "the catalog does not wait for the installed apps" (a held answer on a route
  fake), exact tab names, and the texts. The notes of `apps.install-on-a-pool` still say that
  the catalog cards skip on the stand-in, which no longer holds.
- The App Store, container, app and phone layout specs on the stand-in: 163 passed. The whole
  of `make e2e-local`: 674 passed, 14 skipped, and 2 failed. The two are the phone layout's menu
  checks of the dashboard, whose header does not wrap the workstation's host name
  (beast.tail.home.arpa) beside "Updated" and Refresh: a bug of the dashboard on main, not of
  this change, which the next change fixes (`webui.mobile-layout`). `make check` passed. Not
  host-level.

### 2026-11-10: JSON state files are replaced whole, and a damaged one is kept as .bad

- `architecture.atomic-state-files`, under the owner's delegation: ten of the supervisor's 14
  JSON state files in `/data` were rewritten in place, and most loaders read a file that they
  could not parse as the defaults. So the next write made the loss final, for the firewall
  rules, the scheduled tasks and the power, UPS and update settings, and nothing said so.
- Now `src/state_files.py` replaces each of them through a temporary file and a rename.
  Synced first are the SMART history, the notification files and the key file, as their ADRs
  want; no other write gained a sync on the event loop. `load_json` renames a file that cannot
  be read as its state to `<name>.bad`, names it in a warning and answers the defaults. A file
  that cannot be opened reads as none where it is a history or a cache, and raises where it
  holds settings, rules or tasks. The SMART history's endpoints keep their 500 until the
  monitor's pass, which keeps the file as `.bad` and starts anew (ADR-0324).
- The key file of the backup tasks and the notification files had decisions of their own, now
  restated: ADR-0325 supersedes ADR-0240, and ADR-0326 supersedes ADR-0260. The references to
  the two older ADRs moved; GLOSSARY.md has the term State file.
- Test first (ADR-0186): `test_state_files.py` kills a writer with SIGKILL before and after the
  rename. For each of the 14 files, a table checks the rename and the `.bad` file, and scans of
  `src` keep the table and the writes in step. On the code before, 10 of the 14 rename checks
  and every `.bad` check failed.
- Four existing tests followed the new writes:
  - `test_notifications.py`'s full disk also fails the write of a file made with `os.open`;
  - `test_scheduler_validation.py`'s sync check counts only the key file;
  - the SMART monitor's check in `test_smart_predictions.py` expects the `.bad` file;
  - `test_standin.py` lists no probes of the two files that `main.py` reads through their loaders now.
- A fresh-context review found every step met, and:
  - a runs file that cannot be opened stopped the supervisor's start, a task's run and the RAID
    check;
  - a reader could set aside a file that a writer had just put in place;
  - writes that were synced on the event loop;
  - RAID gauges that went missing for a status without pools;
  - claims of the ADR and the records.

  Fixed:
  - the start and a run go on, and the RAID monitor reads such a status as none;
  - renames take the path's lock, and a reader checks the inode;
  - only the files that an ADR wants durable are synced;
  - the health outputs read the status as before;
  - the texts.

  Each fix has a test. Not host-level.

### 2026-11-10: The container and app routes answer 503 when Docker does not answer

- `apps.docker-down-503`, under the owner's delegation: while Docker could not be reached, the
  container API answered 500 with docker-py's error, the Apps API 500 also for errors that Docker
  answered, and the Containers tab showed counts of 0 and "No containers found" with its Create
  button under the error (ADR-0175). Now `get_client` raises `DockerUnavailable` when Docker
  does not answer `docker.from_env`'s version request, and `NO_ANSWER` holds it and the forms of a
  refused or broken connection. The containers API answers them with 503 `Docker is not
  available: <reason>` on every route. The Apps API does so for the first request of each route to
  Docker, and an operation that fails after it keeps its 500 `Failed to ...` (ADR-0254,
  ADR-0256). A read timeout and an error that Docker answers keep their status, and the
  Containers tab shows the error alone (ADR-0323).
- Test first (ADR-0186): 41 tests of `TestADockerThatDoesNotAnswer` in `test_containers.py` and
  `test_apps.py` failed before. On the stand-in, which has no Docker socket, `api.spec.ts`
  checks the 503 and its reason for three routes that it skipped there. The "Containers without
  Docker (stand-in)" specs check the Containers tab, its Images tab and the Apps page; the test
  stack skips both, as it skips the specs that need Docker (ADR-0215).
- A fresh-context review found the step met for a Docker that is down, and nine points:
  - the tab's error, shared with the Create dialog, which cleared it;
  - a broken stream or chunked answer that gave 500;
  - a read timeout of a long stop that was called unavailable, and so was an error answer to the
    version query;
  - the Apps API's operations, which ADR-0254 and ADR-0256 keep at 500;
  - the API checks, which ran on the test stack against ADR-0215;
  - an Apps-page spec that passed before;
  - records that claimed more than the code did.

  All are fixed as ADR-0323 now says. Left: the Apps page's empty texts under the error
  (`apps.catalog-without-docker`), and the dashboard, which does not show the 503.
- The whole of `make e2e-local` on the first version: 674 passed, 15 skipped. After the fixes,
  the specs of the API, the Containers and Apps pages, the App Store, the phone layout and
  the languages: 218 passed, 15 skipped. Not host-level: it changes the answers, not the host's
  containers. A stopped Docker on the dev device stops UNOS with it, so the stand-in is the test
  host.

### 2026-11-10: Uninstall restarts smbd once and restores smb.conf through a temporary file

- `deploy.uninstall-samba-restore`, under the owner's delegation ("work on through the
  features, ask no questions; I allow everything", in translation): with `smb.conf.original`,
  `deploy/uninstall.sh` restarted smbd twice when it removed a Samba password, and copied the
  file straight over smb.conf, so that a copy that stopped halfway left a part of a file
  (ADR-0175). Now `remove_samba_passwords` leaves the restart to `restore_samba_config`, whose
  restart with the restored file also ends the removed passwords' connections; when the restore
  fails, it runs the `try-restart` itself. The restore copies the file to
  `.smb.conf.unos-restore` next to smb.conf and renames it over smb.conf, and a failed restart
  after it is reported (ADR-0322, which supersedes ADR-0180).
- Test first (ADR-0186): `test_with_smb_conf_original_smbd_restarts_once`,
  `test_a_restart_that_fails_after_the_restore_is_reported` and
  `test_a_copy_that_fails_halfway_leaves_smb_conf_whole` failed on the script before.
- A fresh-context review found both steps met in the code, but the rename and its order before
  the restart unasserted, a failed removal of the temporary file that ended the script under
  `set -e`, a new file's mode taken from the umask, a warning that named one cause of a failed
  restart of two units, a CHANGELOG line that said the restart only comes when smbd runs, and the
  entry passed without the device check of a host-level change. Fixed: three more tests (the
  copy, the rename and the restart in order, a rename that fails, a temporary file that cannot
  go), `rm -f ... || true`, `cp -p`, the warning and the CHANGELOG line; the temporary file takes
  a name of the supervisor's temporary files, which a new installation removes. The entry stays
  open as host-level (GLOSSARY.md), in the by-hand row of the feature-pass skill: uninstall.sh
  never runs on the dev device, as for `deploy.uninstall-script`, which names ADR-0322 now. The
  uninstall tests, 36 passed.

### 2026-11-10: The web UI's Caddyfile exists once

- `deploy.one-caddyfile` (ADR-0201), under the owner's delegation ("work on through the
  features, ask no questions; I allow everything", in translation): the copy of
  `webui/Caddyfile` in `deploy/` is deleted. Nothing used it: the web UI image copies
  `webui/Caddyfile` (`webui/Dockerfile`), and neither compose file mounts a Caddyfile. Its
  references went with it: the deploy component's runtime files of `scripts/dev/tree.py`, the
  copy's header check in `test_standin.py`, the web UI container of `test_apps.py` (a bind of the
  copy that the real container never had), the comment of `webui/Caddyfile`, `deploy/AGENTS.md`,
  `webui/AGENTS.md` and ADR-0025, whose decision is unchanged, corrected in place (the adr
  skill's first case).
- Test first (ADR-0186): `test_the_web_ui_has_one_caddyfile` of `test_deploy_scripts.py` failed
  with the copy, which it listed beside `webui/Caddyfile`; it also checks that `webui/Dockerfile`
  copies the file and that no compose file mounts one.
- A fresh-context review found both steps met, but step 2 without a test, and the record of the
  checks missing: `test_nothing_refers_to_a_copy_of_the_caddyfile_in_deploy` runs `git grep`
  for the copy's path, which only this entry's steps name. `make check` passed; `make e2e-local`
  did not run: the stand-in serves the web UI through `scripts/standin/front.mjs`, never through
  `webui/Caddyfile`, and the build does not read it. The review also found that the Dev device
  section said main's later commits change no runtime file: corrected.
- The dev device's checkout keeps the copy until the next sync of `deploy.sh`, which deletes the
  files that the tree no longer has; nothing on the device reads it. The new comment of
  `webui/Caddyfile` is a change of the web UI's runtime files, as the web UI changes merged since
  the last deploy are. Not host-level.

### 2026-11-10: The E2E specs fail where an API answers an error

- `testing.no-skips-that-hide-api-errors`, under the owner's delegation ("work on through the
  features, ask no questions; I allow everything", in translation): four checks of the E2E specs
  passed over an API that answered an error, as the reviews of `testing.no-compat-skips` found.
- `files.spec.ts`: the File Browser specs skipped whenever POST /files/mkdir failed in every
  folder of the pools. A folder that takes no new folder answers 403 "Permission denied" (an
  unmounted pool's mountpoint is immutable); any other answer, a 403 for another reason included,
  now fails the test with its status and text, and the skip fires only when every folder refused
  so.
- `shares.spec.ts`: the seeded share's import-notes specs skipped without notes, which e2e.sh's
  new stand-in always has; they fail now. Run twice on one stand-in (`--repeat-each 2`), both
  failed in the second round, where the old skip passed over them. On the test stack a second
  run fails them until `down -v` gives it a new database (docs/TESTING.md). The IPv6 hints skip
  only when the request to `[::1]` is refused or this machine has no IPv6 loopback; any other
  failure of the request, and an answer that is not OK, fails.
- `api.spec.ts`: GET /files/list expects 200, and GET /snapshots/ of the pool folder testpool 200
  with no snapshots. GET /containers/images and GET /snapshots/subvolumes, which the stand-in
  answers with 500 (no Docker; no findmnt), skip with their reasons on `E2E_STAND_IN=1`, which
  both local targets set, so that no E2E check runs them now: the backend tests hold their
  answers (`test_containers.py`, `test_snapshots.py`), and `apps.docker-down-503` is to give the
  images' answer on the stand-in, 503, a check of its own. `test_standin.py`'s
  `SKIP_CONDITIONS` lists `health === null` in place of `!reachable`, and `notes.length === 0`
  no more, as ADR-0317 foresaw.
- The three specs on the stand-in: 134 passed, 17 skipped (the stand-in skips of `api.spec.ts`);
  the whole of `make e2e-local`, 668 passed and 18 skipped, the two new skips more than before.
- A fresh-context review found every step met and five points, none blocking: a 403 for another
  reason than "Permission denied" (a path refused as outside the pools, a symbolic link) also let
  the File Browser specs skip; the two skipped checks run on no local target; any failure of the
  request to `[::1]` skipped the IPv6 hints; two comments still said that the test stack's pools
  folder is empty; and a word of docs/TESTING.md. Fixed: the first by the detail, the third by
  the reasons of no answer, the fourth and fifth in place; the second is recorded above. Not
  host-level.

### 2026-11-10: SMB changes are audited with actions of their own

- `shares.rollback-era-safeguards`, at the owner's word to go on ("Continue", in translation),
  under ADR-0207: the SMB code audited with `other`, `user_updated` and `share_updated` only, because a release
  after an update's rollback dropped the whole audit log at an action it did not know. The
  audit loader of this release skips a line it cannot read and keeps the rest, so SMB changes
  have actions of their own now: `smb_account_updated`, `smb_shares_imported`,
  `smb_reconciled`, `smb_restored` and `share_permissions_repaired`, and the NFS exports
  `nfs_export_created` and `nfs_export_deleted`; the audit page names them in English and
  Russian (ADR-0320, which supersedes ADR-0157). The purge of SMB rows (ADR-0137) and the copy
  of foreign share sections (ADR-0092) stay, for restored and hand-edited databases and files;
  only their remarks on older releases went from the comments.
- Tests first (ADR-0186): the tests of the startup reconcile, the restore, an SMB account, the
  import and the NFS exports expect the new actions and failed before the change (Repair
  permissions' test runs on Linux only). 1075 tests of those files and of the audit log, the
  share API and the SMB integration passed; svelte-check and the text check passed. Not
  host-level.
- The fresh-context review (ADR-0017) found no blocker. Fixed: ADR-0292 still said that NFS
  changes are audited as share changes, so ADR-0321 supersedes it with that sentence changed;
  ADR-0320 names all changes of an SMB account (also disallowed and revoked at a promotion),
  says that a user's deletion keeps one `user_deleted` entry, and says what a release before an
  action does with it after an update's rollback: it keeps it when it appends or rotates, but a
  deletion of old entries by date rewrites the file without it; the code and tests that applied
  the rule cite ADR-0320; remarks on older releases in tests and a stale comment on the audit's
  writes are gone; USER_GUIDE.md lists the NFS exports and the SMB state after a restore among
  the recorded changes. Open: `test_share_repair.py` is Linux-only, and `linux-tests.sh` needs
  the supervisor's root filesystem in the device's `/dev/shm`, gone since the reboot; making it
  again is an owner action, so `passes` stays false until that run.

### 2026-11-10: a restore refuses backups without SMB tables, and backups hold no smb.conf

- `backup.no-old-backup-import`, at the owner's word to go on ("Continue", in translation),
  under ADR-0207: a restore created the SMB tables in a backup's database that had none, of a
  UNOS before shares were stored in the database, and imported their shares from the archive's
  smb.conf, which every backup carried for that (`include_shares`). Now a database that lacks
  the SMB tables is refused with 400 and nothing changes; a database with the tables but
  without the import marker imports the live smb.conf, never with guest access, as it did when
  an archive had no smb.conf; backups hold no smb.conf, `include_shares` is gone from the API
  and the web UI's type, and an smb.conf in an archive is ignored; `pending_import_conf` is gone
  from the model (ADR-0319, which supersedes ADR-0040, ADR-0077, ADR-0139, ADR-0142 and ADR-0143
  and restates their rules that stay). The import's note is
  "Guest access was not restored", as the guest share now comes from the live file. USER_GUIDE.md
  and CHANGELOG.md say so.
- Tests first (ADR-0186): the refusal of a backup without SMB tables, the live import beside an
  archived smb.conf, BackupConfig without `include_shares`, and the pending sources of the model
  failed before the change; the tests of the archive's import are
  gone or read the live file, and the test databases of this release have the SMB tables. The
  restore, import, model, account and backup file tests passed (587). The entry is host-level
  (the smb.conf that a restore leads to), so `passes` stays false until the device check by hand
  in its notes, with the reboot check (ADR-0018); the feature-pass skill lists it.
- The fresh-context review (ADR-0017) found one blocker and fixed it: scheduled backups still
  copied the host's smb.conf, and the test of a backup without smb.conf could not fail on this
  workstation, which has no `/etc/samba/smb.conf`; the copy is gone, and the tests of manual and
  scheduled backups pretend that the host's file exists and fail when it is read (the scheduled
  one failed before). Also fixed: a test of the tables that a restore created now passed only
  because the test database had them, and is gone; the end-to-end checks of a restore that
  imports, which only the removed test of the archive held (the restored parts, the access rows,
  the marker, the rendered smb.conf, the audit text), are on the test of the live file now; a
  database of the previous layout, with `pending_import_conf` and the source 'backup', restores
  (new test); ADR-0040, ADR-0077 and ADR-0139 stated the removed rules, so ADR-0319 supersedes
  them too and restates what stays, with why the model change needs no `ALTER` (ADR-0022);
  stale comments and the refusal's text for one missing table. The tests of restore, scheduler,
  import, model, accounts, apply, backup files, upload checks and startup passed (888).
- `make check` passed, and `make e2e-local` 669 with 16 skips on listed conditions; `files.spec.ts`
  "uploads files from the keyboard" failed again, this time without a second stand-in, so it is
  the new entry `testing.keyboard-upload-opens-the-file-chooser` (it passed in three of five full
  runs of the day and alone).

### 2026-11-10: the stats message and the web UI's stats code carry nothing unread

- `system.stats-without-unread-parts`, which the review of `system.no-legacy-basic-stats`
  found, at the owner's word to go on ("Continue", in translation), under ADR-0207 and
  ADR-0006: the stats message sent the history of the last two minutes, about 17 of its 25 KB
  every 2 seconds, which no page read; `stats.ts` had a `temperatures` store that nothing set
  and derived stores that nothing read, and `api/system.ts` clients of `/system/stats` and
  `/system/temperatures` that nothing called. They are gone; the endpoints stay. The spec fakes
  of the message have no history, webui/AGENTS.md names the stats store as it is, and
  CHANGELOG.md says so.
- Tests first (ADR-0186): `test_ws_stats.py` checks that the message has no history and the
  rule for the web UI's code: every module of `$lib/api` and `$lib/stores` has a reader, every
  value of the stats store is imported somewhere, and no client asks for `/system/stats` or
  `/system/temperatures`; both failed before the change. Not host-level.
- The fresh-context review (ADR-0017) found no blocker. Fixed: the first test only looked for
  the removed names, so a new unread store would have passed it; it now checks the rule. Also
  gone with it: `webui/src/lib/api/monitor.ts`, a client of the ten `/api/v1/monitor/*`
  endpoints that nothing imported since the move to the socket, `formatBytesPerSecond`, which
  no page calls (webui/AGENTS.md no longer names it), the connection manager of `events.py`,
  whose `broadcast` nothing called, and unused imports and a memory history that no chart drew
  in `MonitorTab.svelte`. Kept and recorded: the fields of the message that the Resource Monitor
  may show; `system.resource-monitor` builds its history in the tab, from the message, so it
  needs no history of the socket. ADR-0006 and ADR-0297 keep their text on the derived stores and
  the formatters as written then. `/api/v1/system/stats` still answers the flat shape and has no
  caller in the web UI; it is the example of `scripts/dev/api.py`, and removing it would be an
  entry of its own. `getPCIeDevices` of `api/system.ts` has no caller either, and is no stats
  code.
- On the stand-in with `E2E_CHROMIUM_PATH`, `make e2e-local` passed (670, 16 skips on listed
  conditions), and so did `make check`: the entry passes.

### 2026-11-10: the apps keep no code for apps of older versions under /mnt/pools/default

- `apps.no-legacy-default-pool`, at the owner's word to go on ("Continue", in translation),
  under ADR-0207 and ADR-0039: the start of an installed app had a 409 of its own for an app
  without a pool label, whose data was in `/mnt/pools/default` on the system disk, and counted a
  catalog pool volume outside the pools base as data; the web UI had a warning and texts for
  such apps. They are gone: data is what lies under the pools base, where an install puts every
  pool volume, and the one 409 for data that is not on a mounted pool names the pool that the
  folder lies on, so a folder on another pool than the install's (a share's folder) names that
  pool, not the install's. The comments of `apps/schemas.py`, `health/router.py` and
  `webui/src/lib/api/apps.ts` no longer speak of older versions, and the fake of `apps.spec.ts`
  is an app whose pool is not mounted.
- Tests first (ADR-0186): `test_the_answer_names_the_pool_that_holds_the_folder` and
  `test_no_code_for_apps_of_older_versions` failed before the change; the tests of an app under
  `/mnt/pools/default` and of an override outside the pools base are gone. `test_apps.py`,
  `test_containers.py` and `test_health_monitor.py` 340 passed; svelte-check and the text check
  passed. `passes` stays false: the entry is host-level (the host's containers), with its device
  check by hand in the notes. ADR-0318 supersedes ADR-0039: the same rules without the handling
  of apps of older versions.
- The fresh-context review (ADR-0017) found one blocker and fixed it: the Russian test of
  `apps.spec.ts` still faked an app of an older version and looked for the removed warning; it
  now fakes an app whose pool is not mounted, and the text check also reads the spec. Also fixed:
  with the pools base set to a path through a symbolic link, the pool volumes, which an install
  gives Docker under the resolved base, no longer counted as data, so an app whose pool was not
  mounted would start; data is now what lies under either form of the base
  (`test_data_under_a_pools_base_reached_through_a_link_is_checked`, which failed on the code
  that the review read); remarks on older installs in `router.py` and `test_disk_safety.py` are
  gone. Recorded in ADR-0318, not changed: a container that UNOS did not install and that binds
  the pools base itself is told to mount the pools base. `test_apps.py`, `test_containers.py`,
  `test_disk_safety.py` and `test_health_monitor.py` 413 passed, and `make check` passed.
- `make e2e-local` with `E2E_CHROMIUM_PATH`: 669 passed and 16 skips on listed conditions, and
  `files.spec.ts` "uploads files from the keyboard" failed once (the file chooser did not open
  within 15 s) while a second stand-in ran on other ports; `files.spec.ts` and `apps.spec.ts`
  alone then passed (65), as that test did in the other full runs of the day. Not changed: if it
  fails again without such a load, it becomes an entry of its own.

### 2026-11-10: the stats message of the WebSocket has one shape

- `system.no-legacy-basic-stats`, at the owner's word to go on ("Continue", in translation),
  under ADR-0207: the stats message of `/ws/v1/events` repeated the CPU, memory and network
  values as flat fields kept for backward compatibility, and sent the usage of the container's
  root as `disk_*`, which no page read. They are gone, with the legacy `SystemStats` type of
  `webui/src/lib/stores/stats.ts`; `temperatures` stays, the only copy of the sensors. The
  dashboard reads `cpu.percent` and `memory.percent`, `used` and `total`, as the Monitor tab
  does, and the fake message of `dashboard.spec.ts` has the current shape.
- Test first (ADR-0186): `test_ws_stats.py` checks the keys of the message and of the objects
  that the web UI reads, and failed before the change. `test_ws_stats.py`, `test_ws_auth.py` and
  `test_system.py` 58 passed; svelte-check found no error. On the stand-in, with
  `E2E_CHROMIUM_PATH`, the specs that use the stats socket passed (165 tests), and so did
  `make e2e-local` (670 passed, 16 skips on listed conditions). Not host-level.
- The fresh-context review (ADR-0017) found no blocker. Fixed: CHANGELOG.md says that the flat
  fields are gone, as a script that read them over an API token breaks; the test pins the
  `memory` and `network` objects too; a comment of `stats.ts` still spoke of extended types.
  Found beside the change and taken into the new entry `system.stats-without-unread-parts`:
  the message's history, about 17 of its 25 KB every 2 seconds, which no page reads, the HTTP
  stats client of `webui/src/lib/api/system.ts` without a caller, and stores of `stats.ts` that
  nothing sets or reads.

### 2026-11-10: the skip detector sees every form, and testing.no-compat-skips passes

- At the owner's word to go on ("Continue", in translation), after the review of 2026-11-09 had
  stopped the pass: `test_standin.py`'s detector of skips matched only `test.skip` and its
  `test.describe` and `testInfo` forms in the top folder's `.ts` files. It now sees a skip, fixme
  or fail call on any object (`test.step`, a TestInfo under any name, `test.info()`) in every file
  of `webui/tests` and its folders, of every extension Playwright loads; the fake of
  `logs.spec.ts` names its method `failWith`, so that no helper looks like a skip.
- Test first (ADR-0186): `test_every_skip_form_in_every_spec_file_is_seen` failed on the old
  detector, which found one of its five skips. With the new one no real spec has a skip on an
  unlisted condition (`test_standin.py` 51 passed); `logs.spec.ts` on the stand-in, 19 passed.
- `testing.no-compat-skips` passes: its step 1 since 2e4306d, its step 2 as ADR-0317 rewords it.
  The review's skips that can still pass over a broken API became the entry
  `testing.no-skips-that-hide-api-errors`. Not host-level.
- A second fresh-context review found that step 2 as written was not met (eight of the nine
  listed conditions are properties of the target, not missing stand-in features), forms that
  the detector still missed (a skip after a `//` in a string, `?.`, `['skip']`, `.only`, aliases,
  annotations) and that a `test.fail` on the stand-in's condition passed. Fixed: ADR-0317 rewords
  the entry to what the check holds; comments are blanked with strings and regular expression
  literals kept, and the detector finds those forms and refuses every `test.fail`
  (`test_an_expected_failure_is_refused_on_any_condition`); the new entry has the review's
  further checks.
- A third fresh-context review found step 2's "in any form" still not met: a `{ skip }`
  destructured from `test`, a template literal or `?.` in brackets, a parenthesized `(test)` and a
  template literal annotation passed unseen. It also found that ADR-0317 called the listed
  conditions permanent while `testing.no-skips-that-hide-api-errors` changes one, and, as nits, a
  division or a regular expression literal that the heuristic misread and that hid the skip after
  it, two quadratic paths on long words and chains, a stale count in the notes and a `test.fixme`
  that passed on a listed condition. Fixed: `test_standin.py` reads the files with TypeScript's
  parser (`SKIP_SCAN`, run by node with `webui/node_modules/typescript`), which finds a skip,
  fixme or fail called or referenced on any object, destructured or as an annotation, and fails
  on a file that does not parse; the regex detector and its tokenizer are gone, and with them the
  misreads and the quadratic paths; every `test.fixme` is refused like `test.fail`; ADR-0317 and
  step 2 say what the check holds and what it does not see (a name that the code computes or
  passes as a value), and the notes give the counts of the new test.
- Tests first (ADR-0186): `test_every_skip_form_in_every_spec_file_is_seen`, now with 22 skips in
  four files, failed on the regex detector (it stopped with an error at the regular expression
  literal after `if (ok)`, and without that line missed the new forms and the skip after a
  division), and so did the fixme case of `test_an_expected_failure_is_refused_on_any_condition`
  and `test_a_file_that_typescript_cannot_parse_fails_the_check`. In the real specs the parser
  finds the same 28 skips, all on listed conditions, in 0.3 s; `test_standin.py` 53 passed.
- A fourth fresh-context review found step 2 still not met as written: a cast around a key
  (`test['skip' as const]`, `'fixme'!`, `<const>'skip'`, `satisfies`), an import alias
  (`import s = test.skip`), a class field `type = 'skip'` as an annotation, and
  `expectedStatus = 'failed'`, which marks an expected failure as `test.fail` does. Fixed: the
  scan no longer looks for forms one by one; it reports every use of the names skip, fixme, fail
  and expectedStatus that a file writes out, a member, a destructured name, a key
  `expectedStatus`, an import alias or any other string of a name alone, and the check refuses
  all of them but a skip on a listed condition (ADR-0317 and step 2 say so). Also fixed from its
  nits: the reason check takes a folder and has a test of its own
  (`test_a_skip_without_its_reason_is_refused`), the parse test feeds a file whose readable skip
  is on a listed condition, and the notes say that the skip on `E2E_OLD_SUPERVISOR` was a manual
  run. Recorded in ADR-0317: conditions are compared as text, a comment in a condition or a reason
  makes the check refuse more, and the Unit Tests job of the CI workflow (off, ADR-0230) installs
  no web UI packages, so the checks would skip there. The forms test holds 36 uses in five files,
  which the scan before failed on; `test_standin.py` 54 passed.
- A fifth fresh-context review found that the scan still took a set of forms: a key, a method,
  an enum member, a class field or a declaration named after one of the names (`{ fixme: 0 }`
  read by a `for ... in`, `class { expectedStatus = 'failed' }`, `enum Mark { expectedStatus }`
  passed to `Object.assign`), and JSX, passed unseen, and that `test.describe.skip(!httpUrl, body)`
  read as a skip on a listed condition, though it takes a title. Fixed: the scan reports every
  token whose text is one of the names, an identifier, a string, a template or a part of one, and the check
  refuses all of them but a skip on a listed condition; a skip of `test.describe` or `test.step`
  gives `(a title)` and is refused; a file with JSX fails the check
  (`test_jsx_in_a_spec_fails_the_check`); the variable `fail` of `health.spec.ts` is
  `listingFails`. ADR-0317 and step 2 say so, and that modules outside `webui/tests` are not
  read. The forms test holds 53 uses in six files; `test_standin.py` 55 passed, `health.spec.ts`
  on the stand-in 14 passed (with `E2E_CHROMIUM_PATH`), and `make check` passed.

### 2026-11-10: install.sh recognizes the smb.conf of UNOS by its header alone

- `deploy.no-legacy-smb-conf-recognition`, at the owner's word to go on ("Continue", in
  translation), under ADR-0207: `configure_samba` of `deploy/install.sh` kept any smb.conf with
  `server string = UNOS NAS`, for installations whose shares were not imported into the database
  yet. It now keeps only a file whose first two lines are the header that the supervisor writes
  (ADR-0074), as the supervisor's own check does; any other file, one with only that server
  string included, is replaced, and kept as `smb.conf.original` only when there is none yet
  (ADR-0316). The text it writes is the function `unos_smb_conf`, which the check compares with.
  CHANGELOG.md says so; the server string stays in the `[global]` of ADR-0281 but marks nothing.
- Tests first (ADR-0186): a re-run over a file with the header and without the server string
  keeps it; a file with only the server string is replaced; these failed before the change. A
  file with a part of the header, the header below its first line, indented, after a byte order
  mark or with CRLF line ends is replaced too, and a re-run keeps no copy of a file it replaces
  when `smb.conf.original` exists. 182 tests of `test_deploy_scripts.py` passed. The entry is
  host-level (GLOSSARY.md: Samba's smb.conf on the host), so `passes` stays false until a run of
  `install.sh` on a host with the reboot check (ADR-0018), like `deploy.install-script`; the
  feature-pass skill lists it among the checks by hand, and the notes give the steps.
- The fresh-context review (ADR-0017) found no blocker. Fixed: the texts said that a replaced
  file is always kept as `smb.conf.original`, but after the first run that file exists and a
  re-run keeps no copy (now said, and tested); a comment of `test_smbconf.py` still named the old
  recognition; the check read a prefix of the first line, looser than the supervisor's, so it
  kept a file cut off in its header, and no test pinned that it must be the first line: it now
  compares the whole header, and the tests cover those cases. Recorded in ADR-0316, not changed:
  a file of UNOS whose first lines someone changed is replaced without a copy, where the
  supervisor would have kept its hand-made sections (ADR-0092). ADR-0281 keeps its remark on the
  server string, which ADR-0316 says no longer holds, as ADR-0281's decision of the values stands.
  `make check` passed.

### 2026-11-10: the certificate code keeps no fix-ups for files of older versions

- `security.no-legacy-certificate-fixups`, at the owner's word to go on ("Continue", in
  translation), under ADR-0207: `_write_private_key` no longer tightens an existing key file, the
  self-signed generation no longer chmods one before openssl, and the delete no longer takes any
  id inside `CERTS_DIR` for names that older versions stored: it takes only the names that an
  upload and a self-signed certificate store.
- Tests first (ADR-0186): the delete of `My NAS`, `*`, `a b.local` and `-x.local` answers 400
  and keeps the file, and the certificate code has no chmod; both failed before the change. The
  tests of 0644 keys and of the name `My NAS` are gone.
- The fresh-context review (ADR-0017) found no blocker and three things that are fixed: the
  removed tests were the only ones of a key file that a new upload replaces whole
  (`test_a_new_upload_replaces_the_whole_key_file`); the self-signed key was 0600 only because
  OpenSSL creates its key files so (LibreSSL's are 0644), and openssl wrote the new key over the
  stored one before it refused the subject, so openssl now prints the key and
  `_write_private_key` stores it after openssl succeeded; the listing showed each chain file
  `<name>.chain.crt` as a certificate, whose id the delete now refused for some names, so the
  chain is stored as `<name>.chain.pem` and the listing shows only the names that the delete
  takes (`test_every_listed_certificate_can_be_deleted`). Both new tests failed on the code
  before. Recorded, not changed: an IP address id in another form (`custom_0:0::1`) answers 400
  instead of 404, which the web UI never sends; older gaps of the certificate code (untested
  error paths, the unchecked organization and country, openssl on the event loop, symlinks
  followed) went into the notes of `security.custom-certificates`. `test_security.py` 119
  passed.
- `make e2e-local` on main f3347fb, which holds the change, passed on the stand-in with
  `E2E_CHROMIUM_PATH`, the cached Chromium that Now names (670 passed, 16 skips on listed
  conditions), so `passes` is true; the first record of this entry said wrongly that Chromium
  could not start. Not host-level.

### 2026-11-10: install.sh and update.sh start UNOS without a compose down first

- `deploy.no-legacy-network-recreate`, at the owner's word to go on ("Continue", in translation),
  under ADR-0207: `start_unos` of `deploy/install.sh` and `restart_services` of
  `deploy/update.sh` ran `docker compose down --remove-orphans` before `up`, kept for the network
  of releases without the fixed subnet (ADR-0048). Both start with `up --remove-orphans` alone
  now, which recreates the containers of new images and removes those of services that a release
  dropped or renamed, as the old `down --remove-orphans` did; their comments on older networks are
  gone, and `docs/INSTALL.md`, README.md and CHANGELOG.md describe the restart and the upgrade by
  hand so. A change of the subnet still needs a `docker compose down` by hand, as that guide says;
  the rollback of `update.sh` keeps its own.
- Tests first (ADR-0186): an update makes no `compose down` call, and `start_unos` makes none
  either, nor does either script mention the fixed subnet; both failed before the change. The
  docker stub of `test_deploy_scripts.py` interrupts the restart during its `up` now
  (`interrupt-at-up`), where it interrupted at the old `down`. 173 tests of the file passed. The
  entry is host-level (GLOSSARY.md: the host's containers), so `passes` stays false until a run of
  both scripts on a host with the reboot check (ADR-0018), like `deploy.install-script` and
  `deploy.update-script`; the feature-pass skill lists it among the checks by hand.
- The fresh-context review (ADR-0017) found seven things, all fixed: CHANGELOG.md still described
  a `down` before `up`; without `--remove-orphans`, the containers of a service that a release
  dropped or renamed kept running (a renamed `webui` would keep ports 80 and 443 and its address,
  and every update to that release would roll back), so both starts pass it now and the tests
  assert the exact `up`; `passes` was true with a reason that GLOSSARY.md does not give, and the
  Log entry had no results; the `on_interrupt` comment of `update.sh`, the guide's text on an
  interrupted update and the upgrades by hand of `docs/INSTALL.md` and README.md still named the
  old `down`; the stub's `interrupt_at down` was dead code. `make check` passed.

### 2026-11-09: storage.scrub passes on the scenario check scrub, before and after a reboot

- At the owner's word to work on through the features ("I allow everything", in translation),
  main eceead5 was deployed to the dev device at 21:07 UTC (39.270 MiB) and
  `scripts/dev/scenario.sh all`, with the area `scrub` of ADR-0314, ran after the deploy and
  after a reboot from the API at 21:12 UTC (the boot of 21:14:01 UTC), with the SD MiB of each
  step (Dev device). All seven areas passed both times and left the device as they found it, but
  for the scrub check's history: the pool's last scrub result and two runs in the Scheduler
  (ADR-0314).
- `storage.scrub` passes again, on what its notes record: the start's answer and the status right
  after it (ADR-0313), a scrub to its end with its progress, a cancel, and a scheduled scrub that
  starts, and one of a folder with nothing mounted on it that fails with the 409's text (0.152 and
  0.148 MiB), by which `storage.scrub-needs-mounted-pool` holds too. The Storage page itself is
  the E2E specs' (ADR-0215).
- Not checked on the device: whether the regular account `test` still signs in after ADR-0315
  (Open owner actions).
- A fresh-context review found every number and time in line with the logs and nothing that
  blocks the pass. Fixed from it: the owner action named a deploy that has happened, the notes
  of `storage.scrub` now record the scheduled scrub of a folder with nothing mounted and that the
  page was not looked at on the device, the step's MiB in the order of the runs, the history that
  the scrub check leaves, why the last scrub read idle (the smbconf check restarts the supervisor
  before it), and Now and Next name the pass.

### 2026-11-09: the stub checks of the SMB models' tests are gone

- `testing.remove-dead-stub-checks`, at the owner's word to work on through the features ("I
  allow everything", in translation), under ADR-0186 and ADR-0207: `test_smb_models.py` lost the
  test that skipped for each of the 13 functions whose NotImplementedError body real code had
  replaced, and its table, now `SIGNATURES`, lost the work packages' labels; `TestSignatures`
  checks the 13 signatures as before. `make test TESTS=tests/test_smb_models.py`: 174 passed and
  none skipped, where 13 skipped before. Not host-level.
- A fresh-context review found nothing that blocks; from its nit, the table is `SIGNATURES`, as
  `TestInterfaces` of the same file tests the types of the module `interfaces`. `make check`
  passed on the tree merged with main eceead5 (6066 backend tests, 108 skipped, 13 fewer than
  before, 1 xfailed; svelte-check without errors, the build).

### 2026-11-09: passwords are checked as given (ADR-0315)

- `auth.no-legacy-password-truncation`, at the owner's word to work on through the features ("I
  allow everything", in translation), under ADR-0207: `verify_password` no longer cuts a password
  to bcrypt's 72 bytes, which ADR-0026 kept for the hashes of the passlib code before Phase 0, so
  that a password of 72 bytes no longer signs in with anything appended to it. A password longer
  than 72 bytes matches no hash and fails like a wrong password, also at the sign-in (401). On
  the dev device the administrator's password was set through the API after the 72-byte limit;
  its regular account `test` kept its password of Phase 0 (ADR-0226), so whether it still signs
  in after the deploy is for the owner to check (Open owner actions). ADR-0315 supersedes
  ADR-0026 and restates the rest of it; the comments of `auth/schemas.py` and the changelog say
  so.
- Tests first (ADR-0186): `test_a_password_longer_than_72_bytes_matches_no_hash` (ASCII and two-byte
  characters) and `test_a_sign_in_with_a_password_longer_than_72_bytes_fails_like_a_wrong_one`
  failed on the old code, where the longer password verified and signed in. The tests of the
  passlib hashes of longer passwords are gone; a passlib hash of a shorter password still
  verifies. Not host-level.
- A fresh-context review found nothing that blocks; fixed from it: the ADR's premise about the
  device's passwords (the account `test` kept its own) and its citation of the sign-in's limits
  (ADR-0065, not ADR-0062). Not done: API tests of a change of one's own password and of turning
  one's SMB on with a current password longer than 72 bytes; both check it through
  `verify_password` before anything else and answer 400 like a wrong one.
- `make check` passed on the tree merged with main f3d20be (6066 backend tests, 121 skipped, 1
  xfailed; svelte-check without errors, the build), before the review's edits of the ADR.

### 2026-11-09: the scrub steps checked by hand become the scenario check scrub (ADR-0314)

- At the owner's word to work on through the features ("I allow everything", in translation),
  and as the review of that day's device record asked: `scripts/dev/scenario.sh scrub` runs the
  steps of `storage.scrub` and `storage.scrub-needs-mounted-pool` that a script of the session had
  checked by hand. Its first step names the pool's last scrub; a folder
  `<base>/unoscheck<6 hex digits>` with nothing mounted on it gets 409, and a scheduled scrub of
  that folder fails with the same text (ADR-0311); Start Scrub answers running, and so does the
  status right after it (ADR-0313); the scrub ends finished without errors, its bytes scrubbed
  growing on the way; a second scrub is cancelled once btrfs has recorded it and ends aborted; a
  disabled scheduled scrub task's Run now says "Scrub started", and its scrub ends finished; the
  task is deleted, its run stays in the Scheduler's history. On a pool with more than 32 GiB of
  data the scrubs are cancelled, each once btrfs has recorded it, instead of waited for.
  `REMOTE_POOL_FOLDER` makes and removes the folder on the root filesystem, only for a name of a
  check, and removes only an empty folder that is no mount point.
- The feature-pass skill's table names the new check for both entries, which leave the row of
  checks by hand; AGENTS.md, `deploy/AGENTS.md`, `docs/TESTING.md`, GLOSSARY.md and the
  device-checks skill name the area, the Scheduler task among what the checks make, and how to
  remove a leftover task or folder.
- `supervisor/tests/test_dev_scenarios.py`: the fake NAS records a scrub a few requests after its
  start, leaves no record for a cancel before that, and has the Scheduler's tasks and runs; the
  check passes on it, every call that fails in turn leaves the fake as it was, and the check fails
  on the defects it exists for: no mount check, Start Scrub answering idle, a status that reads
  idle before btrfs records the scrub, errors found, a cancel that leaves the scrub running, and
  runs deleted with their task. Without the wait for btrfs's record, the large pool's last cancel
  fails on the fake. The helper is tested against a temporary base.
- A fresh-context review found nothing that blocks. Fixed from it: the large pool's last cancel
  waited for no record, the leftover recipe lacked the folder and the task, the table did not say
  what the check leaves out of `storage.scrub`, the start's status accepted "finished", the
  listing of the base ran outside the read-only namespace, and the step that claimed one helper
  showed none (the scheduled scrub of the folder shows it now); the progress and the last scrub
  are now steps' facts; the ADR names the clean-up's cancel of another scrub, the smallest pool,
  the rate that the wait needs and the Scheduler's last 100 runs.
- `make check` passed on the tree merged with main 0b29a40 (6065 backend tests, 121 skipped, 1
  xfailed; svelte-check without errors, the build). The check runs on the device with the next
  deploy.

### 2026-11-09: the logs API has one entry endpoint for every source

- `system.no-legacy-log-endpoints`, at the owner's word to work on through the features ("I allow
  everything", in translation), under ADR-0207 and as ADR-0257 foresaw: `GET /api/v1/logs/dmesg`
  and `/logs/journal` are gone from `supervisor/src/logs/router.py`; `GET /logs/entries` with
  `source=dmesg` or `source=journal` serves what they did, and no page called them.
- Tests first (ADR-0186): `TestOneEntryEndpoint` of `test_logs_sources.py` replaces the tests of
  the old routes; its 404 test failed while they answered 200. The changelog says so. Not
  host-level.
- A fresh-context review found nothing that blocks; fixed from it: the tests' helper that only the
  old routes used is gone, and the notes of `system.logs-viewer` no longer name their test class.
- `make check` passed (6043 backend tests, 121 skipped, 1 xfailed; svelte-check without errors,
  the build), before the review's two edits of a test and a note, which `test_logs_sources.py`
  and `make check-fast` cover.

### 2026-11-09: the unused mock layer of the tests is gone

- `testing.remove-dead-mock-layer`, at the owner's word to work on through the features ("I
  allow everything", in translation), under ADR-0207: `supervisor/tests/mocks` (one file,
  `mock_data.py`, which nothing imported) is deleted, with its copy in
  `supervisor/Dockerfile.test` and the pytest flag `--ignore=tests/mocks` of the Makefile's test
  target and of the device's test runner (`scripts/dev/tree.py`); `UNOS_TEST_MODE`, which no code
  read, left `deploy/docker-compose.test.yml`, whose header now says that host commands run in
  the supervisor's container. `docs/TESTING.md` had described neither.
- Tests first (ADR-0186): `test_the_test_stack_has_no_mock_layer` of `test_deploy_scripts.py`
  and the test target's command in `test_repo_tooling.py` failed before the removal; the check
  that the build context keeps what the test images copy now looks for `tests/fixtures`, the one
  folder of `tests/` that `Dockerfile.test` still copies. Not host-level.
- A fresh-context review found nothing that blocks; fixed from it: `mocks` left the
  `norecursedirs` of `supervisor/pytest.ini`, the test also asserts that `docs/TESTING.md` and
  `pytest.ini` name neither, and the compose file's header says which data is test data.
- `make check` passed: 6037 backend tests, 121 skipped, 1 xfailed; svelte-check without errors, the
  build. Its first run, without `webui/node_modules` in the worktree, failed in its frontend part
  and skipped the 87 backend tests that need them.

### 2026-11-09: a scrub runs from its start, before btrfs records it (ADR-0313)

- The device check of `storage.scrub-needs-mounted-pool` met it again: for about 6 s after a
  start the API answered the scrub as idle, which the notes of `storage.scrub` had seen on
  2026-11-08 ("idle for a moment"). Measured on the dev device: Start Scrub's answer and the
  requests after it said idle for 5.9 s, until the process that runs the scrub first reported its
  progress (btrfs-progs does that every 5 s); `btrfs scrub status` in the supervisor's container
  printed "no stats available" 0.8 and 3.4 s after a start, followed by the "Time left" and "ETA"
  lines that it prints only for a scrub that runs. The Storage page follows a scrub only while an
  answer says running, so it stopped following a scrub that ran, and a second Start Scrub failed
  with "Scrub is already running".
- `get_scrub_status` answers a scrub whose output has those lines as running, also below "no stats
  available", and `start_scrub` answers running when the status read right after btrfs accepted
  the start says idle (ADR-0313, which superseded ADR-0272 and restates its rules). Tests first
  (ADR-0186): the status of a scrub that btrfs has not recorded yet, with the device's output, and
  `test_start_answers_running_before_btrfs_reports_the_scrub` failed on the old code. The page
  needs no change: it shows a running scrub without a bar until btrfs reports bytes scrubbed. A
  first version kept the time of each start in memory and answered "no stats available" as
  running for 15 s after it; btrfs's own lines made that unneeded, also for a scrub started
  outside UNOS.
- Two fresh-context reviews: the first, of the version with the times in memory, was stopped
  when btrfs's own lines were found. The second found nothing that blocks; fixed from it: the
  copy of a disk replace, which the kernel runs as a scrub of the source disk, shows as no scrub
  when no record shows it (`btrfs replace status -1`: Cancel Scrub would cancel the replace), a
  cancel that btrfs did answers aborted when the status then says idle, and the docs of the
  endpoints, the schema and the web UI's type. Recorded, not fixed: a replace while the container
  keeps the record of an earlier scrub still shows as a running scrub (ADR-0313); UNOS starts no
  replace. The replace's output is btrfs-progs' format, not seen on the dev device, which has no
  spare disk. `storage.scrub` passes again only after the fix's device check before and after a
  reboot.
- `make check` passed on the tree merged with main 5b55acd (6040 backend tests, 121 skipped, 1
  xfailed; svelte-check without errors, the build), and `webui/tests/storage.spec.ts` on the
  stand-in (41 passed).

### 2026-11-09: two host-level entries pass on the device checks of main 31ac73b

- At the owner's word to work on through the features ("I allow everything", in translation),
  main 31ac73b was deployed to the dev device at 18:25 UTC; `scripts/dev/scenario.sh all` and
  the scrub steps checked by hand ran after the deploy and again after a reboot from the API at
  18:41 UTC (the boot of 18:42:31 UTC), with the SD MiB of each step (Dev device). The scenario
  checks passed both times and left the device as they found it.
- `shares.samba-runtime-writes` passes: the smb check's step 3 read no lockout and a password
  history of 3, and its steps 19 (the replaced password) and 21 (a password the account never
  had) left passdb.tdb's time of change as it was, at 0.016 and 0.000 MiB, before and after the
  reboot (ADR-0310). Setting a previous password again through UNOS, which its notes still
  asked for, leaves the device check: smbpasswd as root does it without the history's check
  (ADR-0278), and neither the entry's steps nor the reviewed smb check name it.
- `storage.scrub-needs-mounted-pool` passes on the steps checked by hand (ADR-0311): POST
  .../scrub for a temporary folder /mnt/pools/unoscheck<hex> with nothing mounted on it answered
  409 with the text of ADR-0311, and the folder went again (0.078 MiB); Start Scrub of data and
  Run now of a disabled scheduled scrub task of data ("Scrub started") each started a scrub that
  btrfs reported running and that ran to its end after 18 to 19 s, Start Scrub's with 0 errors
  (0.043 and 0.113 MiB before the reboot, 0.035 and 0.125 after it); at 19:06 UTC no task of
  the check was left, and the runs of its four tasks stay in the Scheduler's history (Dev
  device). The same runs read GET /system/info: boot_time and no uptime (`system.boot-time-field`).
  These steps are a script of this session; they become an area of `scripts/dev/scenario.py`
  (Next).
- The scrub steps met again what the notes of `storage.scrub` had seen on 2026-11-08 ("idle for
  a moment"): Start Scrub answered idle each time, so the Storage page stops following a scrub
  that runs, and a second Start Scrub fails with btrfs's "Scrub is already running". The first
  run's Start Scrub step passed without seeing a scrub, and in the first two runs the start of
  the scheduled scrub was refused that way; the third waited until btrfs reported the scrub, and
  passed. Measured afterwards: the API answered idle for 5.9 s after a start (18:52 UTC, 0.031
  MiB), and `btrfs scrub status` in the supervisor's container printed "no stats available" 0.8
  and 3.4 s after a start (19:06 UTC). Step 2 of `storage.scrub` fails on the device, so that
  entry no longer passes (the feature-pass skill's gotchas) until a fix and its device check; the
  fix is a change of its own.
- A fresh-context review found every number and time in line with the logs and nothing that
  blocks the two entries. Fixed from it: ADR-0312 was named before main was merged into the
  branch; `storage.scrub` goes back to passes false, with the defect in its own notes; the claims
  that the logs did not show (the delete of the check's task, btrfs's own output, no errors for
  the Run now's scrub) were checked on the device or dropped; the Scheduler's runs that the check
  leaves; the scrub steps as an area of `scripts/dev/scenario.py`; the step of
  `shares.samba-runtime-writes` that never ran; and wording.
- The record changes only PROGRESS.md and the ledger: `make check-fast` passed with it.

### 2026-11-09: a WebSocket's re-check in progress runs to its end when the socket closes

- `make check` of the smb check's comparison of passdb.tdb, merged with main a718ba9, hung
  for more than two hours at the end of `test_rechecks_of_an_api_token_do_not_commit_its_last_use_again`
  of `supervisor/tests/test_ws_auth.py`. The Python stacks (an abort of the hung pytest, which
  pytest's faulthandler dumps) showed the TestClient's event loop in its shutdown, waiting for a
  task left over. That test alone, repeated in a loop with a dump of the asyncio tasks, hung after
  106 and after 21 runs, both times on SQLAlchemy's close of a discarded aiosqlite connection,
  waiting in aiosqlite's `close()` for a worker thread that had ended.
- The cause on UNOS's side: a socket that closes cancelled its credential watch (ADR-0031,
  `_finish`) also in the middle of a re-check's database query; SQLAlchemy then discards the
  connection and closes it in a task of its own, the event loop's shutdown cancels that close
  too, and aiosqlite 0.22.1 then waits for ever. `_watch_credential` of
  `supervisor/src/websocket/events.py` now waits for each check shielded, so a check in progress
  runs to its end and its answer is dropped (ADR-0312), for the events and the logs socket.
- Test first (ADR-0186): `test_a_socket_that_closes_lets_its_recheck_in_progress_run_to_its_end`
  holds a re-check in progress, closes the socket and lets the check go on once the watch has
  ended; on the old code the check ended with the cancellation. With the fix the whole of
  `test_ws_auth.py` passed, and the test that hung and the new one, 500 times each in one run,
  passed without a hang (1000 passed in 267 s). Not host-level.
- A fresh-context review found nothing that blocks: it made the hang outside the test suite
  with a slow query cancelled in its middle and then the event loop's shutdown (three hangs in
  three runs of the old shape, none in 120 runs of the new one). Fixed from it: ADR-0312 names
  the order of the two stops that makes the hang come only sometimes, uvicorn's shutdown, the
  longest a check runs on after its socket closed (up to the busy timeout of 15 s when its
  last-use commit waits, ADR-0252), and the handshake's query as what a test must not leave a
  socket in; a second test, `test_a_recheck_that_fails_after_its_socket_closed_is_dropped_quietly`,
  failed with asyncio's "Task exception was never retrieved" without `_consume_result`.
- `make check` passed on the tree merged with main 31ac73b (6035 backend tests, 121 skipped, 1
  xfailed; svelte-check without errors, the build), before the review's second test and ADR
  wording, which `test_ws_auth.py` (42 passed) and `make check-fast` cover.

### 2026-11-09: the smb check compares passdb.tdb at a wrong password

- For `shares.samba-runtime-writes`, at the owner's word to work on through the features ("I
  allow everything", in translation): the smb scenario check's step with a password the account
  never had compares passdb.tdb's modification time before and after it, and its first step reads
  Samba's "bad lockout attempt" beside the password history and expects 0, as UNOS leaves Samba
  (ADR-0280): a failed sign-in writes nothing to passdb.tdb while no lockout counts failures
  (ADR-0310). The feature-pass skill's table no longer leaves this out of the smb check.
- `supervisor/tests/test_dev_scenarios.py`: the stand-in's Samba writes passdb.tdb at a wrong
  password only while a lockout counts failures (and then not for the two previous passwords);
  new tests failed on the old check: a write for a password the account never had, and a lockout
  policy, each fail it now; `scenario.Device.passdb_entries` reads the lockout policy.
- The policy is read at the check's start, before anything is made: with a lockout the check's
  own wrong passwords would count against its temporary account, so the steps after them would
  not show what they claim. A device with a lockout stops the smb check there.
- The entry passes once this check has run on the device before and after a reboot; setting a
  previous password again through UNOS stays unchecked on the device, as its notes say.
- A fresh-context review found nothing that blocks. Fixed from it: `passdb_time` requires a
  timestamp, so an unreadable passdb.tdb fails the step instead of comparing two empty values;
  the device-checks skill, Now and the test's docstring name the compared step; the defects'
  failure messages, the steps' numbers, and the reason the lockout is read at the first step.
- `make check` passed on the tree merged with main a718ba9 (6034 backend tests, 121 skipped, 1
  xfailed; svelte-check without errors, the build). Its first run hung in
  `supervisor/tests/test_ws_auth.py`, which this change does not touch; a change of its own
  fixes that hang.

### 2026-11-09: GET /system/info names the host's boot time boot_time

- `system.boot-time-field`, at the owner's word to work on through the features ("I allow
  everything", in translation). The field `uptime` of GET /api/v1/system/info held
  `psutil.boot_time()`, the boot time in seconds since the epoch, which an API client would read
  as a duration; the pages computed the uptime from it. It is `boot_time` now, with no field
  `uptime` left (ADR-0207), and `webui/src/lib/api/system.ts`, the dashboard and System > General
  read it.
- Tests first (ADR-0186): `test_system.py` (the answer carries boot_time, close to
  `psutil.boot_time()`, and no uptime) failed on the old code; `webui/tests/api.spec.ts` asserts a
  boot_time in seconds since the epoch and no uptime against the stand-in, and failed with an
  uptime field put back; the fake system info
  of `mobile-layout.spec.ts` has the new field. The dashboard's and System > General's uptime are
  asserted by `dashboard.spec.ts` and `updates.spec.ts`, which passed with `api.spec.ts` on the
  stand-in (69 passed, 15 skipped for what the stand-in lacks). Not host-level.
  `CHANGELOG.md` names the rename. `supervisor/tests/mocks/mock_data.py` keeps an `uptime` of its
  own: nothing imports it, and `testing.remove-dead-mock-layer` removes that layer.
- A fresh-context review found one blocker, fixed: the ledger's notes quoted a Russian text,
  where the repository's text is English (ADR-0200). Fixed from it as well: the notes' "today"
  sentence, a lower bound in `api.spec.ts` so that a duration cannot pass for a time, and the
  changelog's line. `make e2e-local` passed (670 passed, 16 skipped; 11.7 minutes).
- `make check` passed on the tree merged with main cfed052 (6031 backend tests, 121 skipped, 1
  xfailed; svelte-check without errors, the build).

### 2026-11-09: a scrub of a pool that is not mounted answers 409, through one helper

- `storage.scrub-needs-mounted-pool`, at the owner's word to work on through the features ("I
  allow everything", in translation). The Storage page's Start Scrub ran `btrfs scrub start` on
  `<pools base>/<pool>` whether or not the pool was mounted there; not mounted, that path is a
  folder on the root filesystem, and btrfs would scrub that filesystem if it is btrfs. The
  Scheduler's scrub tasks checked the mount, in a helper of their own, and only whether anything
  was mounted at the path. Now `scrub_start` of `supervisor/src/storage/pools.py` is the one start
  of a scrub (ADR-0311, which supersedes ADR-0071 and closes its open item): it asks the host's
  mount table for a btrfs filesystem mounted at the pool's path, answers 409 without any btrfs
  otherwise ("Pool data is not mounted at ..."), and starts btrfs killed at its timeout
  (ADR-0290). The Storage page's start and the Scheduler's scrub tasks both call it.
- Tests first (ADR-0186): `test_storage.py` (TestScrubNeedsAMountedPool: the 409 and no btrfs
  with another pool mounted, and what counts as mounted on the fake host's mount table) and
  `test_scheduler_validation.py` (both starts go through `scrub_start`) failed on the old code;
  a check that took a mount at the parent or of another type failed them too. The tests of the
  scrub endpoints and of the scheduled scrubs count their test pools, plain folders, as mounted.
- A host-level change (GLOSSARY.md): the entry keeps `passes: false` until the device check and
  the re-check after a reboot (ADR-0018), with the next deploy.
- Found, and not part of this change: the status and the cancel of a scrub (GET and DELETE of the
  same path) still run btrfs on an unmounted pool's folder (ADR-0311, Consequences).
- A fresh-context review found nothing that blocks; fixed from it: no ADR recorded the decision
  (ADR-0311 does, superseding ADR-0071, whose open item this closes); the check asked only
  whether anything was mounted, in the container, so another filesystem mounted by hand or an
  unmounted btrfs subvolume passed it (it asks the host's mount table for btrfs now); the planned
  device check could not see "no btrfs run" and tested only a missing folder (it uses a folder
  with nothing mounted, and the 409's text); the notes claimed too much; `test_command_timeouts.py`
  left `verified_by`. Left: the Scheduler's snapshots keep their own mount check in the container.
- `make check` passed (6030 backend tests, 121 skipped, 1 xfailed; svelte-check without errors,
  the build).

### 2026-11-09: an SMB entry passes on the scenario checks, and passdb.tdb's writes measured

- At the owner's word to work on through the features ("I allow everything", in translation),
  `scripts/dev/scenario.sh all` ran at 13:58 UTC after the reboot of 12:22 UTC, with the runtime
  files of main 12483dd: every step of the six areas passed (78), 6.994 MiB. With the run of
  07:43 UTC, before that reboot and after the deploy of 12483dd, it is the device check and the
  re-check after a reboot (ADR-0018) of the entries that the scenario checks cover.
- `files.group-writable-entries-in-shares` passes: its open steps, an upload's 0660 in a share's
  folder and an SMB user's change there, are the smb check's steps 10 and 11, and its step 3 the
  files check's steps 3 and 4 (755 and 644 root:root outside share folders); the feature-pass
  skill's table names the smb check for it, and it had not been passed after these runs.
- `shares.samba-runtime-writes` stays open. Its step 2, that a failed sign-in writes nothing to
  passdb.tdb, looked out of reach: ADR-0159 and ADR-0278 took every wrong password but an
  account's two previous ones to write passdb.tdb once. The smb check's step 21 showed 0.000 MiB
  for a password the account never had, and a measurement at 14:24 UTC with a temporary account
  (bad lockout attempt 0, password history 3; 1.496 MiB in all) found that four sign-ins with
  passwords it never had and one with its replaced password each left passdb.tdb's modification
  time as it was, at 0.000 to 0.016 MiB each; the account was deleted again. ADR-0310 records it
  and supersedes ADR-0159: passdb.tdb stays on the system disk, as nothing writes it while no
  lockout counts failures; `docs/USER_GUIDE.md` no longer says that wrong passwords are written to
  the system disk. But step 21 does not look at passdb.tdb, so its pass at 07:43 UTC, before the
  reboot, shows nothing of step 2, and the measurement came after the reboot only: the entry
  passes once the smb check compares passdb.tdb's modification time at its wrong password and
  reads the lockout policy, before and after a reboot. `GET /audit/logs` with the search
  "password history" answered one entry, "Set Samba's password history to 3 (it was 0)" of
  2026-11-08 04:02 UTC: every start since read the policy without setting it.
- Fresh-context reviews (ADR-0017). The first found that the narrowing of step 2 that this change
  first made (by ADR-0310 then) rested on a claim the run contradicted: the measurement replaced
  it, and the ADR records the measurement. The second found that step 2 had no evidence from
  before the reboot, as step 21 does not compare passdb.tdb: the entry stays open; and that the
  ADR stated Samba's reason as fact, named not every statement it contradicts, and promised no
  write at all where the journal entry stays: fixed. Both entries' notes and this record carry
  the rest of their findings; the two entries are in one change as one run of the scenario
  checks shows both.
- `make check` passed on the tree merged with main a050256 (6027 backend tests, 121 skipped, 1
  xfailed; svelte-check without errors, the build), before these fixes of text.

### 2026-11-09: check.sh checks network discovery, and nmbd is disabled on the device

- `device.check-network-discovery`, at the owner's word to work on through the features ("I
  allow everything", in translation): `scripts/dev/check.sh`'s section "samba and network
  discovery" requires smbd, wsdd2 and avahi-daemon active and reports nmbd while it is active or
  enabled (ADR-0275), whatever smb.conf says: UNOS always renders NetBIOS off, so the branch for
  an smb.conf with NetBIOS on went (ADR-0207). `supervisor/tests/test_dev_scripts.py` gives
  check.sh a systemctl stub per unit; the old check.sh failed the new tests, as it checked
  neither wsdd2 nor avahi-daemon and ignored is-enabled (`assert "- wsdd2 is failed" in out`
  failed; it answered OK with nmbd active).
- Its first run on the device (about 14:01 UTC) found nmbd inactive but enabled: Debian's
  nmbd.service skips its start through `ExecCondition=/usr/share/samba/is-configured nmb` while
  NetBIOS is off, so nmbd did not run (the boot of 12:23 UTC: "Skipped due to
  'exec-condition'"), but `deploy/install.sh` disables it (`systemctl disable --now nmbd`,
  ADR-0275), and step 3 waits for the owner to have turned it off. Changing the device's services
  is the owner's (ADR-0211, ADR-0275): the session ran `systemctl disable nmbd` at 14:02 UTC on
  the owner's word "I allow everything", the end state of install.sh's line, as nmbd was not
  running; it removed its two links (0.680 MiB), and check.sh then answered check: OK (0.016 MiB;
  the run before it 0.055).
- `shares.smb-discovery`'s notes still describe check.sh before this change (the order of its
  device setup, nmbd turned off by the owner): its next pass updates them.
- A fresh-context review found nothing that blocks; fixed from it: the record of who disabled
  nmbd and on which word; the change of `shares.smb-discovery`'s notes taken back out (one entry
  per pass), with the line above instead; "enabled-runtime" no longer said to start at boot;
  tests for an nmbd that is activating or enabled-runtime, and for one that is not installed,
  masked or static (no problem); `docs/TESTING.md`'s description of check.sh.
- `make check` passed on the tree merged with main a8a1f53 (6027 backend tests, 121 skipped, 1
  xfailed; svelte-check without errors, the build).

### 2026-11-09: two entries pass on tests that were there, and a test of a 429 at sign-in

- At the owner's word to work on through the features ("I allow everything", in translation).
  `security.request-limits` and `auth.login-rate-limit` kept `passes: false` for a test that,
  their notes said, was missing; both tests were in the tree already: a declared and a chunked
  2 MiB body with a valid administrator token answer 413
  (`test_a_signed_in_client_gets_413_for_a_declared_and_a_chunked_body`, since 2026-11-07), and
  another client signs in with the right password, 200, while the first still gets 429 with
  it (`test_login_rate_limit_is_per_client_not_global`, in this form since 2026-11-07). Both
  notes came in the same commit as the tests they asked for (2e4306d): they were stale from the
  start. Every step of both is asserted now (the entries' notes map each step to its tests);
  neither is host-level, and both pass in one change, as one stale note made both open.
- `webui/tests/auth.spec.ts` gets 'a sign-in refused for too many attempts says so and keeps the
  form': a 429 at the password step shows "Too many attempts. Wait a minute and try again." and
  keeps the form, as the test of the code step already showed there. It failed with the 429
  message taken out of `webui/src/lib/api/client.ts` and passes with it; the whole spec passed
  on the stand-in (8 tests).
- A fresh-context review found nothing that blocks; fixed from it: the date of the strengthened
  rate-limit test and the history of both notes. Found, and not part of this change: the limiter
  keys the client by the address in X-Forwarded-For that the web UI's Caddy sets; nothing pins
  that `webui/Caddyfile` does not trust a client's own header (`trusted_proxies`), which would
  give a LAN client a new bucket per attempt: a test of the Caddyfile is a task of its own.
- `make check` passed (6017 backend tests, 121 skipped, 1 xfailed; svelte-check without errors,
  the build); of the E2E specs, `tests/auth.spec.ts`, the one changed, passed on the stand-in.

### 2026-11-09: the scenario check smbconf runs the SMB steps checked by hand (ADR-0309)

- Asked whether to move the checks by hand of the Log entry below into the scenario checks, as
  the feature-pass skill asks of a check by hand that is repeated, the owner answered "Yes, move
  them", and then "work on through the features, ask no questions; I allow everything" (in
  translation). `scripts/dev/scenario.sh smbconf`, also in `all`, runs them as an area of
  `scripts/dev/scenario.py`, with the device's regular user for an access entry and a temporary
  share: smb.conf
  edited by hand and named on the Shares page before an apply, the apply's copy byte for byte in
  `smb.conf.unos-replaced` and its problem, a comment with the expected version and a stale one
  refused, the share's folder replaced by a symbolic link and access refused without a new
  folder and saved with one, Delete keeping the folder with its files and smb.conf as found, and
  a restart of the supervisor that writes no file in `/etc/samba` once its startup reconcile
  has run. It refuses to start beside a copy, a section that UNOS did not write, an smb.conf
  that is not UNOS's or a problem.
- ADR-0309 records what ADR-0304 did not allow: the hand edit runs without the read-only
  namespace, and the check restarts the supervisor once (and again in the clean-up only while
  the Shares page names its section). The feature-pass skill's table names the new check for
  `shares.smb-conf-and-status` and `shares.edit-and-delete`; the smb row no longer lists them.
- `supervisor/tests/test_dev_scenarios.py`: the stand-in's smb.conf is a file of its own now,
  with UNOS's six fixed lines in each section, an apply that copies sections without them, links
  that are not served and a restart whose reconcile applies after the API answers; the new tests
  failed before the area existed. The area passes
  and leaves the stand-in as it was, also after a failure at any API call or an interruption;
  four defects fail it (a status that misses the hand-edited file, an apply without the copy,
  access granted to a share that is not served, a restart that writes smb.conf); the helpers
  for the hand edit and the link run here and refuse what is not a check's own.
- On the device at 13:22 UTC every step passed, 2.621 MiB, the restart healthy after about 6
  seconds; the clean-up removed the link, the share's folders and the temporary user, and the
  device was as the check found it. After the review's fixes, at 13:46 UTC, every step passed
  again with the regular user `test`, 2.391 MiB, and the device was as the check found it.
- A fresh-context review (ADR-0017) found nothing that blocks; fixed from it: a section that UNOS
  did not write under UNOS's header, which the apply would copy and the clean-up delete, now
  stops the check at its start, and the clean-up deletes `smb.conf.unos-replaced` only when it
  holds exactly the check's edited bytes; "healthy" did not mean that the startup reconcile had
  applied, so the check now waits for it (a PATCH of a share that does not exist, 404) before it
  reads `/etc/samba`, and the stand-in's reconcile runs after the API answers, as the device's;
  the stand-in's status names a file that is not UNOS's only once an apply has run; the hand
  edit fails as a whole when a part of it fails; the link has a clean-up of its own; the
  device's regular user replaces a temporary one, 0.27 MiB less per run; ADR-0309 names its
  exception to ADR-0183; the skills' texts. Mutations of the wait and of the refusal fail the
  tests. Left as it is: the comment and its 409 repeat a step of the smb check (0.18 MiB), so
  that one row of the feature-pass skill's table covers `shares.edit-and-delete`.
- `make check` passed (6017 backend tests, 121 skipped, 1 xfailed; svelte-check without errors,
  the build).

### 2026-11-09: two SMB entries pass the steps checked by hand

- Asked whether to check by hand on the device the steps of `shares.smb-conf-and-status` (4 and
  5) and `shares.edit-and-delete` (3) that `scripts/dev/scenario.sh smb` leaves out (the
  feature-pass skill's table), told that the checks change the device and need a reboot, the
  owner answered "Yes, check" (in translation). The device runs the runtime files of main
  (`deploy.sh status`). A script of the session ran each step through `scripts/dev/` under
  `sd.sh measure` and printed PASS or FAIL with its MiB; it removed all it made, also after a
  failed step.
- Step 4, as ADR-0092 decides it (copied before an apply, reported after it): smb.conf edited by
  hand (UNOS's header replaced by a comment, a share section `[unoscheck<hex>hand]` with
  `available = no` appended, so that Samba served nothing new). The create of a share through
  the API applied smb.conf: it copied the hand-edited file byte for byte to
  `smb.conf.unos-replaced` (root's, mode 600) and installed UNOS's file with its header,
  [global], [DATA] and the new share's section; GET /shares/smb-status then named the copied
  section, until the supervisor restarted. Before the apply the status named only the file as
  not UNOS's (ADR-0249), because its header was gone: with the header kept, nothing is reported
  before an apply.
- Step 5: the status answered no problem as found and after each clean-up, and named each
  problem on its own: the file that is not UNOS's, the copied section, and the share of step 3
  while it was not served. The Shares page prints each problem of this answer in its error
  alert, which `webui/tests/shares.spec.ts` asserts on the stand-in (ADR-0215); the panel itself
  was not opened on the device, whose web UI runs the panel code of main (`webui/` differs only
  in its Dockerfiles).
- Step 3: the share's folder replaced on the host by a symbolic link, so that UNOS did not serve
  the share ("Share path must not contain symbolic links (...)"; smb.conf kept its section until
  the next apply). A PATCH with the expected version and read access for the regular user `test`
  was refused with 400 and that reason and changed nothing (version 1, no access, smb.conf
  unchanged); the same PATCH with a new folder was saved as version 2, made the folder
  root:unos-smb 2770 and served the share there (`test`'s entry saved, not effective while its
  SMB is off: `valid users = root`). Then the share, the link and the folders went.
- Before the reboot (from 12:20:49 UTC) every step passed, 1.082 MiB. Rebooted from the API at
  12:22:52 UTC: smb.conf byte-identical and not written by the boot, the status without a
  problem. After the reboot (from 12:23:57 UTC) every step passed again, 2.308 MiB; a restart of
  the supervisor at the end (`docker restart`, healthy after about 8 seconds) left smb.conf
  unchanged (same bytes and mtime; step 3 of `shares.smb-conf-and-status`) and the status
  without a problem. smb.conf ends byte-identical to how the checks found it, rewritten at
  12:24:22 UTC by the last delete's apply. `check.sh` OK before and after. Both entries pass.
- The first two attempts stopped on errors of the session's script, not of UNOS: the first only
  read the device; the second edited smb.conf by hand, and its clean-up put the file back with
  the hash it had found.
- A fresh-context review (ADR-0017) found no step unmet. Fixed from it: "wrote no file" for the
  restart, which compared smb.conf only; step 4's reading of ADR-0092 and what the status names
  before an apply; that the panel was not opened on the device; the caveats of step 3; the MiB
  of every read; Next item 3. `make check` passed (5978 backend tests, 121 skipped, 1 xfailed;
  svelte-check without errors, the build).

### 2026-11-09: a sign-in writes at most 0.1 MiB in the middle of the log (ADR-0308)

- Asked whether an ADR rewords step 3 of `architecture.sqlite-write-cost` (a sign-in writes a
  few KiB) to the writes measured with the write-ahead log, the owner answered "Yes, through
  an ADR" (in translation). ADR-0308 sets at most 0.1 MiB for a sign-in in the middle of the
  log, against the 0.082 MiB measured on 2026-11-09 (the Log entry below), and the entry's
  description says the same; the first commit after a start or a checkpoint stays outside
  the bound. The entry passes. `scripts/dev/api.py`, the deploy-device skill and the
  device-checks skill quote a sign-in's writes as measured, in place of 0.16 MiB.
- A fresh-context review found no blocker: the numbers match the measurements, a new ADR
  that names the entry is right (ADR-0224 only handed the SQLite part to it), and the quotes
  are accurate. Its minor findings, fixed: the ADR and the notes say that each figure is one
  run, the ADR that the start itself made `unos.db-shm` again, the notes why no re-check after
  a reboot applies (not a host-level change), and a long line of the device-checks skill is
  wrapped. `make check` passed (5978 backend tests, 121 skipped, 1 xfailed; svelte-check
  without errors, the build).

### 2026-11-09: the deploy of main 12483dd and the SD writes of the write-ahead log

- At the owner's word ("do it", to the deploy of `architecture.sqlite-write-cost` that waited
  for their yes) deployed main 12483dd: the database's write-ahead log (ADR-0306) and the start
  order of ADR-0307. `make check` passed on that tree (5978 backend tests, 121 skipped, 1
  xfailed; svelte-check without errors, the build). `deploy.sh status`: 6 runtime files
  differed (the 4 of the supervisor that ADR-0306 changed, `deploy/update.sh` and a comment of
  `deploy/docker-compose.yml`); the sync wrote 27 files and deleted the ADR's old name.
- build-supervisor started from the pinned base on the device and took every step from the
  cache but `COPY src/`: deploy-supervisor:latest `a32d5759...`. up recreated the supervisor and
  kept the web UI, both healthy, post-up check OK. In the data volume `unos.db-wal` (0 bytes)
  and `unos.db-shm` (32 KiB) appeared beside `unos.db` at the start.
- Measured with `scripts/dev/sd.sh measure`, each sign-in after `scripts/dev/api.py
  --forget-token`: a sign-in right after the start, which starts the log, 0.145 MiB; a sign-in
  in the middle of the log 0.082 MiB, against 0.156 to 0.168 with the rollback journal; a call
  with a cached token 0.020 MiB, as before; 30 seconds idle 0 MiB before and after. The two
  sign-ins grew `unos.db-wal` to 24,752 bytes, 6 frames of 3 pages each, as ADR-0306 counted.
  Each measurement syncs the file system before and after, so it counts a request's writes at
  once; without the sync the kernel writes them out within about 35 seconds, together with those
  of other commits (ADR-0306).
- `docker restart unos-supervisor` up to healthy, about 8 seconds: 1.125 MiB in all. The stop's
  checkpoint rewrote `unos.db` and emptied `unos.db-wal`, and the start made `unos.db-shm`
  again; Docker's own writes of a restart are part of the number, so it does not isolate the
  0.05 MiB of ADR-0306's exception.
- `scripts/dev/scenario.sh all` after it, from 07:43 UTC: every step of the five areas passed
  (63), among them a plain and an encrypted backup of the database in the log; 4.606 MiB.
- `architecture.sqlite-write-cost` keeps `passes` false: its step 3 asks for a few KiB, and the
  owner's choice of ADR-0306 measures 0.082 MiB in the middle of the log. An ADR that rewords
  the step is the owner's call.

### 2026-11-09: the reboot of 07:17 UTC: the web UI waited for the API at boot

- At the owner's word ("permission granted"), each step measured with `sd.sh`: `check.sh` OK
  before (0.039 MiB); `docker image prune -f` deleted the 11 dangling images and reclaimed
  134.9 kB (5.371 MiB); the reboot from the API at 07:16:57 UTC (the boot of 07:17:13 UTC; ssh
  answered by 07:17:34); the RAM rootfs of `scripts/dev/linux-tests.sh` made again at 07:21:13
  (1.148 MiB); `check.sh` OK after it (0.023 MiB).
- The re-check of `deploy.health-checks-and-start-order` after the reboot (ADR-0018), with 78bb5b8
  on the device: dockerd started both containers at once again (07:17:05 by the clock before
  timesyncd set it at 07:17:28). The web UI's command logged one wait, `waiting for the
  supervisor's API: wget: can't connect to remote host (172.31.254.3): Connection refused`, and
  Caddy's first line came at 07:17:34.27 UTC, 1.5 s after the supervisor's `Application startup
  complete` (07:17:32.78). Both containers healthy with no restart, Caddy the web UI's PID 1, the
  health checks without credentials, `check.sh` OK: the entry passes (ADR-0307).
- The old boot wrote 36.0 MiB in its 8.3 hours; the new one 9.5 MiB by 07:21:37 UTC, the RAM
  rootfs's 1.148 among them; the re-check's reads 0 to 0.141 MiB.
- `scripts/dev/linux-tests.sh` on the tree of 195f7cb, 07:27 to 07:33 UTC (0.039 MiB): 5266
  passed, 324 skipped. Its 29 failures and 456 errors all come from the chroot of the supervisor
  image, which has no git (466), no make (18) and no pty device (1): the tests of the guard and
  of the repository's tooling, and two of `deploy/reset-admin.sh`. No other test failed.

### 2026-11-09: the web UI waits for the API also at boot (ADR-0307)

- The re-check of 2026-11-08 after the reboot left step 2 of
  `deploy.health-checks-and-start-order` open: the dev device has no `unos` unit (systemd finds
  none), and at boot dockerd started both containers by their restart policy at once, the web UI
  first. On a host of `install.sh` the unit's `docker compose down` at shutdown leaves dockerd
  nothing to start after a clean shutdown, but not after a power cut. The owner left the choice
  to the agent ("do it").
- The web UI's `command` in both compose files now waits until
  `http://supervisor:8080/api/v1/health` answers (busybox wget without a proxy and without a
  file, an error printed only when it changes) and then execs the image's CMD (ADR-0307).
  `test_webui_waits_for_the_api_whoever_starts_it` failed first (no `command`), then passed, and
  fails for a loop that prints each try or a single `$`. In the running web UI on the device
  (busybox 1.37, `caddy` left out) the loop waited on a closed port and on an unknown name,
  printing each error once, and passed at once on the API (0 MiB).
- Deployed at 01:18 UTC: sync 0.812 MiB, `up webui` 2.453 MiB (only the web UI recreated,
  healthy after 6 s, post-up check OK). Its PID 1 is `caddy run --config /etc/caddy/Caddyfile`,
  and its log has no waiting line, as the API answered at the first try; `check.sh` OK.
  `make check` passed before the deploy (5966 tests).
- A fresh-context review found no blocker; its findings, all fixed: the CHANGELOG spoke of every
  reboot; the silent loop hid a cause that stays (now its error is printed once); the test ran
  the command without compose's `$$` (now through `_compose_string`); the test stack's comment
  and other wording; the dev device's missing unit and the owner's decision were unrecorded; and
  the ADR lacked the option of Caddy answering while the supervisor starts.
- Open: the re-check after a reboot (ADR-0018): Caddy's first log line after the supervisor's
  startup, at boot.

### 2026-11-08: the testparm test of Samba's logging lines

- `test_samba_takes_the_logging_lines` of `supervisor/tests/test_smbconf_testparm.py`, which
  skips without testparm, failed against the device's testparm (Samba 4.22.6): it expected a
  `log level` line and `debug timestamp = no`. Probed through the shim: the dump of
  `testparm -s` shows no `log level` line, not even for a debug class that Samba lacks, and
  names `debug timestamp` `timestamp logs`; only a misspelt parameter name draws a warning. So
  of `log level` testparm checks only the parameter's name, never its classes or levels.
- With the owner's yes ("yourself", in translation, asked for that yes) the test now asserts
  `timestamp logs = no` beside the log file, its size limit and the syslog level; that Samba
  knows the name `log level` is what `testparm()` asserts for every line. All 11 tests pass
  against the device's testparm (0 MiB, measured with `sd.sh`); with `debug timestamp = yes` in
  the renderer the logging test fails. The level of auth_audit rests on `test_smbconf.py`.
- At work after the reboot, from the journal of the smb check's run at 23:03 UTC: the account's
  two wrong passwords each added one notice of smbd.service with `Auth:`,
  `NT_STATUS_WRONG_PASSWORD`, the account and `remote host [ipv4:127.0.0.1:`, and its successful
  sign-ins none, so auth_audit logs at level 2, not 3; no smbd message had a header line, and
  `GET /logs/entries?source=samba&search=<account>` listed both entries. The notes of
  `shares.smb-auth-audit-logging` record it as the entry's re-check after a reboot.
- A fresh-context review found the test right and as strong as testparm allows, and five
  problems of wording and record: an overclaim of what the device check had seen, the owner's
  yes not recorded, words wider than the probes, stale notes and missing backticks; all fixed.

### 2026-11-08: the re-check of the host-level entries after a reboot (ADR-0018)

- The owner rebooted the device at about 22:57 UTC; it answered again at 23:00 (the boot of
  22:59:48 UTC). Read before the reboot and again after it: the API's pools, shares, Samba
  status, NFS exports, users, installed apps, RAID monitor, `health/full` and Prometheus metrics;
  the sha256 of `/etc/fstab`, `smb.conf`, the export file and the IPv6 filter's rules; the modes
  of the import backup and of the share DATA's folder; 8 services, the pool's mount and the
  containers; the HTTPS headers, the redirect and the answers without a token. After the reboot
  all of it was the same but for times, memory and load, and `check.sh` was OK once the web UI
  turned healthy, a minute after the boot. Each read wrote 0 to 0.031 MiB.
- `scripts/dev/scenario.sh all`, 23:02 to 23:05 UTC: every step of the five areas passed again,
  with the NT_STATUS answers of the runs before; files 0.441 MiB, volumes 0.734, smart 0.105,
  backups 0.449 and smb 3.664, 5.393 MiB in all. By hand: Delete and Delete for good of a folder
  with a bind mount below it answered 409 and deleted nothing (the mount and both folders were
  removed afterwards); the boot's restart of the supervisor changed no file (smb.conf
  byte-identical, fstab, the export file and the import backup untouched, no apply in its log).
- 12 entries passed, each with its re-check in its notes: `device.scenario-checks-in-repo`,
  `files.protects-volumes-and-mounts`, `shares.smb-accounts`, `shares.smb-share`,
  `shares.per-user-access`, `shares.guest-off-by-default`, `shares.password-follows-unos`,
  `security.https-local-ca`, `shares.smb-conf-import`, `apps.install-on-a-pool`,
  `apps.transmission-login` and `system.health-details-and-metrics`.
- Not passed: `storage.pool-health`, whose step 4 (a degraded RAID1 test pool) needs spare disks
  and was never checked on the device; `deploy.health-checks-and-start-order`: at boot dockerd's
  restart policy starts both containers at once, the web UI 34 ms before the supervisor, and the
  web UI's first health check failed before it turned healthy, so step 2 holds for
  `docker compose up` only (the fix: ADR-0307, 2026-11-09).
- Not passed either: `shares.smb-conf-and-status` and `shares.edit-and-delete`, first recorded
  as passed. The re-check did not check by hand the steps of theirs that the smb scenario check
  leaves out (the feature-pass skill's table): step 4 of the first (a share section that UNOS
  did not write, copied to `smb.conf.unos-replaced` and reported before an apply) and its
  step 5 (a problem shown as an alert of the status panel), and step 3 of the second (a share
  that is not served gets access only with a new folder). A fresh-context review of this record
  found that on 2026-11-09, and both went back to `passes: false` with notes saying what is
  missing.
- The boot wrote 9.7 MiB by 23:00:29 UTC. Docker records that boot's container starts as
  22:56:57, before the clock was set. The RAM rootfs of `scripts/dev/linux-tests.sh` is gone
  again (made again on 2026-11-09).

### 2026-11-08: the deploy of main 78769b4

- At the owner's request ("Yes, deploy" and "do it all yourself", in translation) deployed main
  78769b4, with #79 (`device.scenario-checks-in-repo`) and #80
  (`deploy.digest-pinned-base-images`), from the worktree of the scenario checks: `make check`
  passed on it (5964 backend tests, 121 Linux-only skipped, 1 xfailed; svelte-check without
  errors; the build), and `make smoke`. `scripts/dev/deploy.sh status`: of the runtime files only
  `supervisor/Dockerfile` and `webui/Dockerfile` differed, besides 28 docs, tests and tools.
- Read on the device before the builds, as build-supervisor does: python:3.11-slim
  `sha256:65bd3d9f...` is no image, and the build cache holds layers pulled from it;
  caddy:2-alpine `sha256:f2b257f2...` is an image (0.129 MiB, with `docker buildx du`).
- SD writes: backup 0 MiB; sync 1.527 MiB (30 files, 1043.5 KiB); build-supervisor 6.094 MiB:
  `base python:3.11-slim@sha256:65bd3d9f...: on the device`, then `COPY requirements.txt`,
  `pip install` and `COPY src/` CACHED, nothing pulled, deploy-supervisor:latest `97728ea0...`;
  build-webui 18.539 MiB (18.555 MiB that morning): the final stage from the pinned Caddy on the
  device with `--pull=false`, nothing downloaded, deploy-webui:latest `d77b0aa7...`; up
  5.676 MiB: both containers recreated, healthy after 7 s, post-up check OK (the pins of
  `supervisor/requirements.txt` installed, Caddy v2.10.2, the content security policy of
  `/srv/index.html`); 31.836 MiB in all.
- After it: `scripts/dev/check.sh` OK (no ERROR line in the supervisor's log, the IPv6 filter
  loaded); `GET /system/stats` answered 200 (0.027 MiB); `scripts/dev/scenario.sh all` passed
  every step of the five areas again, 22:32 to 22:35 UTC, with the NT_STATUS answers of the run
  at 17:15 UTC: files 0.426 MiB, volumes 0.641, smart 0.125, backups 0.430 and smb 3.637,
  5.259 MiB in all. The pool holds no `unoscheck` name and its trash is empty.
- `tests/test_smbconf_testparm.py` against the device's testparm: 10 passed, and
  `test_samba_takes_the_logging_lines` failed (0 MiB). It expects `log level = ...auth_audit:2`
  and `debug timestamp = no` in testparm's output, and Samba 4.22.6's testparm prints no
  `log level` line and the second as `timestamp logs = No`. The rendered lines are those of
  a641c0e, this deploy changed no Samba code, and at the device check of
  `shares.smb-auth-audit-logging` the journal held a failed sign-in that auth_audit logged: the
  test's expectations are wrong, a fix of its own.
- `scripts/dev/linux-tests.sh`: the RAM rootfs is gone since the morning's reboots, and making it
  again creates a container, an owner action (made again on 2026-11-09).
- `deploy.digest-pinned-base-images` keeps `passes` false: its supervisor build reused the
  cached base, and step 3 asks for that once the tag's arm64 image has moved upstream (that
  morning only riscv64 had moved), step 2 for Dependabot's first digest pull request.

### 2026-11-08: the database uses SQLite's write-ahead log (`architecture.sqlite-write-cost`)

- The cause of a sign-in's 0.156 to 0.168 MiB on the SD card: `unos.db` kept SQLite's defaults,
  the rollback journal with synchronous FULL. Counted with a VFS that counts SQLite's own file
  operations, one login-history row's commit created, synced twice and deleted
  `unos.db-journal`, synced its folder, and wrote and synced 4 pages of `unos.db`; every sync
  makes ext4 commit its own journal. With the write-ahead log and synchronous NORMAL the same
  commit, in the middle of the log, appends 3 pages (12,360 bytes) to `unos.db-wal`, changes 3
  pages of `unos.db-shm` and syncs nothing.
- The owner chose "WAL + NORMAL" out of three options, with its costs (ADR-0306, superseding
  ADR-0078): a power cut can roll back the commits of its last seconds, security changes among
  them; the first connection after every start makes `unos.db-shm` again, about 0.05 MiB, which
  ADR-0214 now names as its one accepted exception ("Yes, an exception"). Synchronous FULL for
  security changes only was offered and not chosen.
- Backups stay one file with the rollback journal (a copy of a database in the log came out
  marked for the log, and the restore refused it with 400); `update.sh` keeps `unos.db-wal`
  beside its copy of a supervisor that was not running; a restore refuses a backup with pages of
  another size before it changes anything (it failed with 500 after the copy kept before a
  restore); at the stop a checkpoint copies the log into `unos.db` and keeps `unos.db-wal` and
  `unos.db-shm`; a switch to the log that SQLite refuses leaves the connection in the rollback
  journal with FULL, which reads as before (ADR-0251); `docs/RECOVERY.md` copies the database's
  files as root and reads the copy, and `docs/INSTALL.md` says to delete `unos.db-wal` and
  `unos.db-shm` before a `unos.db` is put back by hand.
- Tests first: 12 new tests. Eight failed on the code before (the engine in `delete` and 2, pages
  0 to 3 of `unos.db` written by a sign-in, no log, both backup copies marked for the log, the
  restore's 400 and 500, the runbook's command on the mount), the backup of a stopped supervisor
  failed before its fix, and the others caught their named mutations (no checkpoint at the stop,
  or closing the pool there; no warning; a refused switch that raises, or sets NORMAL).
- Reviewed in a fresh context (ADR-0017): no blocker, 2 major and 8 minor findings and 3 nits,
  all fixed but one recorded. Fixed: a start on a full `/data` after a clean stop could not read
  the database, as closing the pool deleted `unos.db-shm` (now the stop's checkpoint keeps it);
  the runbook's `unos.db*` was expanded by the user's shell, which cannot enter Docker's folders
  (now root's shell does); "syncs nothing" (the commit that starts the log again syncs it once);
  the power-cut window, up to about 155 seconds while the log grows, not 120; the log's size
  after a restore; NORMAL on the rollback journal after a refused switch; `update.sh`'s copy of a
  stopped supervisor; a test docstring's reason, and the rule for a `unos.db` put back by hand;
  ADR-0214's new line, moved from its Decision to its Consequences (the owner asked that ADR-0214
  name the exception; its decision text is unchanged); the restore test's check of the file's
  header; `make e2e-local`. Recorded: the in-app updater copies `unos.db` with `shutil.copy2`,
  wrong in the log; it starts nothing today (`system.in-app-updater`).
- `make check` passed (5874 backend tests, 121 skipped, 1 xfailed; svelte-check without errors,
  the build), and `make e2e-local` alone (669 passed, 16 skipped): the stand-in's real supervisor
  ran its database in the log, its restores kept copies with the rollback journal, and its stop
  left an empty `unos.db-wal` and `unos.db-shm` beside `unos.db`. (A run beside `make check`
  failed from its 386th test on: that build replaced `webui/build` while the stand-in served it.)
- Committed as da7bb46, which names the decision ADR-0304. main took ADR-0304 for the scenario
  checks (#79) and ADR-0305 for the base images (#80) first, so the merge of main renumbered this
  branch's ADR to ADR-0306 (the adr skill). After the merge `make check` passed (5976 backend
  tests, 121 skipped, 1 xfailed), and `make e2e-local` alone (669 passed, 16 skipped).
- Not deployed. After the owner's deploy of the supervisor (no Dockerfile or requirement
  changed), twice: `scripts/dev/api.py --forget-token`, then
  `scripts/dev/sd.sh measure sign-in -- scripts/dev/api.py GET /auth/me` (the second sign-in is
  in the middle of the log), against the 0.156 to 0.168 MiB of before; and the writes of a
  supervisor start against the exception's 0.05 MiB.
- Merged into main after the deploy record of main 78769b4 and the testparm test, each by a
  merge commit pushed over SSH without a pull request: no client on the workstation was signed
  in to GitHub's API, and the owner said "YOU CAN DO IT BY YOURSELF PERMISSION GRANTED". The
  merge of main joined only PROGRESS.md by hand; `make check` passed on the tree of all three
  (5976 backend tests, 121 skipped, 1 xfailed; svelte-check without errors, the build).

### 2026-11-08: the base images pinned by tag and digest

- `deploy.digest-pinned-base-images`, on its branch (ADR-0305): every `FROM` line names
  `name:tag@sha256:...`. The pins are what the dev device holds: python:3.11-slim
  `sha256:65bd3d9f...` in BuildKit's build cache and caddy:2-alpine `sha256:f2b257f2...`;
  node:22-alpine and the Playwright image take the registry's digests of the day. Read on the
  device in its read-only namespace: the first layers of deploy-supervisor:latest and
  deploy-webui:latest are those bases'; each read wrote 0 to 0.023 MiB. The registry's
  python:3.11-slim had moved to `sha256:07d48240...` that morning, but only for riscv64: its
  linux/arm64 image is still the cached one, which BuildKit would have reused.
- The tag stays in the `FROM` line instead of a comment, as the entry first said: dependabot-core
  reads the tag only there, and for a digest without a tag proposes the digest of `latest`. Its
  cooldown counts from the tag's last push, and its experiment
  `docker_digest_only_update_suppression` can stop digest-only pull requests, so the monthly pass
  compares the pins as well (`docs/maintenance.md`, item 11).
- `deploy.sh build-supervisor` refuses a base without a whole digest and says before the build
  whether a layer of the build cache comes from the pinned digest; run by hand on the device,
  read-only, that check found `65bd3d9f...` and not `07d48240...` (0.016 MiB).
- Tests: `TestBaseImages`, the digest in the three tests that read `FROM` lines, and
  build-supervisor's base check against a docker stub; each failed before the change. `make
  check` and `make e2e-local` passed. A fresh-context review found the false claim that the
  07d48240 move would have cost the device a rebuild, a note of build-supervisor that said "pulls
  it" where BuildKit reuses an unchanged arm64 image, Dependabot's cooldown, a digest without
  hex that passed the check, and a test blind to the order of the refusal: all fixed. Kept by
  design: the `FROM` check of the test treats a line that starts with `from` as an instruction,
  also inside a heredoc, and build-supervisor reads the second word of a `FROM` line.
- Committed as dbb8df7, which names the decision ADR-0304. main took ADR-0304 for the scenario
  checks first (#79), so the merge of main renumbered this branch's ADR to ADR-0305 (the adr
  skill).
- Next, the owner's: the pull request. Step 3 of the entry needs a deploy after python:3.11-slim
  names an arm64 image other than `sha256:31afb5c0...`: build-supervisor then says "base
  python:3.11-slim@sha256:65bd3d9f...: on the device" and shows every step CACHED, with the SD
  writes in single MiB. Step 2 waits for Dependabot's first digest pull request. The pinned Caddy
  2.10.2 is older than the registry's caddy:2-alpine (`sha256:78bd3502...`): moving it needs that
  image pulled onto the device first (an owner action) before build-webui.

### 2026-11-08: the scenario checks of the dev device, in the repository

- Built `device.scenario-checks-in-repo` (ADR-0304) in its worktree:
  `scripts/dev/scenario.sh [--pool NAME] files|volumes|smart|backups|smb|all` holds the device
  lock, runs `scripts/dev/scenario.py` once per area and prints `SD writes scenario <area>`.
  Each check reads the device's state, makes a temporary user, share, volume and folders named
  `unoscheck<6 hex digits>` and two backups through the API, prints PASS or FAIL with each
  step's SD MiB, removes what it made (also after a FAIL, a lost answer or a signal) and compares
  the device with the state it read. The smb check runs the slice-2 flows of ADR-0185 with
  smbclient on the device, one session held open while the API lowers and removes the access and
  changes the password. `scripts/dev/api.py`'s relay now uploads files. The feature-pass skill's
  table names the check of each host-level entry, and what each scenario check leaves out.
- Tests: `supervisor/tests/test_dev_scenarios.py` (81, in `make test-fast`) runs every area
  against a stand-in of the device in memory, with a 500 at each API call before and after the
  call changed something, and checks that every call is a route of the supervisor with a body its
  model accepts; `make check`: 5953 backend tests passed (121 Linux-only skipped, 1 xfailed),
  svelte-check without errors, the build passed.
- This worktree's `supervisor/.venv`, `.venv-lint` and `webui/node_modules` were copied from
  `../unos-shares.nfs-exports-pass` with their stamps; the venv holds the pins of before #25, as
  the main checkout's, so `setup.sh --check` reports it outdated (Open owner actions, 2).
  `make smoke` passed before the change.
- The first fresh-context review found 4 major and 6 minor problems and some nits, all fixed:
  writing to a closed terminal stopped the clean-up; the quota step read a field that the API's
  list of volumes does not have (now the quota group's limit is read with btrfs); backups are not
  named `unoscheck`, and their clean-up, like the user's and the share's, was registered after
  the call that made them (now before, and a backup whose answer was lost is named, never
  deleted); the skill's table missed `shares.nfs-exports`, `device.ipv6-inbound-filter` and
  `device.key-only-ssh` and hid what the checks leave out; the clean-up caught fewer errors than
  a step; the docs understated the SD writes; an unreadable log counted as clean; several steps
  took any failure, and the anonymous attempt was no null session and required signing on the
  client; the advice for leftovers was incomplete; digests in the output, an interrupted check
  without its SD line, untested helpers, and `scenario.py` running without the lock.
- A second fresh-context review of the fixes found no major problem and 6 minor ones and 4 nits,
  all fixed: a HUP or TERM to the process group ended the session that holds the device lock while
  a check still cleaned up (`dev_lock` of `scripts/dev/env.sh` now starts it with both ignored and
  ends it with SIGKILL at the release, for every script that takes the lock); a Ctrl-C during the
  SD measurement let the next check start; a lost output let the steps go on unseen and turned
  the exit status into 120; with a guest share the null session's refusal was expected; the
  "partition" refused was no partition on the device; any 409 naming a quota passed as quotas
  being off; a signal during the clean-up was dropped instead of waiting; `client ipc signing`
  was left to its default; and the leftover advice, the ADR's wording and the lock's check of
  its holder.
- Left out by design, in the skill's table: a restore, a supervisor restart, a pool mounted
  read-only, a bind mount below a folder, guest shares, Empty trash, scrubs, the Scheduler's
  tasks, and what survives a reboot (each check removes what it makes): by hand.
- Committed as 2c15597 and pushed to its branch at the owner's word; the pull request is the
  owner's. With the owner's go for the rest of the feature ("bypass all permissions"), the first
  run of `scripts/dev/scenario.sh all` on the device (the pool `data`, at about 17:15 UTC)
  passed every step, and each check left the device as it found it: files 16 steps, 0.418 MiB on
  the SD card; volumes 7, 0.938 MiB; smart 5, 0.098 MiB; backups 8, 0.512 MiB; smb 27,
  3.980 MiB, of which 0.957 MiB made the account and 0.785 MiB deleted it; 5.946 MiB in all.
  Samba answered as ADR-0103 and ADR-0104 expect: NT_STATUS_NETWORK_NAME_DELETED for the
  session's next write once its access was lowered and for its tree connect once the access was
  removed, NT_STATUS_ACCESS_DENIED for a connect without access and for the null session,
  NT_STATUS_CONNECTION_DISCONNECTED for the open session at the password change, and
  NT_STATUS_LOGON_FAILURE for the replaced and for a wrong password; the replaced one left
  passdb.tdb as it was (ADR-0278). Quotas are off on `data`, so the volumes check saw the
  refusal; sda has no partition, so the smart check refused the system disk and an absent name
  only, and its short self-test of sda completed without error. `scripts/dev/check.sh` before
  and after: OK, no ERROR line in the supervisor's log. No step failed: the scripts needed no fix.
  Open: the same run after the owner's reboot (ADR-0018), then `passes`.

### 2026-11-08: the NFS server on the dev device, and `shares.nfs-exports` on it

- At the owner's request ("Enable NFS") nfs-server, rpcbind.socket and rpcbind are enabled and
  run on the device (ADR-0303), 0.348 MiB. Over IPv6, 111 and 2049 answer the workstation on the
  LAN and are closed to the network outside (the namespace probe of the IPv6 filter); over IPv4
  the workstation's showmount answers.
- `shares.nfs-exports` passes: through the API, a read-write export for 10.1.1.0/24 went into
  UNOS's file and exportfs; a line written by hand into /etc/exports was listed as not managed
  and kept its bytes; a client (the device itself, through its LAN address: an NFS mount on the
  workstation needs root) wrote with root squashed to nobody, and could not write once the export
  was read-only; after a reboot the export was back with the pool mounted; Delete removed it.
  An add or a delete writes 0.30 to 0.32 MiB, a client mount 0.21 to 0.24 MiB. The probe folders
  and the hand line were removed afterwards.

### 2026-11-08: the web UI on a phone, on the dev device

- Merged #76 (`webui.mobile-layout`, ADR-0302): below 768 px a menu button in the top bar opens
  the navigation as a drawer, and every page, tab and dialog fits a 375 px screen without
  scrolling sideways; only a table scrolls, inside its `.table-frame`.
  `webui/tests/mobile-layout.spec.ts` checks it in English and Russian at 375 x 812, sideways at
  667 x 375, and from 768 px up; the whole E2E suite passed on the stand-in (631 passed, 16
  skipped). A fresh-context review found six problems (the New Folder row, a long folder cut
  off, Back to a hash leaving the drawer open, a tap beside the drawer on iOS, dialogs that did
  not scroll, notes that said more than the spec checks); all are fixed, with three more
  overflows found on the way. Not checked: a phone's own browser.
- Deployed the web UI only (no supervisor file changed): sync 2.105 MiB, build-webui
  18.555 MiB, up 2.969 MiB; check.sh OK, the IPv6 filter loaded.

### 2026-11-08: the inbound IPv6 filter on the dev device

- At the owner's request ("Yes, turn it on") the device admits inbound IPv6 to its services only
  from the LAN (ADR-0301, superseding ADR-0227): `scripts/dev/ipv6-filter.sh install` put the
  table `ip6 unos_ipv6_inbound` (`/etc/unos-dev/ipv6-inbound.nft`) and
  `unos-ipv6-inbound.service` on the device, with the LAN's global prefix read there, so it stays
  out of the repository. The first install wrote 0.164 MiB, the reviewed one 0.133 MiB and a
  rerun 0 MiB; deleting the first install's copy under `/etc/unos` 0.098 MiB.
- Before: from a network outside the LAN (a network namespace on the device with the source
  2001:db8:6::2, routed to the device's global address) every service port answered. After, and
  again after two reboots from the API: 22, 80, 443, 445, 5355 and 9091 closed, 51413 open, ping
  answers. From the workstation 22, 80, 443, 445, 9091 and 51413 answer over both global and
  both unique-local addresses and the link-local one, and 22, 443 and 445 over IPv4; Bonjour,
  the router over link-local and outbound IPv6 work. The unit is active before systemd-networkd,
  no unit failed, and check.sh, which now reports the filter, is OK. The probes wrote 0 to
  0.023 MiB each.
- Reviewed twice in a fresh context, the second time by a workflow of four lenses (netfilter,
  systemd, shell, the repository's rules) with a skeptic per finding: 16 of 22 findings held and
  are fixed, among them check.sh's system-disk line, install's unchecked writes and a SIGPIPE
  race in its refusal, `remove --help` removing the filter, remove's leftovers, multipath
  routes, the file's place under `/etc/unos` (which `deploy/uninstall.sh` deletes) and the
  docs' ICMPv6 and NFS claims. Kept by design: every on-link global route counts as LAN (a VPN or
  Docker network served by the device).
- `device.ipv6-inbound-filter` passes. Not probed: the router's own inbound IPv6 filtering from
  the internet. The filter decides by source address, so a datagram from outside that forges a
  unique-local or LAN source is stopped only by the router's ingress filtering (the UDP services;
  a forged source cannot complete a TCP handshake).

### 2026-11-08: NFS, the command timeouts and the Russian web UI, on the dev device

- Merged: NFS exports on the host (#65), the command timeouts (#62), and the web UI's translation
  into Russian in nine branches (#59, #63, #64, #66 to #73): 1782 keys in English and Russian, and
  no text outside the catalogs. Deployed main ebb1856: the supervisor 12.4 MiB, the web UI 18.6
  MiB, up 5.5 MiB; 41.2 MiB in all.
- NFS on the device (nfs-kernel-server installed, 27 MiB): an export written to UNOS's own file,
  listed by exportfs and by the workstation's showmount, and deleted again; then nfs-server and
  rpcbind stopped and disabled, as inbound IPv6 is not filtered.

### 2026-11-08: the third feature batch and unmounting, on the dev device

- Deployed main 533668a (#53 to #60): sync, the supervisor 13.3 MiB, the web UI 17.5 MiB, up
  5.7 MiB; 41.4 MiB in all. Network discovery set up with install.sh's own steps: avahi-daemon and
  wsdd2, 16 MiB.
- Passed on the device: unmounting the pool data (refused while transmission ran; with it stopped,
  the supervisor unmounted a service's copy of the mount, and the host was clean), the share taken
  out of smb.conf and served again, an app refused to start on the unmounted pool, mounting with
  the marker and health back, a New Folder in a share at 2770 with the group unos-smb, Samba's log
  file in /run/samba and a failed sign-in found in System > Logs, and Bonjour listing the NAS on
  the workstation. A reboot from the API brought it all back; check.sh no longer counts the
  stopped nmbd as a problem, since NetBIOS is off (ADR-0275).

### 2026-11-08: the second feature batch, on the dev device

- Nine groups built in parallel as in the first batch; merged as #43 to #51 with their ADRs
  renumbered (ADR-0248 to ADR-0268). Deployed main 1fdf802: sync 4.0 MiB, the supervisor 12.6
  MiB, the web UI 17.4 MiB, up 5.5 MiB.
- Passed on the device: the trash on the pool and in a share (with the veto line and testparm),
  the host's logs from six sources newest first, a custom task's output and exit status,
  Containers' stop and start and the Images list, a scrub to the end and a cancelled one, and
  the scheduler following the host's time zone (set to Europe/Moscow and back to UTC; that change
  wrote 9.7 MiB).
- Found: unmounting a pool is refused on the device, because systemd services' own mount
  namespaces keep copies of the pool's mount that the host's umount does not reach. UNOS mounts
  the pool again, so nothing is lost; the fix is on its way for `storage.mount-and-unmount`.

### 2026-11-07: the first feature batch on the dev device

- Deployed main cce0def with `scripts/dev/deploy.sh all`: sync 3.9 MiB, the supervisor build
  333.7 MiB (the pip layer of #25), the web UI 16.6 MiB, up 5.4 MiB; 359.7 MiB in all. Then
  `deploy.sh` passed the tree's version to the build (5.4 MiB), so System > Updates names it.
- Checked through the API, each check cleaning up after itself: volumes and snapshots on the
  pool data, a snapshot task with keep count 3, the network revert on a VLAN added for the check,
  Wake-on-LAN, a cancelled reboot, and a reboot from the API that served as the reboot re-check of
  ADR-0018 (back after 114 s: pool, Samba, share, containers; the boot wrote 9.8 MiB). The
  Linux-only backend tests of files and uploads passed in the device's RAM.

### 2026-11-07: the first feature batch

- The owner asked to work on features. A read-only triage of the 139 unfinished product entries
  of `feature-list.json` against the code found 73 missing, 47 partial and 19 implemented but
  not yet checked. Eight groups of the most valuable ones that this machine can test were built
  in parallel, one worktree each: tests first, `make check` and the E2E specs on the stand-in,
  a fresh-context review of each branch and the fixes of every finding.
- Merged as #32 to #39; the branches' ADRs were renumbered at the merge (ADR-0232 to
  ADR-0246). Passing now: `storage.smart-replace-soon`, `webui.audit-log-page`, `security.audit-log`, `auth.recovery-codes`, `backup.encrypted-scheduled-backups`, `system.scheduler-fixes`. The others need the device check after the
  next deploy. `make check` and every E2E spec (415 passed, 16 skipped where the stand-in lacks
  the host) passed on main before the last merge.

### 2026-11-07: pull requests merged, CI off, Dependabot's rules

- The owner asked to merge every pull request and to turn the CI checks off. Merged through the
  API: #28, #27, #23 and #25. CI's backend tests had passed with #25's pip versions except for
  the one test below; psutil, croniter, pyotp and slowapi are used only through calls that the
  new versions keep.
- Closed unmerged, with the reason on each (ADR-0231): #26, a group of major versions of the web
  UI's toolchain on which `npm ci` failed (now `webui.svelte-5` and
  `webui.toolchain-major-versions`), and #24, the Playwright image without its npm package, let
  through by an ignore rule that named the image with its registry.
- Every CI run since the conversion had failed on
  `test_a_folder_removed_before_the_file_arrives_is_refused_before_any_of_it_either_way[unnamed]`:
  ext4 refuses an unnamed file in a removed folder with EPERM, which read as 403. Fixed for
  `files.upload` in #29, with a test that fakes ext4's answer: it failed on the
  device's Linux before the fix and passes after it, with 0 MiB written to the SD card.
- The CI workflow was disabled on GitHub (ADR-0230); its file stays, and the documents that said
  CI checks every pull request now say who runs `make check`.
- Fresh-context reviews: #29's found no bug; the order of its checks and three wordings were
  fixed, and a deleted btrfs subvolume keeps a link, so an upload into one is refused only at the
  link (`_removed`'s docstring). This change's review: the stale CI statements, the audit's reach
  and the test's registry check were fixed. ADR-0230 names ADR-0216 and ADR-0229 instead of
  superseding them: the workflow is off, not replaced, and both hold again once it is on.

### 2026-11-07: append-only audit log, device security, flash facts

- The owner merged pull request #22 and handed over the open owner actions.
- The audit log is append-only JSON lines with rotation (ADR-0224). 14 tests; seven mutations of
  the code (rewriting in place, the shift order, an early rotation, a reader without the lock,
  clearing that merges, reads on the event loop, no newline repair) each fail them. A
  fresh-context review found data loss in a failed clearing and a test that could not see a
  rewrite; both were fixed. Deployed and measured on the device (Dev device above).
- The dev device: SSH by key only, root's and the UNOS administrator's passwords rotated
  (ADR-0226); the rollback images and files of slice 2 deleted (0.57 MiB); the device's flash
  checked (ADR-0225); IPv6 exposure reviewed (ADR-0227); Docker's data root stays (ADR-0228);
  the base image's digest checked before the build (`deploy.digest-pinned-base-images`).
- The workspace's local Claude settings lost their password and push-to-main rules; the 21
  branches merged into main were deleted on GitHub; the main checkout was set up offline, with
  the git hooks on.
- The ledger gained the audit plan's missing entries and two from today's measurements
  (`architecture.sqlite-write-cost`, `device.ipv6-inbound-filter`).

### 2026-11-07: slice 2 on the device, device cleanup, transmission login, ADRs and the agentic conversion

- Slice 2 deployed to the device and verified there: the first start 12/12; a restart of the
  supervisor changed no file; the SMB flows with a temporary account and share 30/30 (signed
  sessions, read and write, a downgrade and a revocation during open sessions, a password change,
  a wrong password, anonymous sign-in refused, a new folder below the 0777 pool root refused); the
  final comparison 3/3. The device's testparm (Samba 4.22.6) accepted the rendered smb.conf.
- The device keeps no legacy state (ADR-0220): pool root 755, DATA moved to its own folder with
  the two guest-era files, transmission's data moved from the SD card to the pool, old data and
  images deleted with the owner's permission.
- transmission requires a web login (ADR-0219): reinstalled with a generated login kept in
  `dev.env`; its RPC answers 401 without it.
- Every decision is recorded as an ADR in `docs/adr/` (ADR-0001), and the repository follows the
  agentic patterns: the AGENTS.md files, GLOSSARY.md, CONTRIBUTING.md, `feature-list.json`, this
  journal, the skills in `.agents/skills/`, the guard hook, `make check`, `scripts/dev/` and
  `scripts/standin/`. docs/IMPROVEMENTS.md moved into `feature-list.json`; no backward
  compatibility (ADR-0207).
- The device tooling is `scripts/dev/` (ADR-0221 to ADR-0223). Its read-only checks that day
  wrote 0.953 MiB to the SD card in all, background included; its steps that change the device
  ran only against fixture trees. A read-only check found smbd unconfined by AppArmor
  (ADR-0162).
- The stand-in E2E runner is `scripts/standin/` (ADR-0215): the full run passed 354 tests,
  skipped 16 for what the stand-in lacks and failed none, in 6.8 minutes; with the two tests
  that the review asked for, 356 passed (the final run of the committed tree: 356 passed, 16
  skipped, 0 failed, 6.9 minutes). `make check` of that tree: 4320 backend tests passed, 118
  Linux-only skipped, 1 xfailed; svelte-check without errors; the build passed.
- With the owner's go, slice 2, the transmission fix and the conversion were committed as three
  commits on top of slice 1 and pushed; the slice-2 rollback images and files on the device were
  deleted (0.574 MiB written).
- After reviews by independent agents (ADR-0017) the ledger has 194 entries, 15 of them
  passing. `backup.restore` went back to false: a restore is a host-level change that has not
  run on the device. So did `security.request-limits` and `auth.login-rate-limit`: their tests
  assert step 2 only in a weaker form, and their notes say which test is missing. The steps
  that no test asserted got E2E tests: the sign-out of `auth.sign-in-and-out` and the download
  with an expired session of `files.browse-and-download`. `supervisor/tests/test_feature_list.py`
  lets an entry be dropped or reworded only with an ADR added since the branch's base that
  names it, and tests that rule on fixture ledgers in every run.

### 2026-11-06: Phase 1 slice 1, broken user flows and no needless system-disk writes

- Commit 9ac7b40, pushed: the file manager's download, delete, copy and move with keyboard access;
  streaming uploads; volumes; scrubs off the event loop; SMART on whole host disks; backup restore
  and encrypted uploads; temporary files in RAM; request limits; CI with ruff, pip-audit and the
  web UI checks.
- Deployed to the device: smoke 18/18 and the slice checks passed (files, volumes, SMART, backups,
  scheduled backups, request limits). The deploy wrote 38 MiB to the SD card.

### 2026-11-05: audit and Phase 0, security and robustness hardening

- An audit of the 1.0.0 code, then Phase 0, merged as pull request #21 (afd9c8e): HTTPS from a
  local CA, a JWT secret per installation, first-run setup instead of default credentials,
  administrator-only routers, WebSocket authentication with the first message, pools mounted by
  UUID, host commands through `src/host.py`.
- Deployed to the device: smoke 18/18, unmount and mount of the pool, and a reboot (the pool was
  mounted before Docker started). The owner delegated security decisions to the agent (ADR-0013).

### 2026-01-23 to 2026-02-09: initial build and release 1.0.0

- Built in cloud sessions merged as pull requests #1 to #20; 1.0.0 released on 2026-02-05 with
  the roadmap docs/IMPROVEMENTS.md (ADR-0008, superseded by ADR-0014); RAID5 and RAID6 replaced by
  RAID1C3 and RAID1C4 (ADR-0011).
