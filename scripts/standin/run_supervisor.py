#!/usr/bin/env python3
"""Run the real supervisor on this machine as the backend of the local E2E stand-in.

The E2E specs run only against local targets, by default this stand-in (ADR-0215).
scripts/standin/e2e.sh starts it; supervisor/tests/test_standin.py checks it.
Everything the supervisor writes goes under the state directory (default:
.standin/ at the repository root, which ignores itself in git):

    data/   the SQLite database with its jwt_secret, smb.conf, the NFS exports,
            every file that supervisor/src keeps under /data (DATA_GLOBALS), and
            stand-ins for the host files it reads or changes (HOST_FILE_GLOBALS):
            an empty fstab and, where this machine has no /proc (macOS), an
            empty mount table
    pools/  UNOS_POOLS_BASE_PATH; testpool/ is the folder of a pool that UNOS
            does not manage, as on the Docker test stack
    tmp/    TMPDIR of the supervisor
    bin/    first on the supervisor's PATH: restic, the fake of
            supervisor/tests/fake_restic.py, and a pass-through ionice, for the
            off-device copy (ADR-0337); restic is not installed here
    fake-restic/  the fake's calls and the stores of its s3: repositories

The stand-in keeps the supervisor away from this machine's system:

* Host commands run here as the current user (UNOS_HOST_EXEC=local), so it
  refuses to run as root.
* The startup pool mounts, the SMB startup reconcile and the background loops
  are off (UNOS_DISABLE_BACKGROUND_TASKS=1).
* Docker is out of reach: UNOS_DOCKER_SOCKET and DOCKER_HOST name a socket that
  does not exist, so no container or image of this machine is touched.
* The off-device copy runs the fake restic (bin/). The state directory stands
  for /mnt, where its disk targets lie, as pools/ stands for /mnt/pools, and
  one filesystem holds all of it: the copy's check of the system disk is off
  (there is none to protect here), so that the specs can copy to a folder of
  testpool.
* Power actions are off: with host commands on this machine
  (UNOS_HOST_EXEC=local) the power API refuses to reboot, shut down or
  suspend it, to cancel its shutdowns and to change Wake-on-LAN (ADR-0233),
  and the UPS monitor sees a container (container=docker), so it never shuts
  this machine down.
* UNOS_INSTALL_DIR names a folder that does not exist: the updater never runs
  git or docker compose in a checkout.
* UNOS_* and DOCKER_* variables of the caller's environment are dropped.

The login rate limit is off: the E2E specs sign in once per test.

Usage: run_supervisor.py [--state-dir DIR] [--port PORT] [--fresh]
  --fresh  delete data/, pools/ and tmp/ first, then seed them as
           supervisor/tests/fixtures/init-test-data.sh seeds the Docker test
           stack: smb.conf with the share [e2e_seed] (webui/tests/shares.spec.ts)
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
import shutil
import sys
from pathlib import Path, PurePath

REPO = Path(__file__).resolve().parents[2]
SUPERVISOR = REPO / "supervisor"
DEFAULT_STATE = REPO / ".standin"
DEFAULT_PORT = 46180
# Marks a state directory that --fresh may empty. A directory without it is
# taken for one only when it holds nothing but the stand-in's own entries
# (e2e.sh makes logs/ and tls/ before the supervisor starts).
MARKER = ".unos-standin"
STATE_ENTRIES = frozenset({MARKER, ".gitignore", "data", "pools", "tmp", "bin", "fake-restic", "logs", "tls",
                           "e2e.lock"})
# The fake restic that the off-device copy runs here (bin/restic).
FAKE_RESTIC = SUPERVISOR / "tests" / "fake_restic.py"

# Module globals of supervisor/src that hold a path under /data. Each one is
# replaced by the same path under the stand-in's data directory
# (/data/audit/audit.jsonl -> <state>/data/audit/audit.jsonl), keeping its type
# (str or Path). supervisor/tests/test_standin.py fails when a /data path is
# left anywhere in the loaded modules.
DATA_GLOBALS: dict[str, tuple[str, ...]] = {
    "src.audit.router": ("AUDIT_FILE",),
    "src.backup.router": ("BACKUP_DIR",),
    "src.health.router": ("RAID_STATUS_FILE", "SMART_HISTORY_FILE"),
    "src.notifications.router": ("CONFIG_FILE", "LOGS_FILE"),
    "src.offsite.service": ("SETTINGS_FILE", "SECRETS_FILE", "STATUS_FILE", "DATA_DIR"),
    "src.power.router": ("CONFIG_FILE", "UPS_CONFIG_FILE", "WOL_FILE"),
    "src.scheduler.router": ("TASKS_FILE", "RUNS_FILE", "SCHEDULED_BACKUP_DIR", "DB_FILE", "BACKUP_KEYS_FILE"),
    "src.security.router": ("FIREWALL_RULES_FILE", "CERTS_DIR", "CADDY_CERTS_DIR"),
    "src.updates.updater": ("DATA_DIR", "UPDATE_STATE_FILE", "UPDATE_HISTORY_FILE", "UPDATE_CONFIG_FILE"),
}

# Module globals that name a file of the host outside /data, and the file in
# the data directory that replaces it.
HOST_FILE_GLOBALS: dict[str, dict[str, str]] = {
    "src.storage.pools": {"FSTAB_PATH": "fstab", "FSTAB_BACKUP_PATH": "fstab.unos-backup"},
    "src.updates.updater": {"BACKUP_DIR": "update-backups"},
}

SEED_SMB_CONF = """[global]
   workgroup = WORKGROUP
   server string = UNOS NAS Test
   security = user
   map to guest = bad user
   log file = /var/log/samba/log.%m
   max log size = 50

[e2e_seed]
   path = {pools}/testpool/e2e-seed
   comment = Seeded for E2E
   read only = no
   valid users = e2e_nobody
   vfs objects = fruit
"""


def stand_in_environment(state: Path) -> dict[str, str]:
    """The environment variables the stand-in's supervisor runs with."""
    data = state / "data"
    no_docker = data / "no-docker.sock"
    return {
        "UNOS_DB_PATH": str(data / "unos.db"),
        # Empty: the secret is generated into data/jwt_secret, as in production.
        "UNOS_JWT_SECRET": "",
        "UNOS_SMB_CONF_PATH": str(data / "smb.conf"),
        "UNOS_NFS_EXPORTS_PATH": str(data / "exports"),
        "UNOS_POOLS_BASE_PATH": str(state / "pools"),
        "UNOS_APP_CATALOG_PATH": str(data / "app-catalog.json"),
        "UNOS_DOCKER_SOCKET": str(no_docker),
        "DOCKER_HOST": f"unix://{no_docker}",
        "UNOS_INSTALL_DIR": str(state / "no-install"),
        "UNOS_HOST_EXEC": "local",
        "UNOS_DISABLE_BACKGROUND_TASKS": "1",
        "container": "docker",
        "TMPDIR": str(state / "tmp"),
        # The fake restic of the off-device copy first (ADR-0337).
        "PATH": f"{state / 'bin'}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
    }


def prepare(state: Path, *, fresh: bool) -> None:
    """Create the state directory's layout; with ``fresh``, start from new data."""
    # Resolved as configure() resolves it: the seeded share's path must be under the pools base.
    state = state.resolve()
    state.mkdir(parents=True, exist_ok=True)
    marker = state / MARKER
    if fresh:
        foreign = sorted(entry.name for entry in state.iterdir() if entry.name not in STATE_ENTRIES)
        if not marker.exists() and foreign:
            raise SystemExit(
                f"run_supervisor.py: {state} holds {', '.join(foreign)} and no {MARKER} file: "
                "not a stand-in state directory, nothing deleted"
            )
        for name in ("data", "pools", "tmp", "fake-restic"):
            shutil.rmtree(state / name, ignore_errors=True)
    marker.write_text("State of the local E2E stand-in (scripts/standin/run_supervisor.py).\n")
    # Ignored by git wherever the directory is, whatever the repository's .gitignore says.
    (state / ".gitignore").write_text("*\n")
    data, pools = state / "data", state / "pools"
    for folder in (data, pools / "testpool" / "e2e-seed", state / "tmp"):
        folder.mkdir(parents=True, exist_ok=True)
    smb_conf = data / "smb.conf"
    if not smb_conf.exists():
        smb_conf.write_text(SEED_SMB_CONF.format(pools=pools))
    for name in ("exports", "fstab"):
        (data / name).touch()
    _install_fake_restic(state)


def _install_fake_restic(state: Path) -> None:
    """bin/restic and bin/ionice, the fake of supervisor/tests/fake_restic.py with its state in
    fake-restic/, run by this Python."""
    spec = importlib.util.spec_from_file_location("standin_fake_restic", FAKE_RESTIC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.install(state / "bin", state / "fake-restic", python=sys.executable)


def _replacement(original: object, new: Path) -> object:
    return new if isinstance(original, PurePath) else str(new)


def configure(state: Path) -> Path:
    """Point the supervisor at ``state`` and import it; return the data directory.

    Sets the environment before the first import of the supervisor (its
    settings are read once), then imports src.main and redirects the module
    globals of DATA_GLOBALS and HOST_FILE_GLOBALS. Call once per process,
    after prepare().
    """
    state = state.resolve()
    data = state / "data"
    for name in list(os.environ):
        if name.startswith(("UNOS_", "DOCKER_")):
            del os.environ[name]
    os.environ.update(stand_in_environment(state))
    if str(SUPERVISOR) not in sys.path:
        sys.path.insert(0, str(SUPERVISOR))

    importlib.import_module("src.main")

    for module_name, names in DATA_GLOBALS.items():
        module = importlib.import_module(module_name)
        for name in names:
            original = getattr(module, name)
            text = str(original)
            if not (text == "/data" or text.startswith("/data/")):
                raise SystemExit(
                    f"run_supervisor.py: {module_name}.{name} is {text!r}, not a path under /data: "
                    "update DATA_GLOBALS"
                )
            setattr(module, name, _replacement(original, data / os.path.relpath(text, "/data")))
    for module_name, files in HOST_FILE_GLOBALS.items():
        module = importlib.import_module(module_name)
        for name, file_name in files.items():
            setattr(module, name, _replacement(getattr(module, name), data / file_name))

    # The file manager reads the mount table before it deletes a folder or
    # moves one to another filesystem (src/files/router.py). Without /proc
    # (macOS): an empty table; the st_dev checks still run.
    files = importlib.import_module("src.files.router")
    if not os.path.exists(files.MOUNTINFO):
        files.MOUNTINFO = str(data / "mountinfo")
        Path(files.MOUNTINFO).touch()

    # The off-device copy (ADR-0337): its disk targets lie below the state directory, which stands for
    # /mnt as pools/ stands for /mnt/pools. One filesystem holds all of it, so nothing here is the
    # system disk that a copy must stay off.
    offsite = importlib.import_module("src.offsite.service")
    offsite.MOUNTS_BASE = str(state)
    offsite.on_system_disk = lambda fd: False

    # 5 attempts per minute and client: the specs sign in through the form once per test.
    importlib.import_module("src.auth.router").limiter.enabled = False
    return data


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE, help="default: .standin/ in the repository")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port on 127.0.0.1 (default {DEFAULT_PORT})")
    parser.add_argument("--fresh", action="store_true", help="start from a new data directory")
    args = parser.parse_args(argv)

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SystemExit(
            "run_supervisor.py: refusing to run as root: the stand-in runs host commands on this machine"
        )
    state = args.state_dir.resolve()
    prepare(state, fresh=args.fresh)
    configure(state)

    import uvicorn
    from src import main as supervisor

    uvicorn.run(
        supervisor.app,
        host="127.0.0.1",
        port=args.port,
        # front.mjs is the proxy, as Caddy is in production.
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1,::1",
        log_level="warning",
        # As in supervisor/Dockerfile.
        ws_max_size=65536,
        ws_per_message_deflate=False,
        limit_concurrency=128,
    )


if __name__ == "__main__":
    main()
