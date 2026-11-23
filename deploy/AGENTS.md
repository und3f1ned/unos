# deploy/: compose, Caddy, the install scripts, and the dev device

## Files

- `docker-compose.yml` builds `../supervisor` and `../webui` into `unos-supervisor` and
  `unos-webui` on the fixed subnet `172.31.254.0/24`: the supervisor trusts `X-Forwarded-For`
  only from the web UI's address (ADR-0048). Change every `172.31.254.x` together.
- Its comments explain its values: the `/tmp` (192 MiB, sized for the backup limits of
  `supervisor/src/backup/router.py` within the 1 GB of RAM of the smallest board, ADR-0406) and
  `/var/lib/btrfs` tmpfs keep volatile writes off the system disk (ADR-0171); the health checks and `depends_on: service_healthy` take
  up to 680 s, which `SUPERVISOR_START_TIMEOUT` in `install.sh` and `update.sh` must equal and
  `UNOS_DEV_UP_TIMEOUT` of `scripts/dev/deploy.sh` (700) must exceed (ADR-0338); the web UI's
  `command` waits for the API before it runs the CMD of `webui/Dockerfile`, because at boot
  dockerd starts the containers it still has at once, without `depends_on`: after a power cut,
  and on a host without the `unos` unit, such as the dev device (ADR-0307);
  `UNOS_JWT_SECRET` stays empty so that each install generates its own (ADR-0028).
- `docker-compose.test.yml` is the Docker test stack (the Playwright runner of the `test` profile),
  for a machine that runs Docker. Its project, its ports on 127.0.0.1 and its subnet come from the
  `UNOS_TEST_*` settings that `scripts/test-cycle.sh` derives from its worktree, one stack per
  worktree (ADR-0439); the defaults, `unos-test`, 3080 and 3443 and `172.31.253.0/24`, are those of
  a stack started by hand.
- Caddy's configuration is `webui/Caddyfile`, which the web UI image copies, and no compose file
  mounts another: HTTPS from Caddy's local CA, without HSTS (ADR-0025), or from the served
  certificate, which the supervisor writes into the `served-certificate` volume that only it and
  the web UI (read-only) mount; the web UI's command serves it only when `caddy validate` passes,
  and the supervisor has Caddy load a change with `caddy reload` through `docker exec` (ADR-0404).
  The app names' sites come the same way, from the volume `app-names`, which the Caddyfile imports
  and the web UI's command loads only when they validate (ADR-0420).
- `install.sh`, `update.sh` and `uninstall.sh` run as root on a user's host. A release is a tag
  `v<x.y.z>` whose amd64, arm64 and riscv64 images `.github/workflows/release.yml` publishes on GHCR
  (riscv64's supervisor from the riscv64 wheels of its job `wheels`, ADR-0458):
  `install.sh` checks out the newest release and pulls them, `update.sh [<tag>]` refuses local
  changes, pulls them while UNOS runs, backs up, switches, waits for health and rolls back on
  failure with the backup's database (ADR-0049, ADR-0050); both build only where Docker is neither
  amd64, arm64 nor riscv64 (32-bit arm), which has no release images (ADR-0458). The in-app updater runs `update.sh --fetch` and, in a transient
  systemd unit, `update.sh --in-app` (`supervisor/src/updates/installer.py`); the compose file
  keeps building `deploy-supervisor` and `deploy-webui` for `scripts/dev/deploy.sh`, and the
  scripts give the pulled images those names; `uninstall.sh` keeps the SMB host accounts and removes only their exact passdb
  entries, with one restart of smbd (ADR-0322), and undoes on the disks of UNOS's udev rule of
  the idle timeouts what UNOS set, with the host's hdparm and smartctl, then removes the rule (only
  the records of disks whose scans stay off remain), before it removes `/opt/unos`, so that a run
  stopped in that step can run again; its tests run stubs of them, never the host's own, and put a
  folder of their own in place of the system folders that the script adds to PATH (ADR-0378).
  There are no upgrade paths from older UNOS versions (ADR-0207).
- `reset-admin.sh` also runs as root on a user's host: it runs the emergency command line,
  `python -m src.emergency reset-admin`, in the supervisor container, with the new password on
  standard input (ADR-0284, `docs/RECOVERY.md`); `supervisor/tests/test_emergency.py` runs it
  with a fake docker.
- `supervisor/tests/test_deploy_scripts.py` runs these scripts against fakes, parses the compose
  files and keeps `docs/INSTALL.md` and `docs/TESTING.md` in step with them; `test_authz.py`,
  `test_secret_bootstrap.py` and `test_standin.py` read them too. Run those tests after an edit
  here; `make lint` runs `bash -n` and shellcheck on every script (ADR-0448).

## The dev device

One shared board. It runs from its eMMC, a soldered chip that wears with every write, so every
write counts; its SD card is wiped and unused (ADR-0214, ADR-0470).

- Every action goes through `scripts/dev/`, with the settings of `dev.env`: the deploy-device
  skill for deploys, the device-checks skill for checks. `deploy.sh`, `linux-tests.sh`,
  `ipv6-filter.sh` (install, remove) and `scenario.sh` hold the device lock `/run/unos-dev.lock`
  (ADR-0210). They and `check.sh` print
  `SD writes <label>: <MiB>`, the system disk's writes; `ssh.sh`, `api.py` and `bin/testparm` print none, so wrap them in
  `scripts/dev/sd.sh measure <label> -- ...`. The report carries those numbers.
- The scenario checks (`scripts/dev/scenario.sh <area>`, ADR-0304) run an area's user scenario
  through the API and on the host with a temporary user, share, volume, Scheduler task and folders
  named `unoscheck<6 hex digits>` and two backups, remove them again and compare the device with
  how they found it; the smbconf check also edits smb.conf by hand and restarts the supervisor
  (ADR-0309), the shares check makes entries of other owners and modes on the pool over ssh, signs
  in as its user and turns guest access on for its share (ADR-0340), the scrub check scrubs the pool
  (ADR-0485), the smart check gives the pool's disk its shortest idle timeout, waits for it to sleep
  and sets the timeout of UNOS's rule again, or Off, which leaves the disk no standby timer until
  its next power cycle (it skips that when `/etc/hdparm.conf` sets one, ADR-0378), the balance check
  balances, pauses right after the start, resumes and cancels 24 chunks of the pool that it leaves
  about 12% full (ADR-0379, ADR-0485),
  the offsite check sets up an off-device copy to a folder of the pool and forgets it (ADR-0337),
  skipped where no pool is off the disks of the root filesystem and of the supervisor's data volume
  (ADR-0485),
  the restore check restores a backup it made moments before, twice, which puts the device's
  database back as it was then (ADR-0341), and the hardware check only reads (ADR-0382). They change
  the device for their duration, so they run on the owner's request;
  the feature-pass skill names the check of each host-level entry, and after a reboot
  (ADR-0018) `scenario.sh all` runs again, with the checks by hand that its table lists.
- A linked worktree without a `dev.env` of its own uses the main checkout's (`scripts/dev/env.sh`),
  and `tmp/dev/` is the main checkout's too (ADR-0223). Never create or fill in another `dev.env`:
  its passwords are the owner's (ADR-0212, ADR-0024).
- Deploys use `scripts/dev/deploy.sh` (ADR-0223). The device's checkout is synced file by file and
  is not a git workspace, so `update.sh`, commits and pushes have no place there. Builds, `up` and
  `rollback` run when the owner asks for a deploy; `status`, `sync --dry-run` and `backup` only
  read the device. `backup`, also the first step of `up`, copies the device's fstab, smb.conf and
  data volume into the git-ignored `tmp/dev/backup` and keeps the newest 3 (directories 700, files
  600: they hold the JWT secret, the password hashes and the database; only `rollback` reads
  them). Restoring the volume or fstab is an owner action.
- The web UI's static files are built on the workstation, and the device builds only the image's
  final stage (`deploy.sh build-webui`, ADR-0184). The Dockerfiles pin their base images by tag
  and digest (ADR-0305). A release's riscv64 supervisor is `supervisor/Dockerfile` with its one pip
  layer (`RUN pip install --no-cache-dir pip==<x> setuptools==<y> ...`) replaced by an install of the
  wheels of `supervisor/Dockerfile.riscv64-wheels` (`scripts/riscv64-release-dockerfile.sh`, ADR-0458):
  keep that layer one instruction and the pins of both files the same, and add no `FROM` without a
  digest, which build-supervisor refuses. A supervisor build after a change to its requirements, or to its base's
  digest when the image that digest names for linux/arm64 is new to the device, reruns apt-get
  and pip there: tell the owner first. `deploy.sh build-supervisor` says before the build
  whether the device has the pinned base.
- Checks start no container (ADR-0183), keep scratch data in `/dev/shm` and call the API through
  `scripts/dev/api.py`, which runs on the device against `https://127.0.0.1/api/v1` (the device's
  web ports do not answer the workstation, ADR-0198) and keeps its token in the device's RAM
  (ADR-0222).
- In the device's checkout, `git status` and other index-refreshing commands write to the
  system disk; `scripts/dev/deploy.sh status` compares it with the working tree without writing, and the
  scripts' read-only work runs with the device's root filesystem read-only (ADR-0221).
- After a deploy or a rollback, the Dev device section of `PROGRESS.md` gets what runs there and
  the SD MiB of each step (ADR-0204).
- Reboots, image prunes and package installs (ADR-0211), credentials and the device's security
  settings (ADR-0024) are owner actions.
- Over IPv6 the device's services answer only the LAN: `scripts/dev/ipv6-filter.sh` installs the
  inbound filter of `scripts/dev/device/` (ADR-0301), `status` shows it and `check.sh` reports it
  missing. `install` and `remove` change a security setting: on the owner's request only
  (ADR-0024). The workstation reaches the device over IPv4, which the filter does not touch.
