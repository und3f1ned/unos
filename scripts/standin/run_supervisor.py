#!/usr/bin/env python3
"""Run the real supervisor on this machine as the backend of the local E2E stand-in.

The E2E specs run only against local targets, by default this stand-in (ADR-0215).
scripts/standin/e2e.sh starts it; supervisor/tests/test_standin.py checks it.
Everything the supervisor writes goes under the state directory (default:
.standin/ at the repository root, which ignores itself in git). The supervisor
finds it through its environment alone (stand_in_environment): UNOS_DATA_DIR
and the settings of the host paths. No constant of its modules is patched
(ADR-0419); configure() replaces only functions, and turns the login limit off:
no setting may turn a check of a NAS off.

    data/   UNOS_DATA_DIR: the SQLite database with its jwt_secret and every
            file that the supervisor keeps in its data directory
            (supervisor/src/paths.py); smb.conf and the NFS exports; and
            stand-ins for the host files it reads or changes, each through its
            setting: an empty fstab, the updater's backups and the served
            certificate's folder
    pools/  UNOS_POOLS_BASE_PATH; testpool/ is the folder of a pool that UNOS
            does not manage, as on the Docker test stack
    tmp/    TMPDIR of the supervisor
    bin/    first on the supervisor's PATH: restic, the fake of
            supervisor/tests/fake_restic.py, and a pass-through ionice and
            unshare, for the off-device copy (ADR-0337); restic is not
            installed here
    fake-restic/  the fake's calls, the stores of its s3: repositories, and
            hostname, where each restic writes its host name instead of
            /proc/sys/kernel/hostname of a UTS namespace of its own: the fake
            unshare gives it this file

The stand-in keeps the supervisor away from this machine's system:

* Host commands run here as the current user (UNOS_HOST_EXEC=local), so it
  refuses to run as root.
* The startup pool mounts, the SMB startup reconcile and the background loops
  are off (UNOS_DISABLE_BACKGROUND_TASKS=1).
* Docker is out of reach: UNOS_DOCKER_SOCKET and DOCKER_HOST name a socket that
  does not exist, so no container or image of this machine is touched.
* The off-device copy runs the fake restic (bin/). The state directory stands
  for /mnt (UNOS_MOUNTS_BASE_PATH), where its disk targets lie, as pools/
  stands for /mnt/pools, and
  one filesystem holds all of it: the copy's check of the system disk is off
  (there is none to protect here), so that the specs can copy to a folder of
  testpool. No S3 store is connected to before restic runs: the fake keeps
  its s3: repositories in fake-restic/.
* Power actions are off: with host commands on this machine
  (UNOS_HOST_EXEC=local) the power API refuses to reboot, shut down or
  suspend it, to cancel its shutdowns and to change Wake-on-LAN (ADR-0233),
  and the UPS monitor sees a container (container=docker), so it never shuts
  this machine down.
* UNOS_INSTALL_DIR names a folder that does not exist: the updater never runs
  git or docker compose in a checkout.
* The served certificate (ADR-0404) is data/served-certificate/served.pem, which
  front.mjs serves from its next connection on, while it loads, as Caddy would
  after a reload: the supervisor's handshake asks front.mjs (--front-https-port,
  UNOS_WEBUI_TLS_HOST and UNOS_WEBUI_TLS_PORT) what it serves, and the reload,
  without Docker, only refuses a file that
  Python's ssl cannot load, as Caddy refuses one it cannot.
* UNOS_* and DOCKER_* variables of the caller's environment are dropped.

The login rate limit is off: the E2E specs sign in once per test.

Usage: run_supervisor.py [--state-dir DIR] [--port PORT] [--fresh] [--front-https-port PORT]
  --fresh  delete data/, pools/ and tmp/ first, then seed them as
           supervisor/tests/fixtures/init-test-data.sh seeds the Docker test
           stack: smb.conf with the share [e2e_seed] (webui/tests/shares.spec.ts)
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import logging
import os
import shutil
import ssl
import sys
from pathlib import Path

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

# The HTTPS port of front.mjs, as e2e.sh starts it by default (E2E_HTTPS_PORT).
DEFAULT_FRONT_HTTPS_PORT = 46443

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


def stand_in_environment(state: Path, *, front_https_port: int = DEFAULT_FRONT_HTTPS_PORT) -> dict[str, str]:
    """The environment variables the stand-in's supervisor runs with."""
    data = state / "data"
    no_docker = data / "no-docker.sock"
    return {
        # Every data path of the supervisor, the database included (supervisor/src/paths.py, ADR-0419).
        "UNOS_DATA_DIR": str(data),
        # Empty: the secret is generated into data/jwt_secret, as in production.
        "UNOS_JWT_SECRET": "",
        # The host paths, each through its own setting.
        "UNOS_SMB_CONF_PATH": str(data / "smb.conf"),
        "UNOS_NFS_EXPORTS_PATH": str(data / "exports"),
        "UNOS_POOLS_BASE_PATH": str(state / "pools"),
        "UNOS_FSTAB_PATH": str(data / "fstab"),
        "UNOS_UPDATE_BACKUP_DIR": str(data / "update-backups"),
        # The volume that only the supervisor's and the web UI's containers mount (ADR-0404).
        "UNOS_SERVED_CERTIFICATE_DIR": str(data / "served-certificate"),
        # The off-device copy's disk targets lie below the state directory, which stands for /mnt as
        # pools/ stands for /mnt/pools (ADR-0337).
        "UNOS_MOUNTS_BASE_PATH": str(state),
        # front.mjs is the web UI's Caddy.
        "UNOS_WEBUI_TLS_HOST": "127.0.0.1",
        "UNOS_WEBUI_TLS_PORT": str(front_https_port),
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


def reload_front(tls: str) -> None:
    """In place of `caddy reload` in the web UI's container (src.security.serving.reload_caddy):
    front.mjs reads the served file at every connection, so only Caddy's refusal of a file that it
    cannot load is played here, with Python's ssl."""
    serving = importlib.import_module("src.security.serving")
    if tls not in serving.TLS_MODES:
        raise ValueError(f"UNOS_TLS must be one of {serving.TLS_MODES}, not {tls!r}")
    if tls == "served":
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(serving.served_path())
        except (OSError, ssl.SSLError) as e:
            raise serving.CaddyRefused(f"loading certificates: {e}") from e


def configure(state: Path, *, front_https_port: int = DEFAULT_FRONT_HTTPS_PORT) -> Path:
    """Point the supervisor at ``state`` and import it; return the data directory.

    Sets the environment (stand_in_environment) before the first import of the
    supervisor, which reads its settings and UNOS_DATA_DIR once, then imports
    src.main and replaces the functions that the stand-in fakes. The web UI's
    Caddy is front.mjs on 127.0.0.1:``front_https_port``. Call once per
    process, after prepare(), before anything imports the supervisor.
    """
    state = state.resolve()
    data = state / "data"
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SystemExit(
            "run_supervisor.py: refusing to run as root: the stand-in runs host commands on this machine"
        )
    if "src" in sys.modules:
        raise SystemExit("run_supervisor.py: the supervisor was imported before configure(): "
                         "its paths are not the stand-in's")
    for name in list(os.environ):
        if name.startswith(("UNOS_", "DOCKER_")):
            del os.environ[name]
    os.environ.update(stand_in_environment(state, front_https_port=front_https_port))
    if str(SUPERVISOR) not in sys.path:
        sys.path.insert(0, str(SUPERVISOR))

    importlib.import_module("src.main")

    # The off-device copy (ADR-0337): one filesystem holds the whole state directory, so nothing here
    # is the system disk that a copy must stay off, nor the root filesystem where an unmounted pool
    # would be. No setting turns these checks off on a NAS: the functions are replaced.
    offsite = importlib.import_module("src.offsite.service")
    offsite.on_system_disk = lambda fd: False
    offsite.on_system_filesystem = lambda fd: False
    # Its fake restic keeps an S3 store in its state directory: no store to connect to first.
    offsite.probe_store = lambda endpoint: None

    # The file manager reads the mount table before it deletes a folder or moves one to another
    # filesystem (src/files/router.py). Without /proc (macOS): an empty table; the st_dev checks
    # still run.
    files = importlib.import_module("src.files.router")
    if not os.path.exists(files.MOUNTINFO):
        files._read_mount_points = lambda: []

    # The served certificate (ADR-0404): front.mjs is the web UI's Caddy, reloaded without Docker.
    importlib.import_module("src.security.serving").reload_caddy = reload_front

    # 5 attempts per minute and client: the specs sign in through the form once per test. No setting
    # turns the limit off on a NAS.
    importlib.import_module("src.auth.router").limiter.enabled = False
    return data


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE, help="default: .standin/ in the repository")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port on 127.0.0.1 (default {DEFAULT_PORT})")
    parser.add_argument("--fresh", action="store_true", help="start from a new data directory")
    parser.add_argument("--front-https-port", type=int, default=DEFAULT_FRONT_HTTPS_PORT,
                        help=f"the HTTPS port of front.mjs (default {DEFAULT_FRONT_HTTPS_PORT})")
    args = parser.parse_args(argv)

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SystemExit(
            "run_supervisor.py: refusing to run as root: the stand-in runs host commands on this machine"
        )
    state = args.state_dir.resolve()
    prepare(state, fresh=args.fresh)
    configure(state, front_https_port=args.front_https_port)

    import uvicorn
    from src import main as supervisor
    from src.logging_setup import logging_config

    uvicorn.run(
        supervisor.app,
        host="127.0.0.1",
        port=args.port,
        # front.mjs is the proxy, as Caddy is in production.
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1,::1",
        # The supervisor's JSON lines (ADR-0398), as in the image, from warnings up.
        log_config=logging_config(logging.WARNING),
        # As in supervisor/Dockerfile.
        ws_max_size=65536,
        ws_per_message_deflate=False,
        limit_concurrency=128,
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
