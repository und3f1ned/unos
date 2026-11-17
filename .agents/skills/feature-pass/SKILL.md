---
name: feature-pass
description: Carry one feature-list.json entry to passes true in one pass, from the plan and the failing tests to the reviewed and checked user scenario, the ledger, PROGRESS.md and the ADRs. Use when asked to implement, fix or verify a feature-list.json entry, or to take on the next one.
---

# One feature per pass

A pass takes one entry of `feature-list.json` from `passes: false` to `passes: true`
(ADR-0203). Its rules:

- `passes` turns true only after every step of the entry passed as a user scenario: tests in
  `verified_by` assert each step, the E2E specs on the local stand-in cover the steps in the web
  UI, and an entry whose steps make a host-level change (the term in GLOSSARY.md) has also passed
  a device check and a re-check after a reboot of the deployed tree (ADR-0018). A skipped test
  proves nothing.
- A pass changes `passes`, `verified_by` and `notes`. A changed description or changed steps are
  a requirements change: an ADR added on this branch records it and names the entry's id, and
  the entry's `adr` lists that ADR. Dropping an entry needs an ADR added on this branch that
  names its id. `supervisor/tests/test_feature_list.py` checks both against the ledger and
  `docs/adr/` at the branch's merge base with `main`, and skips where that base has no ledger.
  Until the ledger is merged into `main`, neither a local run nor CI catches a dropped or
  reworded entry (`ci.ledger-changes-checked`) unless you name the base yourself:
  `FEATURE_LIST_BASE=$(git merge-base HEAD <base>) make test-fast`, with the worktree's base
  (AGENTS.md, Start of a session). The review of step 7 also reads the diff of
  `feature-list.json`.
- One entry per pass, worktree, branch and pull request (ADR-0210). What else you notice becomes
  a new entry with `passes: false`, or a line in PROGRESS.md, outside this diff.
- Commits and pushes happen when the owner asks (ADR-0208).

## Steps

1. **Pick the entry.** The one the owner or PROGRESS.md (Now) names; otherwise propose two or
   three entries with `passes: false` and let the owner choose. Read the entry, its ADRs, the
   AGENTS.md of its area and `git log --oneline -10`. Result: you can quote the entry's id and
   steps, and you work in a worktree and on a branch of its own (AGENTS.md, Start of a session).

2. **Check the baseline.** `make smoke` and `make check-fast`; `make check` before a large
   change. Result: a green run, or the red part written in PROGRESS.md (Now) and told to the
   owner before you change anything.

3. **Plan in plan mode.** Map each step of the entry to what will assert it: a backend test in
   `supervisor/tests/` through the API, an E2E test in the spec of the page in `webui/tests/`,
   the device check of a host-level step. List the files to change, the choices that no ADR
   settles (ask the owner, then the adr skill), and what stays out of scope. Result: the owner
   approved a plan in which every step has its test or check.

4. **Write the failing tests first** (ADR-0186). Run a backend file with
   `make test TESTS=tests/<file>` (`PYTHON=<interpreter>` picks another Python, `TEST_WORKERS=0`
   runs in one process); never run pytest directly. Run a spec with
   `scripts/standin/e2e.sh tests/<name>.spec.ts`. Result: the new tests fail on the current code,
   for the reason the entry describes (quote the failing assertion). From now on they stand:
   weakening one to get green needs the owner's yes.

5. **Make them pass** with the simplest change that meets the entry fully: the error paths, the
   role checks (ADR-0030), no needless writes to the system disk (ADR-0214), and every caller in
   the web UI updated with an API change (ADR-0207). Result: the new tests pass.

6. **Run the gate.** `make check`; after a change to `webui/` or to an API the web UI calls, also
   `make e2e-local` (the standin-e2e skill). Result: both exit 0, nothing failed or was flaky, and
   the entry's E2E tests ran instead of being skipped.

7. **Get the change reviewed in a fresh context** (ADR-0017). Give a reviewer without this
   session's history (a subagent, or a new session with mattpocock-skills:code-review) the diff,
   the entry and its ADRs, and ask it to refute the change: a step that is not met or not
   asserted, a counterexample, an error path or a role check left out, a test weakened or fitted
   to the code, a reworded entry. Fix each confirmed finding and run step 6 again. Result: every
   finding is fixed, or recorded with its reason for the Log entry of step 10.

8. **Check a host-level entry on the device.** For an entry whose steps make a host-level change
   (GLOSSARY.md): the owner starts the deploy with /deploy-device (the model cannot load
   that skill), then run the entry's check, which the table below names: a scenario check
   (`scripts/dev/scenario.sh <area>`, ADR-0304), which changes the device and so runs when the
   owner asks for it, or, where the table says by hand, the device-checks skill for the steps
   that the entry's notes give. The re-check after a reboot waits for the owner's reboot and
   runs the same check again. Report the SD MiB of every step. Result: for each host-level
   step, a command and its result from the device, before and after the reboot.

9. **Update the entry.** Set `passes: true` only when steps 6 and 7 hold, and step 8 for a
   host-level entry; list the tests that assert the steps in `verified_by`, and leave the
   description and the steps as they are. A step that could not be checked keeps
   `passes: false`, and `notes` says what is missing. Run `make test-fast`. Result:
   `tests/test_feature_list.py` passes, and `git diff feature-list.json` changes this entry only.

10. **Record the pass.** PROGRESS.md gets Now, Dev device after a deploy, and a dated Log entry
    that names the review's findings left unfixed and why; each decision gets an ADR (the adr
    skill); new terms go into GLOSSARY.md; the docs describe the changed behaviour (ADR-0061).
    Result: `git status --short` lists them.

11. **Commit when the owner asks**, in Conventional Commits in English with the trailer
    `Feature: <id>` (ADR-0209). Result: `git log -1 --format='%(trailers:key=Feature,valueonly)'`
    prints the id, and the commit-msg hook accepted the message.

## Device checks of host-level entries

The check of each host-level entry (GLOSSARY.md): every such entry of `feature-list.json` is in
one row, and the pass that makes an entry host-level adds it. `supervisor/tests/test_dev_scenarios.py`
fails for an entry whose notes name the reboot rule of ADR-0018 and that is in no row, and for an
id that the ledger does not have; a row may also name entries that are not host-level but that its
check covers. A scenario check prints PASS or FAIL for each step with its SD MiB, removes what it
made and compares the device with how it found it (`scripts/dev/scenario.py --help` lists its
steps). What a scenario check leaves out of an entry is in parentheses and is checked by hand.
When a check by hand is repeated, it becomes a step or an area of `scripts/dev/scenario.py`.

Each scenario check removes what it makes, so its run after a reboot shows that the entry's
scenario works after the reboot, not that something made before the reboot is still there: where
an entry's steps say so ("survives a reboot", "after a reboot the items are still listed"), that
part is checked by hand, with the steps of the entry's notes.

| Check | Entries |
| --- | --- |
| `scripts/dev/scenario.sh files` | `files.protects-volumes-and-mounts` (not a bind mount below a folder), `files.delete-to-trash` (not Empty trash, the 30-day expiry, the listing after a reboot, a snapshot's 409 or a trash that another user made), `files.rename` (not the names it refuses), `files.browse-and-download`, `files.upload`, `files.organize` |
| `scripts/dev/scenario.sh volumes` | `storage.volumes` (not a pool mounted read-only, nor a volume that an app, a Docker volume or an NFS export uses), `storage.restore-copies-deletable` (the copy of a volume's snapshot through the API, refused while a share uses it; not the copy of a pool's snapshot, nor a container or an NFS export that uses a copy, which the backend tests show, nor the Snapshots page itself, which the E2E specs show on the stand-in), `storage.volume-quota-usage` (the list's use against a quota of 1 GiB and the use over time, or that the list says quotas are off; not the alerts at 80, 90 and 95%, nor the history across a reboot, which the entry's notes check by hand, nor the dialog itself, which the E2E specs show) |
| `scripts/dev/scenario.sh smart` | `storage.smart` (not the Scheduler's SMART test task), `storage.commands-time-out-cleanly` (the SMART answers that its device check names; not the scrub, the stats reset, the Scheduler's Run now or the supervisor's log, and no command times out on purpose), `storage.disk-list` (the disks listed without the system disk; not their model, serial and badges, nor the page's error), `storage.disk-spindown` (the system disk's refusal, the shortest idle timeout of the pool's disk, its own automatic offline data collection off while it has the timeout and as found afterwards, its standby and the SMART reads that leave it asleep, SKIPs on a host without hdparm, for a disk whose standby timer only `/etc/hdparm.conf` sets, and for one with UNOS's timeout whose offline data collection is on unrecorded; whether the drive keeps that collection off after a reboot, which the check's first step shows on its run after the reboot check; the rule applied by udev after a reboot by hand, with the steps of its notes; not a disk plugged in again, nor the Storage page itself, which the E2E specs show) |
| `scripts/dev/scenario.sh backups` | `backup.restore` (its refusals of a missing backup and of a missing or wrong passphrase; not a restore, its 507 or a second restore), `backup.create-and-download` (not the download) |
| `scripts/dev/scenario.sh smb` | `shares.smb-accounts`, `shares.smb-share` (not the list of offered folders, nor the 409 below a pool root that others can write, which the device's pool root of mode 755 cannot show), `shares.per-user-access`, `shares.guest-off-by-default`, `shares.password-follows-unos`, `shares.samba-runtime-writes`, `shares.smb-login-protection` (step 1; not the end of the lock after 5 minutes, nor a count that starts again after 5 minutes without a failure, which the check does not wait for), `shares.password-changes-only-through-unos` (step 1, with `smbpasswd -r` as the SAMR change that Windows' Change a password asks for; not a Windows client, which stays open in the entry until the owner tries one, ADR-0342; step 2 is the Account page's, which the E2E specs show), `files.group-writable-entries-in-shares`, `auth.user-rename` (a rename of the check's user with SMB on, its share access and its sign-ins; not the refusals of a name or the failure paths, which the backend tests show, nor the edit dialog, which the E2E specs show; Samba's refusal of the old name only where the device has no guest share), `shares.per-share-protocol-options` (the sections' lines with testparm, encryption with an SMB 2.1 and an encrypting smbclient, smbd's answer for the shadow copies of the new volume before its first snapshot, without `.snapshots`, recorded (any answer passes), two snapshots of a volume listed with allinfo, the file, overwritten after both, opened in the one left after the other's Delete with exactly its bytes from before (the SHA-256 of the copy smbclient gets), and neither listed nor opened after both Deletes, smbd refusing the opens while the session goes on and the volume keeping `.snapshots`; a Mac's backup, Windows' Previous Versions and the testparm of every combination by hand, with the steps of its notes) |
| `scripts/dev/scenario.sh smbconf` | `shares.smb-conf-and-status` (not the staged file's testparm and the atomic install, which the backend tests show, nor Samba's reload of a new share's section, which `scripts/dev/scenario.sh smb` shows by serving its share; the status panel itself is the E2E specs' on the stand-in, ADR-0215), `shares.edit-and-delete` (not what the dialog sends, asks first or shows after a 409, which the E2E specs show), `shares.keep-every-replaced-copy` (step 1: the check's two copies, the first kept beside the second and named as an earlier copy, ADR-0362; not step 2, the import backups, which need an import of a new database: the backend tests show it) |
| `scripts/dev/scenario.sh shares` | `shares.acl-aware-parent-check` (step 3: a share folder in a sticky folder of another account, refused with 409, and a folder of another account in a sticky folder of root, whose share gets the warning and the status line and whose Repair is refused; steps 1 and 2 are the backend tests'), `shares.repair-permissions` (not app data, snapshots and mount points, which the backend tests show, nor the Shares page's display of the last run, which the E2E specs show; the ACL entry only on a device with setfacl), `shares.repair-in-background` (step 1: the start answered 202 running and its end, which GET then names; not a large share, nor a second start while one runs or the stop, which the check's share, repaired in moments, cannot show and the backend tests show; not the progress on the Shares page, which the E2E specs show), `shares.self-activation` (the Account page's API, not the page itself, which the E2E specs show on the stand-in; not a two-factor code, which the backend tests show; a password over 72 bytes UNOS itself refuses, so SMB's reason for it cannot be shown), `shares.read-only-guest-share` (not the dialog's warning, which the E2E specs show; not a Windows client, and where the device has other guest shares, not the mandatory signing after the last one) |
| `scripts/dev/scenario.sh scrub` | `storage.scrub` (not the pool card's progress bar and texts, which the E2E specs show on route fakes, nor a pool that is not mounted, whose card offers no Scrub; the last scrub that its first step names after a reboot is the forgotten result of ADR-0171), `storage.scrub-needs-mounted-pool` (not that no btrfs command runs for the 409, which the backend tests show) |
| `scripts/dev/scenario.sh balance` | `storage.pool-balance` (a balance with the usage limit of 20% of chunks that the check leaves about 12% full, its status with the time left, the unit on the host, a scrub and a second balance refused, pause, resume and cancel; not the pool card itself, which the E2E specs show on route fakes, nor a full balance, a read-only pool or a balance across a restart of the supervisor or of the host, which the entry's notes check by hand) |
| `scripts/dev/scenario.sh offsite` | `backup.off-device-copies` (a disk target in a folder of the pool, not a USB disk or an S3 store, nor one refused on the system disk; not a scheduled run at 03:00, a run skipped beside a running copy, a step that fails or a restic stopped at its timeout, which the backend tests show with a fake restic; not the section of Backup & Restore, which the E2E specs show on the stand-in) |
| `scripts/dev/scenario.sh restore` | `shares.restore-keeps-smb-with-proof` (a backup of this device made moments before, restored twice; not a restored row with the uid of a live account of another name, which only a backup of another installation or a crafted archive has, nor a backup taken before the shares were imported, which the backend tests show, ADR-0341), `backup.no-old-backup-import` (the backup by hand and the scheduled one hold no smb.conf, and a restore of the first keeps the shares and smb.conf byte for byte; not the refusal of a backup without the SMB tables, which the backend tests show) |
| `scripts/dev/scenario.sh hardware` | `apps.rk3588-hardware-acceleration` (steps 1 and 2 in part: what the App Store offers Jellyfin, Immich's server and Immich's machine learning on the RK3588, the device nodes of the video hardware the host has, the NPU's render node and the RKNPU driver's version; the installs of Jellyfin and Immich, whose images only the owner pulls (ADR-0211), Jellyfin's ffmpeg decoding and encoding with rkmpp in the container, Immich's models loaded on the NPU with `scripts/dev/immich-ml-probe.py` and its log, the NPU's load and the uninstalls by hand, with the steps of its notes) |
| `scripts/dev/scenario.sh all` | `device.scenario-checks-in-repo`, `deploy.arm64-bpi-w3` (on the dev device, a BPI-W3; the disk list, the pool's mount options after a reboot, check.sh's thermal lines and the dashboard's temperature by hand, with the steps of its notes; a fresh `deploy/install.sh` on a second microSD card and an NVMe pool are open), `deploy.riscv64-bpi-f3` (on a BPI-F3 installed with `deploy/install.sh`, with a SATA pool; the pool's TRIM, the udev rule and the dashboard's temperature by hand, with the steps of its notes) |
| `by hand`: the steps of the entry's notes, with the device-checks skill | `security.https-local-ca`, `security.host-firewall`, `security.server-limits`, `storage.create-pool`, `storage.mount-and-unmount`, `storage.pool-health`, `storage.forget-pool`, `storage.snapshots`, `storage.import-existing-pool`, `storage.hot-plugged-disks-smart`, `shares.smb-conf-import`, `shares.smb-discovery`, `shares.reapply-on-pool-unmount`, `shares.smb-auth-audit-logging`, `shares.nfs-exports`, `shares.nfs-exports-parser`, `shares.group-access` (a group's read and read-write access over smbclient, a member added and removed, a user's own level beside the group's, the group's deletion and no host group, and the group after a reboot, with the steps of its notes), `apps.install-on-a-pool`, `apps.compose-stacks` (Immich's install, its containers, network, mounts and password file, its lifecycle, an uninstall and a reinstall with the same database, the SD MiB of each step and the disk's sleep, with the steps of its notes), `apps.install-progress` (a small app's and Immich's install followed through the job's API and the App Store's card, a reload during the pulls, a second install refused, and a restart of the supervisor during a pull and during the readiness wait, with the steps of its notes), `apps.no-legacy-default-pool`, `apps.transmission-login`, `apps.manage-installed`, `apps.containers-and-images`, `apps.docker-calls-off-event-loop`, `apps.smb-group-option`, `system.temperatures`, `system.network`, `system.hostname-and-time-zone`, `system.power`, `system.ups-monitoring`, `system.logs-viewer`, `system.scheduler-time-zone`, `system.health-details-and-metrics`, `system.network-disable-confirmation`, `system.telegram-from-the-device`, `deploy.install-script`, `deploy.update-script`, `deploy.uninstall-script`, `deploy.uninstall-samba-restore`, `deploy.health-checks-and-start-order`, `deploy.no-legacy-network-recreate`, `deploy.no-legacy-smb-conf-recognition`, `device.ipv6-inbound-filter`, `device.key-only-ssh`, `deploy.container-logs-off-the-system-disk` (100 calls with `scripts/dev/sd.sh measure`), `webui.no-http-polling` (Auto of System > Logs showing a line that `logger` writes on the host and leaving no `journalctl --follow` behind, and a scrub's progress on the Storage page without a reload) |

## Gotchas

- When a re-check of a passing entry fails, or a review finds a step that its tests do not
  assert as written, set its `passes` back to false at once, say in `notes` what is missing and
  say so in PROGRESS.md. That is a regression, not a requirements change.
- An entry too big for one pass is split by an ADR added on the branch: the new entries name it
  in their `adr`, and it names the dropped one.
- Backend tests alone are a checkbox without a check for an entry with steps in the web UI: those
  steps need E2E tests that assert what the page shows, not only that it renders.
- Playwright runs only against the stand-in (ADR-0215). What the stand-in lacks is skipped there
  with `E2E_STAND_IN=1` and needs the device check instead.
- A test that is an expected failure or a skip on macOS, such as the Linux-only paths of uploads
  and host tools, proves its step only on Linux: run it with `scripts/dev/linux-tests.sh`.
- Backend test runs in several worktrees go at once: each pytest process has temporary paths of
  its own (ADR-0383). A new test writes only below its `tmp_path`, never to a fixed path, and
  passes in parallel workers (`make test`) as well as alone (`TEST_WORKERS=0`).
