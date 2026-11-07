# Progress journal

Where the work stands, what runs on the dev device, and a dated log, newest first (ADR-0204). At
the start of a session read it together with `git log --oneline -10`; update it after each
significant step. Features and their state live in `feature-list.json`, decisions in `docs/adr/`.

## Now

- Branch `phase1-broken-flows`. Slice 1 is commit 9ac7b40, pushed and not merged; `main` is
  afd9c8e, the merge of pull request #21 (Phase 0).
- On top of slice 1, three commits with the owner's go, pushed (ADR-0208): slice 2 (SMB users,
  per-share permissions, guest access off by default), the transmission login fix, and the
  conversion to the agentic patterns (the ADRs, the AGENTS.md files, the ledger, this journal,
  the skills, the guard, `make check`, `scripts/dev/` and `scripts/standin/`). The owner opens
  the pull request (owner action 8).
- The branch `smtp-verify-tls` (bbb773b) is pushed and not merged: it makes e-mail notifications
  check the mail server's certificate (ADR-0045). The working tree and the dev device still call
  STARTTLS without that check (`system.notification-channels`).
- This checkout is not set up: there is no `supervisor/.venv`, no `.venv-lint` and no
  `webui/node_modules`, and the git hooks are not installed (`core.hooksPath` is unset). Until the
  owner runs `make setup` (owner action 7), `make check`, `make smoke`, `make e2e-local` and the
  hooks do not run here with their default tools.
- Feature work happens in worktrees (ADR-0210) whose base is the branch that holds AGENTS.md:
  `origin/main` once the conversion is merged; until then `origin/phase1-broken-flows`. The
  command (AGENTS.md, Start of a session):
  `git worktree add --no-track -b <id> ../unos-<id> <base>`; the owner runs `make setup` there.
  A worktree without `AGENTS.md` or `.claude/settings.json` is a stop: ask the owner.
- Next, after the owner's review of the pull request, in this order:
  1. The feature pass of the append-only audit log (`security.append-only-audit-log`), with the
     feature-pass skill.
  2. `device.scenario-checks-in-repo`: the device checks of slices 1 and 2 were scratch scripts,
     and the host-level entries they covered still need the re-check after a reboot of the
     deployed tree (ADR-0018).
  3. The owner's reboot (owner action 12), then that re-check with the scripts of step 2.

## Dev device

State on 2026-11-07. The ArmSoM Sige7 (Debian trixie); its address and logins are in `dev.env`
(ADR-0212). The system disk is the SD card mmcblk1, which cannot be replaced (ADR-0214).

- Runs the code of the branch's commits: the supervisor image was rebuilt on 2026-11-07 with the
  transmission fix, and the web UI is the one of the slice-2 deploy. The device checkout
  /opt/unos was not synced to the commits, to spare the SD card: `scripts/dev/deploy.sh status`
  finds 16 runtime files that differ only in comments, docstrings and texts (the ADR references
  in supervisor/src, a comment of deploy/docker-compose.yml, the final message of
  deploy/uninstall.sh, the test scripts removed from webui/package.json), plus the docs, tests
  and tooling. The next deploy's sync brings the checkout to the branch.
- Containers: unos-supervisor, unos-webui and transmission (with a web login, its data on the
  pool `data`).
- Images: deploy-supervisor:latest (aa02c3df1436) and deploy-webui:latest (e06fbf952aa9),
  lscr.io/linuxserver/transmission, alpine (the final backup of deploy/uninstall.sh) and
  caddy:2-alpine (the web UI's base). The supervisor's base image python:3.11-slim has no tag on
  the device (owner action 10).
- The slice-2 rollback images (`:pre-phase1b`) and files (/root/pre-phase1b) were deleted on
  2026-11-07 with the owner's go for the commits. /dev/shm/unos-rootfs, in RAM, is kept for
  `scripts/dev/linux-tests.sh`; a reboot removes it.
- Storage: the pool `data` (btrfs on /dev/sda) at /mnt/pools/data; the pool root is root:root
  755 (ADR-0220).
- Shares: smb.conf is rendered by UNOS, and the group unos-smb exists. The share DATA is served
  from /mnt/pools/data/shared (root:unos-smb 2770), and no account has access to it yet.
- Accounts: besides the administrator, the regular account `test` stays for checks of the
  regular user's view until the owner deletes it (ADR-0054).
- AppArmor: a read-only check on 2026-11-07 found AppArmor enabled in the kernel, no AppArmor
  utilities installed, no smbd or samba profile loaded, and smbd unconfined (ADR-0162).
- SD-card writes measured in this phase: the slice-1 deploy 38 MiB (2026-11-06); the slice-2
  deploy about 45 MiB of image layers and its checks 4 MiB; the transmission image pull about
  177 MiB; the supervisor rebuild 11 MiB; the transmission reinstall 6 MiB; the device checks of
  the conversion 0.953 MiB (2026-11-07, 08:20 to 09:06, background included); deleting the
  rollback images 0.574 MiB. By 09:06 on
  2026-11-07 the system disk had written 1391.9 MiB in the 37.5 h since boot, deploys included.
  Idle, the device writes 16 KiB (32 sectors) a minute (2026-11-07). An API login writes about
  0.19 MiB and a later API call about 0.02 MiB (`security.append-only-audit-log`,
  `deploy.container-logs-off-the-system-disk`).

Open owner actions:

1. Rotate the device's root password and the UNOS administrator's password: CLAUDE.md once
   published them, and although they are gone from the repository's files, both are in its git
   history (ADR-0024).
2. Switch the device's SSH to key-only logins (`device.key-only-ssh`).
3. Keep the GitHub repository private until both passwords are rotated and the git history is
   cleaned (ADR-0024).
4. Revoke the classic GitHub token and add the device's key as a read-only deploy key
   (ADR-0053). Until then `git fetch` and `deploy/update.sh` cannot reach GitHub from the device;
   deploys from the workstation do not need it.
5. Check the router's inbound IPv6 filter for the device (ADR-0095).
6. Review the allow rules of the workspace's local Claude settings (`.claude/settings.local.json`
   in the folder above the repository): they pre-approve sshpass and `git push origin main` on
   the device.
7. Run `make setup` in this checkout; it needs the network (ADR-0217). It makes the virtual
   environments, runs `npm ci`, installs Playwright's Chromium for @playwright/test 1.57 (build
   1200) and turns the git hooks on. Until that Chromium is installed, `make e2e-local` needs
   `E2E_CHROMIUM_PATH` naming another Chromium build.
8. Open the pull request of `phase1-broken-flows` (its commits are pushed), review and merge it.
9. Turn on branch protection for `main` (`ci.branch-protection`).
10. Tag or pin the supervisor's base image python:3.11-slim on the device before the next
    supervisor build: without it a build may pull a new base and rerun apt-get and pip, hundreds
    of MiB on the SD card (`deploy.digest-pinned-base-images`).
11. Ask for the first real deploy and watch it: the sync, build, up and rollback steps of
    `scripts/dev/deploy.sh` have run only against fixture trees so far (ADR-0223).
12. Reboot the device once the host-level checks are scripts, for the re-check of ADR-0018. After
    the reboot, make the RAM rootfs /dev/shm/unos-rootfs again with the command that
    `scripts/dev/linux-tests.sh` prints (it runs docker create, about 2 MB on the SD card).
13. Give an account access to DATA: allow SMB for a regular user, then add the user to the
    share's access list.
14. Decide whether Docker's data root moves to the hard disk (`device.docker-data-root-on-hdd`).

## Log

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
