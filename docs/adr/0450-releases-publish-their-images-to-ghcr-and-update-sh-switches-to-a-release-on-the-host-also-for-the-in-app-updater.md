---
status: accepted
date: 2026-11-22
area: deploy
decided-by: agent
supersedes: [ADR-0049, ADR-0443]
---

# ADR-0450: Releases publish their images to GHCR, and update.sh switches to a release on the host, also for the in-app updater

## Context

ADR-0050 proposed releases as git tags with amd64 and arm64 images on GHCR, but no workflow published
any, so `update.sh` and `install.sh` built both images on every host, hundreds of MB of writes on a
board's SD card (ADR-0214), and the in-app updater installed nothing: ADR-0443 left open where a
release's images come from and how the supervisor replaces its own container, since the process that
runs the update is in one of the containers it replaces. The owner delegated both choices; this ADR
records them and accepts ADR-0050.

## Decision

- **A release is a git tag `v<x.y.z>`.** `.github/workflows/release.yml` runs on the push of such a
  tag. Its job `images` (the only one with `packages: write`) checks the tag, registers QEMU for
  linux/arm64 from Ubuntu's `qemu-user-static` (no third-party action, whose binfmt image is not
  pinned; the step fails unless the kernel's handler has the fix-binary flag), and builds
  `supervisor/` and `webui/` from their pinned Dockerfiles (ADR-0305) with buildx for linux/amd64 and
  linux/arm64. It pushes them as `ghcr.io/<repository owner, lowercase>/unos-supervisor:<x.y.z>` and
  `unos-webui:<x.y.z>`, logged in with the job's token on standard input. Then the job `release`
  (`contents: write`, after `images`) publishes the tag's GitHub release with `gh release create
  --verify-tag`: the in-app check reads GitHub's latest release (ADR-0234), so a release is listed
  only once its images are there. Actions are pinned by commit as in the other workflows (ADR-0423):
  only `actions/checkout` and `docker/setup-buildx-action`, with their pins; the BuildKit image that
  buildx starts, which holds the push's credentials, is pinned by tag and digest (`driver-opts`,
  as ADR-0305 pins bases), moved by the monthly pass of `docs/maintenance.md`. riscv64 gets no
  release images (ADR-0373, `deploy.riscv64-release-images`).
- **The packages are public**, so that a NAS pulls without credentials: the owner sets both
  packages' visibility once, at the first release (`docs/maintenance.md`).
- **A release image says so.** `supervisor/Dockerfile` has `ARG UNOS_RELEASE` and `ENV UNOS_RELEASE`
  after `UNOS_VERSION`, after the code; `release.yml` passes the tag for both. A local build
  (`scripts/dev/deploy.sh`, a build on riscv64 or by hand) leaves `UNOS_RELEASE` empty. The version
  of ADR-0234 is the tag: release.yml passes it, and on riscv64 install.sh and update.sh still pass
  `git describe --tags --always` of the checked-out tag.
- **`deploy/update.sh [<tag>]` switches to a release** (the newest tag of origin, or the one named):
  it fetches that tag alone, pulls both images (`docker pull --quiet`) while UNOS runs, so that a
  release that cannot be had changes nothing, backs up as ADR-0049 says, tags the running images
  `:previous`, checks the tag out (a detached HEAD), tags the pulled images as `deploy-supervisor` and
  `deploy-webui`, the names compose starts (project `deploy`, the folder of the compose file), and
  removes their GHCR names, then starts them and checks their health. On a failure or an interrupt
  it stops the release and, once Docker says the supervisor no longer runs, puts the backup's
  database back into the data volume (the host folder of the supervisor's `/data`, read while its
  container exists): copied next to `unos.db`, then the live `unos.db-wal`, `unos.db-shm` and a
  journal removed (SQLite would apply that log to the backup) and the copy renamed into place. Then
  it starts the `:previous` images with the previous checkout. A database that cannot be put back
  makes the rollback a failed one, also when the version before answers. A release that is not
  newer than the running one, or than the release it follows (`v1.2.0` for `v1.2.0-3-g<commit>`),
  is refused, as are local changes; the branch (`UNOS_BRANCH`) and its checks are gone (ADR-0207).
  No image is built where Docker's architecture (`docker version`, which a pull takes, not the
  kernel's) is amd64 or arm64. Elsewhere (riscv64, a 32-bit Arm userland) it builds from the
  checked-out tag, on a SpacemiT K1 the supervisor alone (ADR-0373). Old images are
  pruned only after the health check (ADR-0049). The registry is `UNOS_REGISTRY`,
  `ghcr.io/und3f1ned` by default, in both scripts.
- **`deploy/install.sh` installs the newest release**: it clones the repository, checks out the newest
  tag `v<x.y.z>` (it stops when there is none) and, on amd64 and arm64, pulls and names the release's
  images as update.sh does. On riscv64 it builds them from that tag, as ADR-0373 says; a rerun
  checks out the newest release again.
- **The in-app updater runs update.sh's steps on the host, not a second copy of them.** One home for
  the switch, its health check, its rollback, the update lock and the rules of riscv64: an update by
  hand and one from the web UI do the same, and update.sh already runs as root on the host with the
  docker CLI. Safe because its in-app modes ask nothing, make no backup of their own and write their
  result atomically. `HostUpdate` of `supervisor/src/updates/installer.py`:
  - `fetch_release`: `<UNOS_INSTALL_DIR>/deploy/update.sh --fetch <tag>` through
    `host.run_on_host_async`, under the host's `timeout` (up to an hour, ADR-0258): the tag and the
    images, nothing else changes, also for an older release (a rollback's).
  - Then the updater's backup stays ADR-0443's: `copy_database` into `in-app-<time>` of
    `UNOS_UPDATE_BACKUP_DIR`, the host's `/var/lib/unos/backups`.
  - `start_switch`: `systemd-run --unit unos-<update|rollback>-<time> --collect` with
    `KillMode=mixed` and `TimeoutStopSec=2400` (a stop sends SIGTERM to update.sh alone, which then
    rolls back with its signals ignored) of `update.sh --in-app <update|rollback> --backup <that
    folder> --result <file> <tag>`, through `host.run_on_host_async`. The unit runs in the host's
    systemd, outside the containers it replaces, so the switch, the health check and the rollback
    outlive the supervisor's container. In this mode update.sh uses the folder's `unos.db` as its
    backup and writes `success`, `rolled-back` or `failed` with the first error into the result
    file when it ends, also after a wrong argument. A systemd-run that fails while the unit runs is
    followed all the same.
  - `switch_running`: `systemctl show --property=ActiveState` of the unit.
- **The job passes to the next supervisor through the progress file.** Before it starts the unit, the
  updater records `handover` (the unit, the result file, the version before, the release's notes and
  the time) in `update_state.json`. Whichever supervisor runs then follows the unit: the one that
  started it until compose replaces it, then the release, or the version before after the unit's
  rollback, from its start (`recover_after_start`). It reads the result file every 3 seconds and
  records the job: completed, failed and rolled back (`rolled_back`, `can_rollback` false), or
  failed. A unit that ended without a result (the host restarted) or that runs past 2700 seconds
  fails the job and keeps `can_rollback`. A job that was not handed over is still failed at the
  start, as ADR-0443 says.
- **Rollback by hand** first fetches the version before's images (`--fetch`), then runs
  `--in-app rollback` with the newest backup: the unit stops UNOS and, once the supervisor no longer
  runs, copies the backup's database next to the live one, renames the live database's files aside
  in the volume and the copy into place, and starts the version before; if that does not answer, it
  takes the renamed files back and starts the version it came from, and Rollback can be tried
  again. The rollback through the open database of ADR-0443
  (`_restore_database`) is gone; a restore is still refused while a job runs, and the reverse.
- **Where the web UI cannot install,** `unavailable_reason` says why, the first that applies: the
  supervisor runs outside its container (`UNOS_HOST_EXEC=local`: the stand-in or the test stack);
  the image's machine has no release images (riscv64, or a 32-bit userland); the image is not a
  release image (`UNOS_RELEASE` differs from `UNOS_VERSION`: a dev device deployed by
  `scripts/dev/deploy.sh`, or a build by hand), with "not a release" when the running version is not
  a release tag either; the running version is not a release tag; `<UNOS_INSTALL_DIR>/deploy/update.sh`
  is not on the host (read through `/proc/1/root`). `UNOS_INSTALL_DIR` is a setting of `supervisor/src/config.py`
  again (`install_dir`, `/opt/unos`, absolute: ADR-0419).
- Tests: `TestReleaseWorkflow`, `TestUpdateScript`, `TestUpdateScriptInApp` and `TestInstallScript` of
  `supervisor/tests/test_deploy_scripts.py`; `supervisor/tests/test_update_install.py` (the handover,
  the follow, the installer's commands and reasons); `test_data_dir.py` (the setting);
  `webui/tests/updates.spec.ts`.

## Consequences

- ADR-0049 is superseded only where update.sh built from its branch; its backup, lock, `:previous`
  images, health check and `--wait-timeout` stay. ADR-0443 is superseded only in the release
  installer, the rollback's database and the start's failing of a handed-over job; every other rule
  stays.
- The ledger entry `deploy.update-script` changes with it: update.sh updates to a release, pulls its
  images and rolls back with the backup's database.
- Nothing installs until the owner pushes the first tag and makes the two packages public; until
  then `install.sh` stops with "no release yet". Each release costs a run of up to two hours of
  GitHub Actions, most of it the arm64 build under QEMU.
- A NAS downloads each release's images, about the size of both, and writes no build layers. Each
  update keeps one release's images as `:previous`; older ones are pruned.
- The in-app updater writes its result file and, for a rollback, renames the database's files: no
  write beyond what the switch needs. The unit's output goes to the host's journal
  (`journalctl -u unos-update-<time>`).
- A reboot in the middle of a switch leaves what update.sh had done so far, as a reboot during
  update.sh by hand did; the next supervisor fails the job and keeps Rollback.
- A tag moved after a NAS fetched it is refused by git on the next fetch, and the update stops.
  Images are pulled by tag from the repository owner's GHCR namespace, which is trusted as the
  repository is; a tag pushed anew to GHCR is not noticed by a NAS that has its image already.
- The scripts take the newest git tag: until its release run has published the images, an install or
  update stops at the pull without a change.
- Rollback needs GHCR (`--fetch` of the version before), although the `:previous` images are on the
  host: a NAS that cannot reach GHCR cannot roll back from the web interface.

## Considered options

- A copy of the switch in Python, run by a helper container: two homes for the same steps, and a
  container that the switch must not stop.
- docker/setup-qemu-action and docker/build-push-action: new actions to pin, and the first registers
  QEMU from an image tag that moves.
- The images named `ghcr.io/...` in `deploy/docker-compose.yml`: `scripts/dev/deploy.sh` and the unit
  at boot would need the version in their environment, and a missing one would pull or build.
