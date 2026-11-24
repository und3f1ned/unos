---
name: device-checks
description: Check the UNOS dev device (health, API answers, Samba's real testparm, the backend tests on Linux in the device's RAM, read-only; and, when the owner asks, the scenario check of an area, which makes temporary things and removes them) and report the system disk's writes of each check. Use to verify a deployed change, behaviour the local stand-in cannot provide, Linux-only tests before a push (ADR-0462), or the host-level steps of an entry before and after a reboot (ADR-0018).
---

# Checks on the dev device

The device runs from its eMMC, a soldered chip that wears with every write, and its SD card
holds the SD standby, changed only through its API (ADR-0214, ADR-0471, ADR-0488), and Docker keeps its data on the pool (ADR-0486). Run every check from the repository root, through
`scripts/dev`:

- `check.sh`, the API relay of `api.py`, the testparm shim `bin/testparm`, `linux-tests.sh`
  and the commands of the scenario checks (but pdbedit) run on the device in a private mount
  namespace with the root filesystem read-only and noatime (ADR-0221), and keep scratch files
  in RAM (`/dev/shm`). A command you give `ssh.sh` runs as it is.
- Only `deploy.sh` (every step), `linux-tests.sh`, `ipv6-filter.sh` (install and remove) and
  `scenario.sh` take the device lock (ADR-0210); a second run waits. `check.sh`, `api.py`,
  `ssh.sh`, `sd.sh` and the testparm shim take none.
- `check.sh`, `linux-tests.sh`, `deploy.sh`, `ipv6-filter.sh` (install and remove), `scenario.sh`
  (for each check, and each step's MiB in its line) and `sd.sh measure` print
  `SD writes <label>: <MiB> MiB (<sectors> sectors)`, the system disk's writes. `ssh.sh`,
  `api.py` and the testparm shim print nothing of the kind: run them under
  `scripts/dev/sd.sh measure <label> -- <command>`, as the steps below do.
- Every script reaches the device through `ssh.sh`, which shares one SSH connection per device
  (ADR-0477): over it, a call proves no new login, since a firewall lets the connection through
  as established. Run `scripts/dev/ssh.sh --close`, after which the next call logs in anew,
  before a check that the device takes a new SSH login (step 1, and the LAN checks after a change
  of its firewall or network, also within a revert window), before the owner reboots the device
  and before a change of `dev.env`'s SSH settings.

Settings come from `dev.env` (mode 600, ADR-0212); in a git worktree without one, the scripts
use the main checkout's. Output shows secrets as `<redacted>`.

## Steps

1. **Reach the device.** After `--close` (above), so that it is a new login:
   `scripts/dev/sd.sh measure ssh -- scripts/dev/ssh.sh true`.
   Result: exit status 0 and `SD writes ssh: 0.000 MiB`. On `no dev.env` or `mode 644`, do
   what the message says; the passwords in `dev.env` are the owner's to fill in.
   `Host key verification failed` after a reinstall of the device: ask the owner to remove the
   old key (`ssh-keygen -R <host>`).

2. **Check its health.** `scripts/dev/check.sh`. Result: `check: OK`, exit status 0, the lock
   holder if a deploy or a test run is in progress, smbd, wsdd2 and avahi-daemon active and nmbd
   inactive and disabled (ADR-0275), the inbound IPv6 filter active and loaded (ADR-0301), and its
   own `SD writes check.sh` line.

3. **Ask the API.** `scripts/dev/sd.sh measure api -- scripts/dev/api.py GET /system/info` (any
   path under `/api/v1`, query parameters with `--param key=value`). Result: `HTTP 200`, the
   JSON body and `SD writes api: ...`. POST, PUT, PATCH and DELETE change the device: only for
   a check the owner asked for, and undo what you create.

4. **Use Samba's own parser.** From the repository root:
   `PATH="$PWD/scripts/dev/bin:$PATH" scripts/dev/sd.sh measure testparm -- make test TESTS=tests/test_smbconf_testparm.py TEST_WORKERS=0`
   (add `PYTHON=...` for another interpreter than `make setup`'s venv). `make test` runs pytest
   in `supervisor/`, here in one process (`TEST_WORKERS=0`), so that one testparm at a time runs
   on the device; `scripts/dev/bin/testparm` runs the device's
   testparm on each rendered file. Result: the tests pass, none is skipped (a skip means
   `scripts/dev/bin` is not first on PATH as an absolute path), and `SD writes testparm:` at
   or near 0 MiB.

5. **Run Linux-only tests.** `scripts/dev/linux-tests.sh tests/test_<name>.py -q` (pytest's
   arguments, relative to `supervisor/`; none: the whole suite, a few minutes), in the
   device's RAM (ADR-0187), under the device lock. Result: `pytest exit status: 0` and
   `SD writes linux-tests.sh` at or near 0 MiB. After a reboot of the device the RAM rootfs is
   gone: the script prints the owner action that makes it again.

6. **Run a scenario check, when the owner asked for one.** `scripts/dev/scenario.sh <area>`
   (`files`, `volumes`, `smart`, `backups`, `smb`, `smbconf`, `shares`, `scrub`, `balance`, `offsite`, `restore`,
   `hardware` or `all`; `--pool <name>` where several pools are mounted), the checks of the host-level entries that the feature-pass
   skill's table names (ADR-0304). They change the device: they make a temporary user, share,
   volume, Scheduler task and folders named `unoscheck<6 hex digits>`, and two backups, through
   the API, and
   remove them again; `smbconf` also edits smb.conf by hand and restarts the supervisor once
   (ADR-0309), `shares` makes entries of other owners and modes on the pool over ssh, signs in as
   its user and turns guest access on for its share, which lowers signing for the whole server
   while it runs (ADR-0100, ADR-0340), `scrub` scrubs the pool, three times on a small pool, and leaves its last
   scrub result (ADR-0485), `balance` writes 24 GiB of allocated space (fallocate) into a folder of the
   pool (which needs twice that free), deletes most of it again and balances, pauses right after the
   start, resumes and cancels the chunks it left about 12% full, which moves up to 128 MiB of each on
   the pool's disk, and leaves its last balance result, cancelled (finished when it ended first),
   until the host restarts; a step whose request the balance's end beat is a SKIP that says so, and
   a scrub that the ended balance let start is cancelled, which leaves the last scrub result aborted
   (ADR-0379, ADR-0485), `offsite` sets
   up an off-device copy to a folder of the pool and forgets it again (ADR-0337); it refuses to start
   on a device that has one set up, and is a SKIP where no pool is off the disks of the root
   filesystem and of the supervisor's data volume, as on the BPI-F3 (ADR-0485), and
   `restore` restores a backup it made moments before, twice, which puts the device's database
   back as it was then and ends the web sessions but the relay's (ADR-0341); it refuses to start
   beside a share that is not served and has access, which a restore removes, or beside more than
   3 copies `unos.db.pre-restore-*` of the database, of which UNOS keeps 5 (thinning them out is
   the owner's decision), and removes the 2 that its restores make, unless a restore changed a
   user, share, API token of the relay's administrator or account that is not the check's: then
   they stay, named by its FAIL, as they hold that data, and removing them is the owner's decision.
   `hardware` only reads: the SoC, what the App Store offers of it to each container of
   Jellyfin and Immich, the nodes the host has and the RKNPU driver's version (ADR-0382).
   `all` takes several minutes: run it in the background as
   `timeout 3600 scripts/dev/scenario.sh all > <log> 2>&1` (a background command has a deadline,
   ADR-0335), or in the foreground with a tool timeout long enough, since a check that is killed
   cannot clean up, and read its whole output (not `| head`: a check whose output closes stops
   its steps). Result: a PASS line for every step, each with its SD
   MiB, `scenario <area>: OK`, and its own `SD writes scenario <area>` line. A FAIL ends the steps
   of its check; the lines after it say what the clean-up removed and whether the device is as
   the check found it.

7. **Report.** For each check: the command, its result and the MiB of its `SD writes` line
   from this session's output. Result: every check has its number, and no number in the
   report is without its command.

## Gotchas

- Never run `git status`, `git diff` or other index-refreshing git commands in the device's
  checkout (one wrote 56 KiB): `scripts/dev/deploy.sh status` compares it with the working
  tree without writing.
- `sd.sh measure` counts every write to the system disk while the command runs, the device's
  own background too (about 0.016 MiB a minute when idle): a long wait for a lock adds to the
  number.
- A `<redacted>` where you expected a plain value means that a secret's value occurs in the
  output: the redaction works. Tell the owner; the cure is a new password (an owner action),
  never a weaker redaction.
- API calls write a little anyway: about 0.02 MiB a call (logs on the system disk), a login about
  0.08 MiB and the first after a supervisor start about 0.15 MiB (the login history's commit
  in the database's write-ahead log and the file system's metadata, ADR-0308). api.py signs
  in only when it has no usable token: none is kept in the device's RAM
  yet, the API refused the kept one, or the cache is not root's with mode 600 (ADR-0222).
- Playwright never runs against the device: the specs create and delete pools, shares and
  users. Use the standin-e2e skill.
- `check.sh`, `api.py` and the testparm shim take no lock: avoid them while a deploy restarts
  the containers (`check.sh` shows the lock's holder).
- A scenario check that cannot start because the device "holds leftovers of an earlier check"
  names them: a run that lost its connection could not remove them. Ask the owner, then remove
  them in this order under `scripts/dev/sd.sh measure clean -- ...`, and run the check again:
  shares, `scripts/dev/api.py DELETE /shares/smb/<name>`; users,
  `scripts/dev/api.py DELETE /auth/users/<id> --param username=<name>` (the id from
  `GET /auth/users`); the restored copies and snapshots in a check's volume, which its Delete
  refuses, `scripts/dev/api.py DELETE /snapshots/copies/<name> --param subvolume=/mnt/pools/<pool>/<volume>`
  and `scripts/dev/api.py DELETE /snapshots/<name> --param subvolume=/mnt/pools/<pool>/<volume>` (the
  names from `GET /snapshots/copies` and `GET /snapshots/` with the same parameter); volumes,
  `scripts/dev/api.py DELETE /storage/pools/<pool>/volumes/<name>`;
  a subvolume `<folder>/nest/sub` that the files check made, which no API route removes,
  `scripts/dev/ssh.sh 'btrfs subvolume delete /mnt/pools/<pool>/<folder>/nest/sub'`; items of
  the trash, `echo '["<path from GET /files/trash>"]' | scripts/dev/sd.sh measure clean -- scripts/dev/api.py DELETE /files/trash -`;
  folders, `echo '["/mnt/pools/<pool>/<folder>"]' | scripts/dev/sd.sh measure clean -- scripts/dev/api.py DELETE /files/delete - --param permanent=true`
  (the pipe into `sd.sh`, which gives the command its standard input; the `-` before `--param`); a Scheduler task, `scripts/dev/api.py DELETE /scheduler/tasks/<id>`
  (the id from `GET /scheduler/tasks`); a balance that the balance check left running or paused,
  `scripts/dev/api.py DELETE /storage/pools/<pool>/balance`; the scrub and balance checks' empty folder right below the pools'
  base, which the files API does not delete, `scripts/dev/ssh.sh 'rmdir /mnt/pools/<folder>'`. A marked host account or Samba account `unoscheck<6 hex digits>`
  that stayed after its user was deleted (the delete's warning names a failed userdel or pdbedit)
  is the host's: removing it, `scripts/dev/ssh.sh 'pdbedit -x -u <name>; userdel <name>'`, is an
  owner action. A backup is not named `unoscheck`: only the run that made it names one whose
  answer it lost (`FAIL  clean up: delete the check's backups`), and
  `scripts/dev/api.py DELETE /backup/<name>` removes it.
- The smbconf check refuses to start beside an smb.conf that is not UNOS's, 9 copies
  `smb.conf.unos-replaced-<time>` (its two copies would make UNOS delete the oldest, ADR-0362) or a
  problem on the Shares page. A run that lost its connection can
  leave smb.conf as it edited it, with the original in
  `/dev/shm/unos-scenario-unoscheck<6 hex digits>hand.conf`, a `smb.conf.unos-replaced-<time>`
  that holds that section, and a symbolic link `<pool>/unoscheck<6 hex digits>` to its `-moved`
  folder. Ask the owner, then put them back with `scripts/dev/ssh.sh`: the original over smb.conf
  only while smb.conf does not start with UNOS's header (once an apply has run, UNOS's rendering
  stays), the copy removed only when it holds nothing but the check's section beside UNOS's, the
  link removed and the folder moved back; remove the share (and a user `unoscheck<6 hex
  digits>`) as above, and restart the supervisor if the Shares page still names the section
  (ADR-0362).
- The smb check needs smbclient and stdbuf on the device (its first steps say which is
  missing): installing a package is an owner action (ADR-0211).
- A run of the shares check that lost its connection can leave, besides its share, user and
  folders (removed as above; its share's folder `<pool>/unoscheck<6 hex digits>` holds `legacy.txt`
  of `nobody`, `-sticky` holds the folder `theirs` of `nobody` and `-outside` is root's, all of
  which the files API deletes), its share's folder as a symbolic link
  to `-moved` (put back as for the smbconf check) and its user's token in
  `/dev/shm/unos-scenario-unoscheck<6 hex digits>.token`, which the user's Delete makes useless:
  `scripts/dev/ssh.sh 'rm -f /dev/shm/unos-scenario-unoscheck<6 hex digits>.token'`. Its ACL step
  needs setfacl and getfacl (the package acl) on the device; without them it says so and skips.
- The scenario checks write to the system disk, and each step's line says how much: the API's
  database and audit log at every change, smb.conf at every share change, `/etc/passwd` and
  `/etc/shadow` when the smb check's account is made and deleted, passdb.tdb then, at its
  password change and at each wrong password that Samba's lockout counts (not at the replaced
  password, which the history holds, nor while the account is locked: ADR-0334, which the check
  compares), the backups check's two backups, and
  about 0.02 MiB of container logs per API call (ADR-0304).
