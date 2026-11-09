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
  reboot, and the scenario check `smbconf` that runs those checks since (ADR-0309). The dev
  device runs main 12483dd, deployed on 2026-11-09 at 07:41 UTC.
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
     12:22 UTC that day (Log); the others need a device check first or, as
     `storage.pool-health`, a step the re-check could not show.

## Dev device

State on 2026-11-09, 12:25 UTC. A UGREEN NAS board with an RK3588,
running Armbian; its address and logins are in `dev.env` (ADR-0212).

- Flash (ADR-0225): the system runs from the SD card `mmcblk1` (SanDisk, 32 GB). The soldered
  eMMC `mmcblk0` (Samsung, 32 GB) keeps the vendor's firmware: 0 MiB written since boot, its own
  wear estimate 0 to 10%. Nothing writes to it.
- SSH by key only, root by the workstation's key `~/.ssh/unos_dev_ed25519`; root's and the UNOS
  administrator's passwords were rotated and live only in `dev.env` (ADR-0226).
- Runs main 12483dd, deployed on 2026-11-09 at 07:41 UTC: the supervisor built from its runtime
  files (the write-ahead log, ADR-0306) on the base that ADR-0305 pins; the web UI's image is
  the one built for main 78769b4 on 2026-11-08 (the runtime files of 36a6c1b), with the command
  of ADR-0307 since 01:18 UTC on 2026-11-09. The device checkout `/opt/unos` equals the tree of
  main 12483dd; main's later commits change no runtime file (`deploy.sh status`, 12:16 UTC).
  The deploys' backup and rollback copies are in the main
  checkout's `tmp/dev/`. The device has no `unos` unit: `deploy.sh up` starts its containers, and
  at boot dockerd does, by their restart policy. Rebooted from the API twice on the morning of
  2026-11-08, for the reboot checks of the IPv6 filter, by the owner at about 22:57 UTC that day
  for the re-check of ADR-0018 (the boot of 22:59:48 UTC; it answered again at 23:00), and from
  the API at the owner's word ("permission granted") at 07:16:57 UTC on 2026-11-09 (the boot of
  07:17:13 UTC; ssh answered by 07:17:34), and at 12:22:52 UTC that day for the SMB steps
  checked by hand, at the owner's word ("Yes, check") (the boot of 12:23:08 UTC by `uptime -s`;
  ssh answered by 12:23:24, both containers healthy by 12:23:37). The prune before the reboot of
  07:17 UTC deleted the 11 dangling images (the old builds' layers) and reclaimed only 134.9 kB,
  where this file had said about 351 MB.
- Containers: unos-supervisor, unos-webui, transmission (web login, data on the pool `data`).
  Images: deploy-supervisor:latest (`a32d5759...` of 2026-11-09, `97728ea0...` as `:rollback`),
  deploy-webui:latest (`d77b0aa7...`, likewise), lscr.io/linuxserver/transmission, alpine (the
  final backup of `deploy/uninstall.sh`) and caddy:2-alpine (the web UI's base: Caddy 2.10.2 of
  January 2026, `sha256:f2b257f2...`). The slice-2 rollback images and files were deleted on
  2026-11-07.
- Docker 29.1.5 with the containerd image store. The supervisor's base, python:3.11-slim
  `sha256:65bd3d9f...` (Python 3.11.18), is in BuildKit's build cache, not an image: the pins of
  ADR-0305 are these two digests. The supervisor builds of 2026-11-08, 22:28 UTC, and
  2026-11-09, 07:41 UTC, started from it: every step of the first CACHED, of the second all but
  `COPY src/`.
- The audit log is `/data/audit/audit.jsonl` (ADR-0224); the old `/data/audit_log.json` was
  deleted.
- Storage: the pool `data` (btrfs on /dev/sda) at /mnt/pools/data, root:root 755 (ADR-0220).
  The share DATA is served from /mnt/pools/data/shared (root:unos-smb 2770); no account has
  access to it yet. The regular account `test` stays for checks of a regular user's view.
- Scenario checks (ADR-0304): `scripts/dev/scenario.sh all` passed on 2026-11-08 at about 17:15
  UTC, at 22:33 UTC after the deploy and at 23:03 UTC after the reboot, and on 2026-11-09 at
  07:43 UTC after the deploy of main 12483dd, and left none of its users, shares, volumes,
  folders or backups; `scripts/dev/scenario.sh smbconf` (ADR-0309) passed at 13:22 UTC that
  day and, after its review's fixes, at 13:46 UTC, and left the device as it found it. The pool's
  trash
  `/mnt/pools/data/.unos-trash` (made at 00:05 UTC that night) is empty. The first run's short
  self-test of sda completed without error, as the two before it; sda's SMART status: PASSED.
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

## Log

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
- REVIEW_AND_CHECK

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
