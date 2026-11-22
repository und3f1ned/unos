# supervisor/: the FastAPI backend

Python 3.11 and FastAPI. The package is `src`: tests import `src.*`, some from `supervisor/` as cwd.

## Code (ADR-0012)

- Pydantic models for request and response bodies, and type hints. Read command-line tools' JSON
  output (`lsblk -J`, `smartctl -j`); errors are `HTTPException(status, detail="...")`.
- `make lint` checks the types of `src/` with mypy and `mypy.ini` (ADR-0447): a field's default is
  `Field(default=...)`, a table's column `Mapped[...] = mapped_column(...)` with the nullability of
  the table, and an error kept in the code has `# type: ignore[<code>]` with its reason.
- Background loops start and stop only in the lifespan of `src/main.py` (ADR-0043).
- Paths below the pools are opened one component at a time without following symlinks:
  `open_dir_nofollow` and `open_subdirs_nofollow` of `src/security/validators.py` (ADR-0042).

## Logging (ADR-0398)

- `logger = logging.getLogger(__name__)`; never `print()` (a test fails for one). `src/logging_setup.py`
  alone sets the format and levels; do not call `basicConfig` or add handlers. Never log a password,
  token, session key or TOTP code, nor an exception whose text holds one.
- A handler that catches an unexpected exception and answers 500 or 503 calls `logger.exception(...)`
  first, once; one that maps an expected error to 400, 404 or 409 does not.
- INFO only for a real change of state. A loop, a sampler or a request that changes nothing logs
  nothing (ADR-0214); `tests/test_logging.py` checks the loops.

## The host boundary (ADR-0020)

- The supervisor runs in a container with `pid: host`. Whatever must read or change the host
  (mounts, `/etc/fstab`, disks, Samba, host accounts) goes through `src/host.py`: `run_on_host`,
  `run_on_host_async`, `read_host_file`, `write_host_file_atomic`, `sync_host_path`. A plain
  `subprocess.run` reaches only the container: `tests/test_host_command_layer.py` fails for one, or
  for nsenter, in the system, security, logs, NFS, network and power modules (ADR-0416).
- Commands are argv lists, values validated (ADR-0293); secrets go in `input=`, not argv (ADR-0125).
- Call `host.run_on_host(...)` after `from .. import host`: tests replace the attribute
  `src.host.run_on_host`, and a name imported with `from ..host import run_on_host` escapes them.
- A program new to the host joins `HOST_TOOLS` with its Debian package, or `OPTIONAL_HOST_TOOLS`
  when `deploy/install.sh` does not install it: the startup check leaves those out, and the feature
  that runs one says when the host lacks it (exportfs, ADR-0321). Blocking work (host
  commands, smartctl, bcrypt, Docker, e-mail) runs off the event loop with a timeout (ADR-0021).
  The timeout of `run_on_host` ends only nsenter, not the command on the host: a command that
  can run long runs under the host's `timeout` (ADR-0258).
- btrfs and smartctl that run in the container (scrubs, a balance's status, pause and cancel, the
  stats reset, a pool's profiles for a disk's add or removal, SMART tests and reads), the pgrep of a disk's stuck power read (ADR-0378)
  and openssl on the certificates of `/data/certs` (ADR-0416) go
  through `host.run_in_container`: at its timeout it kills
  the command's process group and
  leaves the wait to a daemon thread, so a command stuck in the kernel holds no worker thread;
  it runs one run of a command at a time, and none while a killed run of it is still stuck
  (ADR-0290). The fake hosts refuse it as they refuse `subprocess.run`. The Snapshots page's
  `run_cmd` does not use it yet. smartctl goes through `host.run_smartctl`, which first makes the
  container's `/dev/<disk>` the host's disk from `/sys/block` (a disk attached after the start has
  no node there, ADR-0414); `tests/test_hot_plugged_smart.py` fails for the name `smartctl`
  anywhere else in `src/`, but the members of its JSON.

## Routers and roles (ADR-0030)

- `main.py` makes every router administrator-only (the `_ADMIN_ONLY` loop) except `auth`,
  `system`, `security`, `audit` and the WebSockets, whose endpoints check roles one by one;
  `GET /api/v1/health` alone is public. A new router joins `_ADMIN_ONLY`, and a route that a
  regular user or nobody signed in may reach is listed in `USER_ALLOWED` or `PUBLIC` of
  `tests/test_authz.py`, which fails for a route without a decision. Request bodies are limited
  before the credentials are checked (ADR-0062).
- An API token's scope (ADR-0392) is checked in `authenticate_token` for every request and
  WebSocket. A new router's prefix joins `AREAS` of `src/auth/scopes.py`, a new WebSocket its
  `SOCKET_AREAS`, and a GET whose answer holds secrets, or a POST that only reads, its
  `OPERATION_KINDS`; `tests/test_token_scopes.py` walks every route for each scope and fails for
  a prefix without an area.

## Writes and data paths

- Write only on a real change of state; temporary files go to `TMPDIR` (the `/tmp` tmpfs): the
  container's writable layer is on the system disk (ADR-0171, `tests/test_temp_files.py`).
- Every path of the data directory comes from `src/paths.py`, which reads `UNOS_DATA_DIR` once at
  its import (ADR-0419): a module binds it to a name of its own, which tests move with monkeypatch;
  no test changes the variable in its own process. `tests/test_standin.py` fails for a `/data`
  literal or a path made from `DATA_DIR` anywhere else in `src/` (but the apps' paths inside their
  containers). A host path gets a setting of its own in `src/config.py`, which
  the stand-in sets in its environment: `scripts/standin/run_supervisor.py` patches no constant.
- The schema is the Alembic revisions of `src/migrations/versions/` (ADR-0444): `init_db` brings
  unos.db to the head at the start, before the first request, in one transaction; nothing runs
  `create_all`. A change to a model, a new table included, ships with a revision in the same
  change: from `supervisor/`, with a scratch folder (`mkdir -p $TMPDIR/unos-cli`; the import of
  `src` writes a JWT secret into `UNOS_DATA_DIR`),
  `UNOS_DATA_DIR=$TMPDIR/unos-cli .venv/bin/alembic -x db=$TMPDIR/unos-cli/unos.db upgrade head`,
  then the same with `revision --autogenerate --rev-id NNNN -m "<what changes>"` (the next number).
  Read the file it writes: an existing table changes in `op.batch_alter_table`, data moves with
  `op.execute`, and there is no `downgrade` (ADR-0207). Never edit a committed revision, the
  baseline `0001` included. `tests/test_migrations.py` fails while the models differ from the head.
  SQLite does not enforce foreign keys here, so code deletes the dependent rows in the same
  transaction.

## SMB (`src/shares/`)

| Module | Holds |
| --- | --- |
| `models.py`, `constants.py` | the tables (ADR-0074); the uid range, the group `unos-smb` and the name rules (ADR-0107, ADR-0113) |
| `account_state.py`, `accounts.py` | the SMB state of a user, derived and never stored; host accounts, Samba's passdb, the smb_accounts rows |
| `service.py` | `smb_lock()`, the import, apply, the share API's logic, the startup reconcile, the restore steps, `share_path_problem` |
| `smbconf.py`, `smb.py` | smb.conf as text (pure); the file on the host, testparm, reload, close-share |
| `router.py`, `schemas.py`, `interfaces.py`; `repair.py`, `repair_router.py` | the share API and the types the modules share; Repair permissions, a background job (ADR-0363) |
| `groups.py` | groups of users and their API; smb.conf names their active members (ADR-0385) |
| `listen.py` | the network interfaces that SMB listens on: the host's interfaces, smbd's listeners, the choice and Restart SMB (ADR-0391) |
| `exports.py`, `nfs.py` | NFS: export files as exportfs reads them (pure); UNOS's own export file on the host, exportfs and the export rules (ADR-0321, ADR-0293) |
| `iscsi.py`, `iscsi_router.py` | iSCSI targets: LIO in the host's configfs, the LUN files, the CHAP secrets file, the restore at the start and the API (ADR-0418) |

The SMB flows of users (allow, turn on, password changes) are in `src/auth/router.py`. Rules:
- UNOS alone writes smb.conf, rendered from the database (ADR-0074); every apply is staged,
  checked by testparm and installed atomically (ADR-0089).
- Every change of SMB state holds `service.smb_lock()` and reloads the user and account rows
  after taking it (ADR-0080, ADR-0081).
- A share section states each restriction in a line of its own (ADR-0088). Values are checked when
  stored, when rendered (restored databases are untrusted, ADR-0144) and by `smbconf.py`'s guards.
  A share's protocol options (`smb_share_options`, ADR-0380) are checked the same way: their vfs
  modules on the host and the snapshots of the share's folder, when turned on and at every render.
- `share_path_problem` alone judges share paths (ADR-0145, ADR-0146).
- Only marked host accounts and their passdb entries are touched (ADR-0108), each after a fresh
  read right before a destructive command (ADR-0192).
- An unchanged state renders byte-identical text and writes nothing (ADR-0082); audit entries
  record real changes only, with the SMB actions of `AuditAction` (ADR-0320).
- Administrators never have SMB (ADR-0117); a user's own SMB flows answer fixed texts (ADR-0131).

## Tests

- Host commands sent through `src/host.py` meet fakes: `install_fake_host` of
  `tests/test_pool_persistence.py`, `tests/samba_fake.py` for Samba (ADR-0189). A command a fake
  does not know fails the test. Shared fakes live in test modules that other tests import.
- Linux-only tests skip elsewhere with their reason (`os.O_PATH`, `/proc/self/fdinfo`, POSIX ACLs,
  `O_TMPFILE`); CI's Unit Tests job and `scripts/dev/linux-tests.sh` run them (ADR-0462).
  `tests/test_smbconf_testparm.py` needs `scripts/dev/bin/testparm` on PATH (the device-checks
  skill).
- After an edit of a file that tests read, run those tests: `test_deploy_scripts.py` (deploy/,
  `docs/INSTALL.md`, `docs/TESTING.md`, `scripts/test-cycle.sh`, `scripts/audit-python.sh`, the
  workflows, Dockerfiles, package files), `test_emergency.py` (`deploy/reset-admin.sh`,
  `docs/RECOVERY.md` and the links to it), `test_authz.py`, `test_secret_bootstrap.py`,
  `test_request_limits.py`, `test_standin.py`, `test_smb_accounts.py`, `test_smb_models.py` and
  those of `make test-fast`.
