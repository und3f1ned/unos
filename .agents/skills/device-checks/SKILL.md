---
name: device-checks
description: Check the UNOS dev device without changing it (health, API answers, Samba's real testparm, the backend tests on Linux in the device's RAM) and report the SD-card writes of each check. Use to verify a deployed change, behaviour the local stand-in cannot provide, or Linux-only tests before CI.
---

# Read-only checks on the dev device

The device's system disk is an SD card that cannot be replaced (ADR-0214). Run every check from
the repository root, through `scripts/dev`:

- `check.sh`, the API relay of `api.py`, the testparm shim `bin/testparm` and `linux-tests.sh`
  run on the device in a private mount namespace with the root filesystem read-only and
  noatime (ADR-0221), and keep scratch files in RAM (`/dev/shm`). A command you give `ssh.sh`
  runs as it is.
- Only `deploy.sh` (every step) and `linux-tests.sh` take the device lock (ADR-0210); a second
  run waits. `check.sh`, `api.py`, `ssh.sh`, `sd.sh` and the testparm shim take none.
- `check.sh`, `linux-tests.sh`, `deploy.sh` and `sd.sh measure` print
  `SD writes <label>: <MiB> MiB (<sectors> sectors)`. `ssh.sh`, `api.py` and the testparm shim
  print nothing of the kind: run them under `scripts/dev/sd.sh measure <label> -- <command>`,
  as the steps below do.

Settings come from `dev.env` (mode 600, ADR-0212); in a git worktree without one, the scripts
use the main checkout's. Output shows secrets as `<redacted>`.

## Steps

1. **Reach the device.** `scripts/dev/sd.sh measure ssh -- scripts/dev/ssh.sh true`.
   Result: exit status 0 and `SD writes ssh: 0.000 MiB`. On `no dev.env` or `mode 644`, do
   what the message says; the passwords in `dev.env` are the owner's to fill in.
   `Host key verification failed` after a reinstall of the device: ask the owner to remove the
   old key (`ssh-keygen -R <host>`).

2. **Check its health.** `scripts/dev/check.sh`. Result: `check: OK`, exit status 0, the lock
   holder if a deploy or a test run is in progress, and its own `SD writes check.sh` line.

3. **Ask the API.** `scripts/dev/sd.sh measure api -- scripts/dev/api.py GET /system/stats` (any
   path under `/api/v1`, query parameters with `--param key=value`). Result: `HTTP 200`, the
   JSON body and `SD writes api: ...`. POST, PUT, PATCH and DELETE change the device: only for
   a check the owner asked for, and undo what you create.

4. **Use Samba's own parser.** From the repository root:
   `PATH="$PWD/scripts/dev/bin:$PATH" scripts/dev/sd.sh measure testparm -- make test TESTS=tests/test_smbconf_testparm.py`
   (add `PYTHON=...` for another interpreter than `make setup`'s venv). `make test` holds the
   lock of the backend tests, so it waits for another run on this machine (also in another
   worktree), and runs pytest in `supervisor/`; `scripts/dev/bin/testparm` runs the device's
   testparm on each rendered file. Result: the tests pass, none is skipped (a skip means
   `scripts/dev/bin` is not first on PATH as an absolute path), and `SD writes testparm:` at
   or near 0 MiB.

5. **Run Linux-only tests.** `scripts/dev/linux-tests.sh tests/test_<name>.py -q` (pytest's
   arguments, relative to `supervisor/`; none: the whole suite, a few minutes), in the
   device's RAM (ADR-0187), under the device lock. Result: `pytest exit status: 0` and
   `SD writes linux-tests.sh` at or near 0 MiB. After a reboot of the device the RAM rootfs is
   gone: the script prints the owner action that makes it again.

6. **Report.** For each check: the command, its result and the MiB of its `SD writes` line
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
- API calls write a little anyway: about 0.02 MiB a call (logs on the SD card), a login about
  0.19 MiB. api.py signs in only when it has no usable token: none is kept in the device's RAM
  yet, the API refused the kept one, or the cache is not root's with mode 600 (ADR-0222).
- Playwright never runs against the device: the specs create and delete pools, shares and
  users. Use the standin-e2e skill.
- `check.sh`, `api.py` and the testparm shim take no lock: avoid them while a deploy restarts
  the containers (`check.sh` shows the lock's holder).
