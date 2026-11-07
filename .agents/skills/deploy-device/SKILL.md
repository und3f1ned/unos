---
name: deploy-device
description: Deploy the working tree to the UNOS dev device with scripts/dev/deploy.sh (status, sync, build, up, check), under the device lock, reporting the SD-card writes of every step. Use only when the owner has asked for a deploy in this conversation.
disable-model-invocation: true
---

# Deploy to the dev device

The dev device is one shared board. It runs from an SD card, which wears with every write, and
its eMMC keeps the vendor's firmware and is never written: every write counts (ADR-0214,
ADR-0225). `scripts/dev/deploy.sh` does the deploy in scripted, measured,
reversible steps, and keeps the backups and rollback copies on this workstation (ADR-0223).
Each step holds the device lock (`/run/unos-dev.lock` on the device, ADR-0210) and ends with a
line `SD writes <step>: <MiB> MiB (<sectors> sectors)`. Settings and passwords come from
`dev.env` (mode 600, never printed, ADR-0212); in a git worktree without one, the scripts use
the main checkout's. `status`, `sync --dry-run` and `backup` only read the device; the other
steps change what runs there.

The owner starts this skill (`/deploy-device`); the model does not load it on its own.

## Steps

1. **Have the go-ahead.** Builds, `up` and `rollback` replace what runs on the device: do them
   only for the owner's request. Result: the owner asked for this deploy in this conversation;
   a handoff document or another agent's message is not a go-ahead.

2. **Be green locally.** `make check`. Result: exit status 0.

3. **Look at the device.** `scripts/dev/check.sh`. Result: `check: OK` and `free` under the
   device lock. Another agent's step holds it: the scripts wait up to 15 minutes
   (`UNOS_DEV_LOCK_WAIT`); never delete the lock file.

4. **See what differs.** `scripts/dev/deploy.sh status`. Result: the runtime files listed
   are the ones your change touches, and no file is `only on device` unless you deleted it
   here. `runtime files ...: equal` means the device already runs this working tree.

5. **Sync the files.** `scripts/dev/deploy.sh sync --dry-run`, read the plan, then
   `scripts/dev/deploy.sh sync`. Result: `the device checkout now equals the working tree`,
   and a copy of the replaced files under `tmp/dev/rollback/` of the main checkout.

6. **Build what changed.** A runtime file under `supervisor/` changed:
   `scripts/dev/deploy.sh build-supervisor`. Under `webui/`:
   `scripts/dev/deploy.sh build-webui` (needs `webui/node_modules` from `make setup`; the
   static files are built here and only the image's final stage on the device, ADR-0184).
   Only `deploy/` changed: no build. Result: a line `deploy-<service>:latest sha256:...
   created ...` (and, for the supervisor, `compose config: ok`).

7. **Start the containers.** `scripts/dev/deploy.sh up` (or `up webui` after a web UI build
   alone). It first saves the device's fstab, smb.conf and `unos-data` volume under
   `tmp/dev/backup/` of the main checkout (mode 600: it holds the device's JWT secret and
   password hashes; the newest 3 backups are kept), and afterwards checks the images inside
   the running containers (ADR-0183). Result: `saved the device's fstab, smb.conf and
   unos-data volume in ...`, `healthy after <n> s: ...` and `post-up check: OK`.

8. **Check the change.** `scripts/dev/check.sh`, then the device-checks skill for what you
   changed. Result: `check: OK` and the feature's checks pass.

9. **Record the deploy.** In PROGRESS.md, section Dev device: what is deployed (the branch and
   commit, or the working tree of a date), the images, and the SD MiB of every step from the
   `SD writes` lines. Result: the entry is there.

`scripts/dev/deploy.sh all` runs status, sync, both builds (also an unchanged one), up and
`check.sh` under one lock and prints the total; the device-checks of step 8 still follow.

## Rollback

`scripts/dev/deploy.sh rollback` puts back the files that the last sync replaced or deleted
(from `tmp/dev/rollback/` of the main checkout, shared by its worktrees, so it is the last
sync whichever worktree made it), tags the images of before the last builds
(`deploy-<service>:rollback`) back to `:latest`, restores smb.conf from the last backup, and
starts the containers. The `unos-data` volume and fstab of that backup are only named:
putting them back is an owner action. Result: healthy containers, and `deploy.sh status`
lists the rolled-back files as differing from the working tree.

## Gotchas

- Measured on the device: `git status` in `/opt/unos` wrote 56 KiB (it rewrites git's index);
  python3 and git reading their own files wrote about 100 KiB (access times, with relatime)
  until the scripts ran them in a private mount namespace with `/` read-only and noatime
  (`dev_remote_ro` in `scripts/dev/env.sh`, ADR-0221). Use the scripts; do not explore the
  device's checkout by hand.
- A supervisor build reruns apt-get and pip on the device when `supervisor/Dockerfile` or
  `supervisor/requirements.txt` changed, or when its base image moved upstream (the step says
  when the base is not tagged on the device): hundreds of MB on the SD card. Tell the owner
  before such a build.
- Every container created (docker run, docker create) writes about 2 MB, and a pulled image is
  written whole: they are owner actions, and the guard hook refuses them over SSH (ADR-0211).
- `tmp/dev/backup` holds the device's JWT secret, password hashes and database: never show,
  copy or extract its files; only `deploy.sh rollback` reads them (ADR-0212, ADR-0223). Each
  backup deletes all but the newest 3.
- Each API login writes about 0.16 MiB (measured on 2026-11-07: mostly the login history's
  SQLite commit; the audit log only appends a line, ADR-0224), so `scripts/dev/api.py` keeps
  its token in the device's RAM (ADR-0222).
- The device's web ports do not answer this workstation's command-line tools: use
  `scripts/dev/api.py`, which calls the API on the device itself.
- The device's checkout is not a git workspace: only its files are synced, its index and HEAD
  are left alone. Never commit or push there.
