# deploy/: compose, Caddy, the install scripts, and the dev device

## Files

- `docker-compose.yml` builds `../supervisor` and `../webui` into `unos-supervisor` and
  `unos-webui` on the fixed subnet `172.31.254.0/24`: the supervisor trusts `X-Forwarded-For`
  only from the web UI's address (ADR-0048). Change every `172.31.254.x` together.
- Its comments explain its values: the `/tmp` and `/var/lib/btrfs` tmpfs keep volatile writes
  off the system disk (ADR-0171); the health checks and `depends_on: service_healthy` take
  up to 680 s, which `SUPERVISOR_START_TIMEOUT` in `install.sh` and `update.sh` must equal and
  `UNOS_DEV_UP_TIMEOUT` of `scripts/dev/deploy.sh` (700) must exceed (ADR-0176); the web UI's
  `command` waits for the API before it runs the CMD of `webui/Dockerfile`, because at boot
  dockerd starts the containers it still has at once, without `depends_on`: after a power cut,
  and on a host without the `unos` unit, such as the dev device (ADR-0307);
  `UNOS_JWT_SECRET` stays empty so that each install generates its own (ADR-0028).
- `docker-compose.test.yml` is the Docker test stack (project `unos-test`, ports 3443 and 3080 on
  127.0.0.1, the Playwright runner of the `test` profile), for a machine that runs Docker.
- `Caddyfile` is an identical copy of `webui/Caddyfile`, the file the image uses: HTTPS from
  Caddy's local CA, without HSTS (ADR-0025).
- `install.sh`, `update.sh` and `uninstall.sh` run as root on a user's host. `update.sh` refuses
  local changes and other branches, backs up, waits for health and rolls back on failure
  (ADR-0049); `uninstall.sh` keeps the SMB host accounts and removes only their exact passdb
  entries (ADR-0180). There are no upgrade paths from older UNOS versions (ADR-0207).
- `reset-admin.sh` also runs as root on a user's host: it runs the emergency command line,
  `python -m src.emergency reset-admin`, in the supervisor container, with the new password on
  standard input (ADR-0284, `docs/RECOVERY.md`); `supervisor/tests/test_emergency.py` runs it
  with a fake docker.
- `supervisor/tests/test_deploy_scripts.py` runs these scripts against fakes, parses the compose
  files and keeps `docs/INSTALL.md` and `docs/TESTING.md` in step with them; `test_authz.py`,
  `test_secret_bootstrap.py` and `test_standin.py` read them too. Run those tests after an edit
  here; `make lint` runs `bash -n` on every script.

## The dev device

One shared board. It runs from an SD card that wears with every write, and its eMMC keeps the
vendor's firmware and is never written, so every write counts (ADR-0214, ADR-0225).

- Every action goes through `scripts/dev/`, with the settings of `dev.env`: the deploy-device
  skill for deploys, the device-checks skill for checks. `deploy.sh`, `linux-tests.sh`,
  `ipv6-filter.sh` (install, remove) and `scenario.sh` hold the device lock `/run/unos-dev.lock`
  (ADR-0210). They and `check.sh` print
  `SD writes <label>: <MiB>`; `ssh.sh`, `api.py` and `bin/testparm` print none, so wrap them in
  `scripts/dev/sd.sh measure <label> -- ...`. The report carries those numbers.
- The scenario checks (`scripts/dev/scenario.sh <area>`, ADR-0304) run an area's user scenario
  through the API and on the host with a temporary user, share, volume and folders named
  `unoscheck<6 hex digits>` and two backups, remove them again and compare the device with how
  they found it; the smbconf check also edits smb.conf by hand and restarts the supervisor
  (ADR-0309), and the scrub check scrubs the pool (ADR-0314). They change the device for their duration, so they run on the owner's request;
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
  and digest (ADR-0305). A supervisor build after a change to its requirements, or to its base's
  digest when the image that digest names for linux/arm64 is new to the device, reruns apt-get
  and pip there: tell the owner first. `deploy.sh build-supervisor` says before the build
  whether the device has the pinned base.
- Checks start no container (ADR-0183), keep scratch data in `/dev/shm` and call the API through
  `scripts/dev/api.py`, which runs on the device against `https://127.0.0.1/api/v1` (the device's
  web ports do not answer the workstation, ADR-0198) and keeps its token in the device's RAM
  (ADR-0222).
- In the device's checkout, `git status` and other index-refreshing commands write to the SD
  card; `scripts/dev/deploy.sh status` compares it with the working tree without writing, and the
  scripts' read-only work runs with the device's root filesystem read-only (ADR-0221).
- After a deploy or a rollback, the Dev device section of `PROGRESS.md` gets what runs there and
  the SD MiB of each step (ADR-0204).
- Reboots, image prunes and package installs (ADR-0211), credentials and the device's security
  settings (ADR-0024) are owner actions.
- Over IPv6 the device's services answer only the LAN: `scripts/dev/ipv6-filter.sh` installs the
  inbound filter of `scripts/dev/device/` (ADR-0301), `status` shows it and `check.sh` reports it
  missing. `install` and `remove` change a security setting: on the owner's request only
  (ADR-0024). The workstation reaches the device over IPv4, which the filter does not touch.
