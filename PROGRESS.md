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
  reboot. The dev device runs main eceead5, deployed on 2026-11-09 at 21:07 UTC.
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
     `storage.scrub` again on the scenario check scrub around the reboot of 21:12 UTC (Log); the
     others need a device check first or, as `storage.pool-health`, a step the re-check could
     not show.

## Dev device

State on 2026-11-10, 19:15 UTC. A UGREEN NAS board with an RK3588,
running Armbian; its address and logins are in `dev.env` (ADR-0212).

- Flash (ADR-0225): the system runs from the SD card `mmcblk1` (SanDisk, 32 GB). The soldered
  eMMC `mmcblk0` (Samsung, 32 GB) keeps the vendor's firmware: 0 MiB written since boot, its own
  wear estimate 0 to 10%. Nothing writes to it.
- SSH by key only, root by the workstation's key `~/.ssh/unos_dev_ed25519`; root's and the UNOS
  administrator's passwords were rotated and live only in `dev.env` (ADR-0226).
- Runs main f68c7c1 (session revocation and log rotation), deployed on 2026-11-10 at 18:50 UTC:
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
- Containers: unos-supervisor, unos-webui, transmission (web login, data on the pool `data`).
  Images: deploy-supervisor:latest (`11729d7d...` of 2026-11-10, 17:55 UTC, `ba92f8e6...` as
  `:rollback`), deploy-webui:latest (`232cf5db...` of 17:55 UTC, `44bd3f12...` as `:rollback`),
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
- Storage: the pool `data` (btrfs on /dev/sda) at /mnt/pools/data, root:root 755 (ADR-0220).
  The share DATA is served from /mnt/pools/data/shared (root:unos-smb 2770); no account has
  access to it yet. The regular account `test` stays for checks of a regular user's view.
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
5. Make the supervisor image's root filesystem in the dev device's `/dev/shm` again, gone since
   the reboot (the command that `scripts/dev/linux-tests.sh` prints; it runs a container and
   writes about 2 MB to the SD card): the Linux-only tests wait for it, `test_share_repair.py` of
   `shares.rollback-era-safeguards` among them.

## Log

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
