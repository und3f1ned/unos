# Progress journal

Where the work stands, what runs on the dev device, and a dated log, newest first (ADR-0204). At
the start of a session read it together with `git log --oneline -10`; update it after each
significant step. Features and their state live in `feature-list.json`, decisions in `docs/adr/`.

## Now

- `main` holds the first feature batch (pull requests #32 to #39, ADR-0232 to ADR-0246):
  rename in the file manager, SMART data and replace-soon marks, the Audit Log page, two-factor
  recovery codes, encrypted scheduled backups, snapshots of volumes with volume quotas in the web
  UI, the network interface's disable confirmation with its automatic revert, and power actions
  on the host with an Updates tab that works. Before them: the audit log, the device's security,
  the upload fix for ext4, Dependabot's Actions and pip updates (#23 to #30).
- The batch passed on the dev device (2026-11-07). A magic packet from the workstation did not
  start the device after a shutdown: its board cuts the network card's power (ADR-0247), so only
  its power button starts it again; a reboot is the remote restart.
- The CI workflow is off on GitHub (ADR-0230): `make check` is the only gate, and
  `scripts/dev/linux-tests.sh` runs the Linux-only tests. The weekly Audit workflow stays on.
- The local venvs still hold the pip versions from before #25; `make setup` updates them (the
  network). The device has not had them yet either: the next supervisor deploy rebuilds the pip
  layer.
- The main checkout is set up: `supervisor/.venv`, `.venv-lint` and `webui/node_modules` were
  copied from environments verified against the pins (with `setup.sh`'s stamps), and the git
  hooks are on. Only Playwright's Chromium 1200 is missing: the workstation's network filter
  blocks the download for agents, so `make e2e-local` needs `E2E_CHROMIUM_PATH` (the cached
  `chromium_headless_shell-1234`).
- Feature work happens in worktrees (ADR-0210) from `origin/main`:
  `git worktree add --no-track -b <id> ../unos-<id> origin/main`. A worktree without
  `AGENTS.md` or `.claude/settings.json` is a stop.
- Next, in this order:
  1. `device.scenario-checks-in-repo`: the device checks of slices 1 and 2 as scripts in
     `scripts/dev/`, each reporting its SD writes.
  2. The owner's reboot of the device, then the re-check of the host-level entries with those
     scripts (ADR-0018).
  3. `deploy.digest-pinned-base-images` and `architecture.sqlite-write-cost` (the SD card).

## Dev device

State on 2026-11-07, 20:45 UTC. A UGREEN NAS board with an RK3588,
running Armbian; its address and logins are in `dev.env` (ADR-0212).

- Flash (ADR-0225): the system runs from the SD card `mmcblk1` (SanDisk, 32 GB). The soldered
  eMMC `mmcblk0` (Samsung, 32 GB) keeps the vendor's firmware: 0 MiB written since boot, its own
  wear estimate 0 to 10%. Nothing writes to it.
- SSH by key only, root by the workstation's key `~/.ssh/unos_dev_ed25519`; root's and the UNOS
  administrator's passwords were rotated and live only in `dev.env` (ADR-0226).
- Runs main cce0def (the first feature batch, #32 to #40), deployed on 2026-11-07 at 20:29 UTC,
  with the supervisor rebuilt once more by this branch's `deploy.sh` to carry its version. The
  device checkout `/opt/unos` equals main's tree; the deploy's backup and rollback copies are in
  the main checkout's `tmp/dev/`. Rebooted from the API at 20:36 UTC.
- Containers: unos-supervisor, unos-webui, transmission (web login, data on the pool `data`).
  Images: deploy-supervisor:latest, deploy-webui:latest, lscr.io/linuxserver/transmission,
  alpine (the final backup of `deploy/uninstall.sh`) and caddy:2-alpine (the web UI's base).
  The slice-2 rollback images and files were deleted on 2026-11-07.
- The audit log is `/data/audit/audit.jsonl` (ADR-0224); the old `/data/audit_log.json` was
  deleted.
- Storage: the pool `data` (btrfs on /dev/sda) at /mnt/pools/data, root:root 755 (ADR-0220).
  The share DATA is served from /mnt/pools/data/shared (root:unos-smb 2770); no account has
  access to it yet. The regular account `test` stays for checks of a regular user's view.
- Network: global IPv6 from a Hurricane Electric tunnel prefix; the services listen on every
  IPv6 address and the device filters nothing itself (ADR-0227). Samba admits only private,
  unique-local and link-local addresses. smbd runs unconfined by AppArmor (ADR-0162).
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
3. The reboot of the device for the re-check of ADR-0018, once `device.scenario-checks-in-repo`
   is done; afterwards the RAM rootfs of `scripts/dev/linux-tests.sh` is made again.
4. Who gets access to the share DATA (an SMB account for a regular user).
5. Whether the services are used from outside over IPv6 (`device.ipv6-inbound-filter`).

## Log

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
