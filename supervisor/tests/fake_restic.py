"""A fake restic, for the backend tests of the off-device copy and for the local stand-in (ADR-0337).

restic is not installed on the workstation. This script stands in for it where the tests and
scripts/standin/run_supervisor.py put it on PATH, through a small wrapper (``install``): it keeps a
repository of its own with what the commands of src/offsite/ need, so that what ``backup`` stored
comes back from ``dump``, ``restore`` and ``snapshots``:

* the repository is the folder that RESTIC_REPOSITORY names (``local:<path>`` or a path), or, for
  ``s3:<endpoint>/<bucket>[/<prefix>]``, a folder below ``<state>/s3``, for a REST server
  ``rest:https://<host>:<port>/[<prefix>/]`` and for SFTP ``sftp://<user>@<host>:<port>/<path>`` a
  folder of the server that the fake plays in ``<state>/rest/<host>_<port>`` and
  ``<state>/sftp/<host>_<port>`` (below; ADR-0422); it holds ``config``, a
  ``keys/key`` with the digest of the password and one JSON file per snapshot in ``snapshots/``,
  with the files of the snapshot in it (the tests copy small folders);
* the commands init, cat config, unlock, backup (of folders, with --exclude, or of standard input
  with --stdin and --stdin-filename), forget with --tag, --keep-daily, --keep-weekly and
  --keep-monthly, check, dump, snapshots and restore, with restic's exit codes: 10 for a
  repository that does not exist, 12 for a wrong password, 1 for other failures;
* global options anywhere: --no-cache, --cache-dir, --limit-upload, --limit-download, --json,
  --quiet, --no-lock and --retry-lock; backup prints its summary as restic does with --json
  --quiet, and keeps the host that --host names;
* locks as restic takes them, files in ``locks/`` with their host name, process id and time: cat,
  backup, dump, restore and snapshots a shared one, forget and check an exclusive one, which
  conflicts with any other lock, as a shared one does with an exclusive one. A conflict fails the
  command at once with exit code 11, or after the time of --retry-lock; --no-lock takes none.
  unlock removes the stale locks by restic's rule: older than 30 minutes, or of its own host name
  with a process that is gone. SIGTERM and SIGINT end the fake as they end restic, which removes
  its lock first; while a fault blocks or sleeps, it holds its lock;
* backup of folders exits with 3 when files could not be read: it saves the snapshot without them
  and prints an error for each, a JSON line with message_type "error" on standard error. A file that
  vanished before restic opened it is left out without a word, as restic 0.18 does (filterNotExist);
  only one that vanished between its first open and the next is reported;
* each call records the host name that its wrapper wrote into ``<state>/hostname`` (on the device
  /proc/sys/kernel/hostname of the UTS namespace that ``unshare --uts`` made for that restic), and
  ``install`` puts a pass-through ``unshare`` next to the pass-through ``ionice``: it gives the
  command ``<state>/hostname`` in place of /proc/sys/kernel/hostname, the file of the namespace that
  it does not make.

* a REST server (``rest_server``): restic's credentials in RESTIC_REST_USERNAME and
  RESTIC_REST_PASSWORD, which ``credentials.json`` of the server's folder checks when it is there
  (401 otherwise, as restic reports it), and its certificate: without ``certificate.pem`` (the
  server's own) and ``ca.pem`` (the CA that signed it) the server's is one of the system's roots,
  and a --cacert fails; with them, only a --cacert that holds one of them passes. Each opening appends ``{"user", "cacert"}`` (the file's content) to
  ``<state>/rest.jsonl``;
* an SFTP server (``sftp_server``), which restic reaches through the command of ``-o
  sftp.command``, never one of its own: the fake reads that command as UNOS builds it (ADR-0422):
  ``nsenter --target 1 --mount --net --`` (optional), ``/usr/bin/env -i /usr/bin/ssh -F /dev/null``, its ``-o``
  options, ``-p``, ``-l``, ``-s -- <host> sftp``, and checks the ones that keep passwords, agents,
  forwardings and other host keys out; a path ``/proc/<pid>/root/<path>`` is ``<path>`` of the
  supervisor's container. It checks the server's host key, ``host_key`` (made from the server's
  name when missing), against UserKnownHostsFile under its HostKeyAlias as ssh does with
  StrictHostKeyChecking yes, and writes it there with accept-new; the key of IdentityFile, which
  must be a file of mode 0600, against ``authorized_keys`` when that is there. Each opening
  appends ``{"argv", "identity_mode", "known_hosts"}`` (before the opening) to
  ``<state>/ssh.jsonl``.

``<state>`` is FAKE_RESTIC_STATE, which the wrapper sets. Each call appends a line to
``<state>/calls.jsonl``: its arguments, its environment and its process id. ``<state>/faults.json``
changes what a command does, by the command's name (``backup-stdin`` for the configuration's
backup): ``{"code": N, "stderr": "..."}`` fails it, ``{"sleep": S}`` waits S seconds first,
``{"block": true}`` writes ``<state>/blocked-<name>`` and waits for ``<state>/release-<name>``,
``{"garble": true}`` makes dump change a byte of what it writes, and backup of folders takes
``{"vanished": [paths]}`` (files gone while it ran, left out without a word; with ``"reported":
true``, reported as "no such file or directory") and ``{"unreadable": {path: reason}}`` (such as
"permission denied").

Standard library only: it runs with the stand-in's Python and the tests' alike.
"""

from __future__ import annotations

import base64
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import stat
import sys
import time
from datetime import datetime, UTC
from pathlib import Path
from urllib.parse import urlsplit

# The host name of a UTS namespace, which the fake unshare replaces with <state>/hostname.
UTS_HOSTNAME_FILE = "/proc/sys/kernel/hostname"
GLOBAL_FLAGS = {"--no-cache", "--json", "--quiet", "-q", "--no-lock"}
GLOBAL_VALUES = {"--cache-dir", "--limit-upload", "--limit-download", "--retry-lock", "-o", "--option", "--cacert"}
COMMAND_VALUES = {
    "--stdin-filename", "--tag", "--exclude", "--keep-daily", "--keep-weekly", "--keep-monthly", "--target",
    "--host", "--group-by",
}
# The lock that each command takes, as restic's do.
SHARED, EXCLUSIVE = "shared", "exclusive"
LOCKS = {"cat": SHARED, "backup": SHARED, "dump": SHARED, "restore": SHARED, "snapshots": SHARED,
         "forget": EXCLUSIVE, "check": EXCLUSIVE}


class Fatal(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def install(folder: Path, state: Path, python: str = sys.executable) -> Path:
    """Put ``restic`` (this script, with FAKE_RESTIC_STATE=``state``) and a pass-through ``ionice``
    into ``folder``; return ``folder``, for the front of PATH."""
    folder.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    restic = folder / "restic"
    restic.write_text(
        "#!/bin/sh\n"
        f"FAKE_RESTIC_STATE={shlex.quote(str(state))}\n"
        "export FAKE_RESTIC_STATE\n"
        f"exec {shlex.quote(python)} {shlex.quote(str(script))} \"$@\"\n"
    )
    ionice = folder / "ionice"
    ionice.write_text(
        "#!/bin/sh\n"
        "# ionice as the off-device copy calls it: the options it passes, then the command.\n"
        '[ "$1" = -c ] && [ "$2" = 2 ] && [ "$3" = -n ] && [ "$4" = 7 ] || '
        '{ echo "fake ionice: unexpected options: $*" >&2; exit 99; }\n'
        "shift 4\n"
        'exec "$@"\n'
    )
    unshare = folder / "unshare"
    unshare.write_text(
        "#!/bin/sh\n"
        "# unshare as the off-device copy calls it, without a namespace: the options, then the command.\n"
        "# The host name of the UTS namespace, /proc/sys/kernel/hostname, is <state>/hostname.\n"
        '[ "$1" = --uts ] && [ "$2" = -- ] || { echo "fake unshare: unexpected options: $*" >&2; exit 99; }\n'
        "shift 2\n"
        "for arg; do\n"
        "  shift\n"
        f'  [ "$arg" = {UTS_HOSTNAME_FILE} ] && arg={shlex.quote(str(state / "hostname"))}\n'
        '  set -- "$@" "$arg"\n'
        "done\n"
        'exec "$@"\n'
    )
    for path in (restic, ionice, unshare):
        path.chmod(0o755)
    return folder


# --- the arguments ------------------------------------------------------------------------------


def parse(argv: list[str]) -> tuple[str, list[str], dict[str, list[str]], set[str]]:
    """(command, its positional arguments, the options with values, the flags)."""
    command = None
    positional: list[str] = []
    values: dict[str, list[str]] = {}
    flags: set[str] = set()
    i = 0
    while i < len(argv):
        arg = argv[i]
        name, eq, inline = arg.partition("=")
        if arg == "--":  # the end of the options: the rest are arguments
            positional += argv[i + 1:]
            break
        if arg in GLOBAL_FLAGS or arg in ("--stdin", "--prune", "--read-data", "--with-cache"):
            flags.add(arg)
        elif name in GLOBAL_VALUES or name in COMMAND_VALUES or name == "--read-data-subset":
            if eq:
                values.setdefault(name, []).append(inline)
            else:
                i += 1
                if i >= len(argv):
                    raise Fatal(f"Fatal: flag needs an argument: {arg}")
                values.setdefault(name, []).append(argv[i])
        elif arg.startswith("-"):
            raise Fatal(f"Fatal: unknown flag: {arg}")
        elif command is None:
            command = arg
        else:
            positional.append(arg)
        i += 1
    if command is None:
        raise Fatal("Fatal: no command given")
    return command, positional, values, flags


# --- the repository -----------------------------------------------------------------------------


def state_dir() -> Path:
    return Path(os.environ["FAKE_RESTIC_STATE"])


def location() -> str:
    found = os.environ.get("RESTIC_REPOSITORY", "")
    if not found:
        raise Fatal("Fatal: Please specify repository location (-r or --repository-file)")
    return found


# --- the servers of rest: and sftp: repositories (ADR-0422) ---------------------------------------

SSH = "/usr/bin/ssh"
NSENTER = ["nsenter", "--target", "1", "--mount", "--net", "--"]
HOST_KEY_ALIAS = "unos-copy-target"
# The options of UNOS's ssh that the fake insists on, with their values.
SSH_OPTIONS = {
    "BatchMode": "yes", "IdentitiesOnly": "yes", "IdentityAgent": "none", "PasswordAuthentication": "no",
    "KbdInteractiveAuthentication": "no", "PreferredAuthentications": "publickey", "GlobalKnownHostsFile": "/dev/null",
    "HostKeyAlias": HOST_KEY_ALIAS, "UpdateHostKeys": "no", "ForwardAgent": "no", "ForwardX11": "no",
    "ClearAllForwardings": "yes", "PermitLocalCommand": "no", "ControlMaster": "no", "ControlPath": "none",
}
_values: dict[str, list[str]] = {}
_repository: Path | None = None


def _ssh_string(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + data


def ssh_key_line(seed: bytes) -> str:
    """An ssh-ed25519 public key as known_hosts holds it, "<type> <base64>", of 32 bytes of SEED."""
    blob = _ssh_string(b"ssh-ed25519") + _ssh_string(hashlib.sha256(seed).digest())
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


def server_folder(kind: str, host: str, port: int) -> Path:
    """The folder of the fake's REST or SFTP server at HOST:PORT."""
    return state_dir() / kind / f"{host.strip('[]')}_{port}"


def server_host_key(server: Path) -> str:
    """The host key of the SFTP server whose folder is SERVER, made from its name when it has none."""
    path = server / "host_key"
    if not path.exists():
        server.mkdir(parents=True, exist_ok=True)
        path.write_text(ssh_key_line(server.name.encode()) + "\n")
    return path.read_text().strip()


def public_key_of(private: str) -> str:
    """The public key, "<type> <base64>", of an unencrypted OpenSSH private key."""
    body = "".join(line for line in private.strip().splitlines() if not line.startswith("-----"))
    data = base64.b64decode(body)
    magic = b"openssh-key-v1\0"
    if not data.startswith(magic):
        raise Fatal("subprocess ssh: Load key: invalid format")
    i = len(magic)
    fields = []
    for _ in range(3):  # ciphername, kdfname, kdfoptions
        size = int.from_bytes(data[i:i + 4], "big")
        fields.append(data[i + 4:i + 4 + size])
        i += 4 + size
    if fields[0] != b"none":
        raise Fatal("subprocess ssh: Load key: the fake reads no encrypted key")
    i += 4  # the number of keys
    size = int.from_bytes(data[i:i + 4], "big")
    blob = data[i + 4:i + 4 + size]
    kind = blob[4:4 + int.from_bytes(blob[:4], "big")].decode()
    return f"{kind} {base64.b64encode(blob).decode()}"


def _container_path(path: str) -> str:
    """A file of the supervisor's container as the host's ssh opens it, /proc/<pid>/root/<path>:
    <path> where that does not resolve (macOS)."""
    found = re.fullmatch(r"/proc/\d+/root(/.*)", path)
    return found.group(1) if found and not os.path.exists(path) else path


def _log(name: str, entry: dict) -> None:
    with open(state_dir() / name, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _option_map() -> dict[str, str]:
    found = {}
    for item in _values.get("-o", []) + _values.get("--option", []):
        key, _, value = item.partition("=")
        found[key] = value
    return found


def _ssh_failed(program: str, lines: list[str]) -> Fatal:
    text = "".join(f"subprocess {program}: {line}\n" for line in lines)
    return Fatal(f"{text}Fatal: unable to open repository at {location()}: unable to start the sftp session, error: "
                 "error receiving version packet from server: server unexpectedly closed connection: unexpected EOF")


def sftp_server(where: str) -> Path:
    """The repository folder of an sftp: location, once the command of sftp.command has reached the
    fake's server as ssh would."""
    parts = urlsplit(where)
    user, host, port = parts.username or "", parts.hostname or "", parts.port or 22
    command = _option_map().get("sftp.command")
    if not command:
        raise Fatal("Fatal: the fake restic runs no ssh of its own: -o sftp.command is missing")
    argv = command.split()
    program = argv[0]
    if argv[:len(NSENTER)] == NSENTER:
        argv = argv[len(NSENTER):]
    if argv[:3] != ["/usr/bin/env", "-i", SSH]:
        raise Fatal(f"Fatal: fake ssh: the command is not /usr/bin/env -i {SSH}: {command}")
    argv = argv[2:]
    options: dict[str, str] = {}
    flags: list[str] = []
    positional: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("-o", "-p", "-l", "-F"):
            if i + 1 >= len(argv):
                raise Fatal(f"Fatal: fake ssh: {arg} needs a value")
            value = argv[i + 1]
            if arg == "-o":
                key, eq, val = value.partition("=")
                if not eq or key in options:
                    raise Fatal(f"Fatal: fake ssh: bad or repeated option {value}")
                options[key] = val
            else:
                options[arg] = value
            i += 2
        elif arg == "-s":
            flags.append(arg)
            i += 1
        elif arg == "--":
            positional = argv[i + 1:]
            break
        else:
            raise Fatal(f"Fatal: fake ssh: unexpected argument {arg}")
    wanted = {**SSH_OPTIONS, "-F": "/dev/null", "-p": str(port), "-l": user}
    wrong = {k: options.get(k) for k, v in wanted.items() if options.get(k) != v}
    if wrong or flags != ["-s"] or positional != [host.strip("[]"), "sftp"]:
        raise Fatal(f"Fatal: fake ssh: not the command of UNOS: {wrong}, {flags}, {positional}")
    strict = options.get("StrictHostKeyChecking")
    if strict not in ("yes", "accept-new"):
        raise Fatal(f"Fatal: fake ssh: StrictHostKeyChecking {strict}")
    known = Path(_container_path(options.get("UserKnownHostsFile", "")))
    identity = Path(_container_path(options.get("IdentityFile", "")))
    server = server_folder("sftp", host, port)
    expected = server_host_key(server)
    before = known.read_text() if known.is_file() else None
    mode = stat.S_IMODE(identity.stat().st_mode) if identity.is_file() else None
    _log("ssh.jsonl", {"argv": command.split(), "identity_mode": mode, "known_hosts": before})
    if before is None:
        raise _ssh_failed(program, [f"Can't open user-known-hosts file {known}"])
    # The key exchange comes first: a server without a key of the algorithms offered ends it there.
    algorithms = options.get("HostKeyAlgorithms")
    offered = {"ssh-rsa": "rsa-sha2-512"}.get(expected.split()[0], expected.split()[0])
    if algorithms is not None and offered not in algorithms.split(","):
        raise _ssh_failed(program, [f"Unable to negotiate with {host} port {port}: no matching host key type found. "
                                    f"Their offer: {expected.split()[0]}"])
    pinned = [" ".join(line.split()[1:3]) for line in before.splitlines() if line.split()[:1] == [HOST_KEY_ALIAS]]
    if pinned and expected not in pinned:
        raise _ssh_failed(program, ["@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@",
                                    "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @",
                                    f"Host key for {HOST_KEY_ALIAS} has changed and you have requested strict checking.",
                                    "Host key verification failed."])
    if not pinned:
        if strict == "yes":
            raise _ssh_failed(program, [f"No ED25519 host key is known for {HOST_KEY_ALIAS} and you have requested "
                                        "strict checking.", "Host key verification failed."])
        with open(known, "a") as f:
            f.write(f"{HOST_KEY_ALIAS} {expected}\n")
    if mode != 0o600:
        raise _ssh_failed(program, ["Permissions for the identity file are too open or it is missing",
                                    f"{user}@{host}: Permission denied (publickey)."])
    authorized = server / "authorized_keys"
    if authorized.exists():
        offered = public_key_of(identity.read_text())
        allowed = {" ".join(line.split()[:2]) for line in authorized.read_text().splitlines() if line.strip()}
        if offered not in allowed:
            raise _ssh_failed(program, [f"{user}@{host}: Permission denied (publickey)."])
    path = parts.path[1:]
    if not path:
        raise Fatal(f"Fatal: invalid backend {where!r}, no directory specified")
    return server / "files" / (path.lstrip("/") if path.startswith("/") else f"home/{user}/{path}")


def rest_server(where: str) -> Path:
    """The repository folder of a rest: location, once its credentials and certificate passed."""
    url = where[len("rest:"):]
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password:
        raise Fatal(f"Fatal: the fake REST server takes https:// without credentials in the URL: {url}")
    server = server_folder("rest", parts.hostname or "", parts.port or 443)
    user, secret = os.environ.get("RESTIC_REST_USERNAME", ""), os.environ.get("RESTIC_REST_PASSWORD", "")
    cacert = _values.get("--cacert", [])
    content = Path(cacert[0]).read_text() if cacert and Path(cacert[0]).is_file() else None
    _log("rest.jsonl", {"user": user, "cacert": content, "cacert_path": cacert[0] if cacert else None})
    if cacert and content is None:
        raise Fatal(f"Fatal: unable to load root certificates: open {cacert[0]}: no such file or directory")
    roots = [path.read_text().strip() for path in (server / "certificate.pem", server / "ca.pem") if path.exists()]
    if (not roots and content is not None) or (roots and (content is None or not any(r in content for r in roots))):
        raise Fatal(f"Fatal: unable to open config file: Stat: Head \"{url}config\": tls: failed to verify "
                    "certificate: x509: certificate signed by unknown authority")
    credentials = server / "credentials.json"
    allowed = json.loads(credentials.read_text()) if credentials.exists() else None
    if not user or not secret or (allowed is not None and (allowed["username"], allowed["password"]) != (user, secret)):
        raise Fatal("Fatal: unable to open config file: unexpected HTTP response (401): 401 Unauthorized\n"
                    f"Is there a repository at the following location?\n{where}")
    return server / "repos" / parts.path.strip("/")


def repository() -> Path:
    """The repository's folder, its server reached once per call."""
    global _repository
    if _repository is None:
        _repository = _repository_of(location())
    return _repository


def _repository_of(where: str) -> Path:
    if where.startswith("rest:"):
        return rest_server(where)
    if where.startswith("sftp:"):
        return sftp_server(where)
    if where.startswith("s3:"):
        rest = where[3:]
        for scheme in ("https://", "http://"):
            rest = rest.removeprefix(scheme)
        if not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get("AWS_SECRET_ACCESS_KEY"):
            raise Fatal(f"Fatal: unable to open repository at {where}: Access Denied.")
        return state_dir() / "s3" / rest.replace(":", "_")
    return Path(where.removeprefix("local:"))


def password() -> str:
    found = os.environ.get("RESTIC_PASSWORD", "")
    if not found:
        raise Fatal("Fatal: an empty password is not allowed by default. Pass the flag `--insecure-no-password`")
    return found


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def open_repository() -> Path:
    repo = repository()
    if not (repo / "config").is_file():
        raise Fatal(
            f"Fatal: repository does not exist: unable to open config file: stat {repo}/config: no such file or "
            f"directory\nIs there a repository at the following location?\n{location()}", 10)
    key = json.loads((repo / "keys" / "key").read_text())
    if key["password_sha256"] != digest(password()):
        raise Fatal("Fatal: wrong password or no key found", 12)
    return repo


def _seconds(text: str) -> float:
    """A duration as restic writes them: 10m, 30s, 1h."""
    units = {"s": 1, "m": 60, "h": 3600}
    return float(text[:-1]) * units[text[-1]] if text and text[-1] in units else float(text)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


STALE_AFTER = 30 * 60  # seconds: restic's staleLockTimeout
_hostname = os.uname().nodename


def _stale(lock: dict) -> bool:
    """restic's Lock.Stale: older than 30 minutes, or of this host name with its process gone."""
    try:
        taken = datetime.fromisoformat(str(lock["time"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    if (datetime.now(UTC) - taken).total_seconds() > STALE_AFTER:
        return True
    return lock.get("hostname") == _hostname and not _alive(int(lock.get("pid", 0)))


def _locks(repo: Path) -> list[dict]:
    found = []
    for path in (repo / "locks").iterdir():
        with contextlib.suppress(FileNotFoundError, ValueError):
            found.append(json.loads(path.read_text()))
    return found


@contextlib.contextmanager
def locked(repo: Path, kind: str | None, values: dict, flags: set):
    """Hold a lock of KIND on REPO while the command runs, as restic does."""
    if kind is None or "--no-lock" in flags:
        yield
        return
    deadline = time.monotonic() + _seconds(values.get("--retry-lock", ["0s"])[0])
    while True:
        conflict = next((o for o in _locks(repo) if kind == EXCLUSIVE or o["exclusive"]), None)
        if conflict is None:
            break
        if time.monotonic() >= deadline:
            how = "exclusively " if conflict["exclusive"] else ""
            raise Fatal(f"Fatal: unable to create lock in backend: repository is already locked {how}by PID "
                        f"{conflict['pid']} on host unos by root (UID 0, GID 0)", 11)
        time.sleep(0.02)
    path = repo / "locks" / secrets.token_hex(32)
    path.write_text(json.dumps({"exclusive": kind == EXCLUSIVE, "hostname": _hostname, "pid": os.getpid(),
                                "time": now()}))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def snapshots(repo: Path) -> list[dict]:
    """The snapshots, oldest first."""
    found = [json.loads(path.read_text()) for path in (repo / "snapshots").iterdir()]
    return sorted(found, key=lambda s: s["time"])


def find_snapshot(repo: Path, prefix: str) -> dict:
    found = [s for s in snapshots(repo) if s["id"].startswith(prefix)]
    if len(found) != 1:
        raise Fatal(f"Fatal: failed to find snapshot: no matching ID found for prefix \"{prefix}\"")
    return found[0]


def now() -> str:
    return datetime.now(UTC).astimezone().isoformat()


# --- the commands -------------------------------------------------------------------------------


def cmd_init(args, values, flags) -> None:
    repo = repository()
    password()
    if (repo / "config").exists():
        raise Fatal(f"Fatal: create repository at {location()} failed: config file already exists")
    for sub in ("data", "index", "keys", "locks", "snapshots"):
        (repo / sub).mkdir(parents=True, exist_ok=True)
    config = {"version": 2, "id": secrets.token_hex(32), "chunker_polynomial": "3a5e4e3b0a8d3f"}
    (repo / "config").write_text(json.dumps(config))
    (repo / "keys" / "key").write_text(json.dumps({"password_sha256": digest(password())}))
    print(f"created restic repository {config['id'][:10]} at {location()}")


def cmd_cat(args, values, flags) -> None:
    repo = open_repository()
    if args != ["config"]:
        raise Fatal(f"Fatal: invalid type {' '.join(args)!r}")
    print(json.dumps(json.loads((repo / "config").read_text()), indent=2))


def cmd_unlock(args, values, flags) -> None:
    repo = open_repository()
    removed = 0
    for path in (repo / "locks").iterdir():
        with contextlib.suppress(FileNotFoundError, ValueError):
            if _stale(json.loads(path.read_text())):
                path.unlink()
                removed += 1
    print(f"successfully removed {removed} locks")


def _pattern_matches(pattern: str, path: str) -> bool:
    """restic's --exclude, as far as the copy uses it: a pattern with "/" is anchored at the root and
    matches the path or a folder above it; one without matches any name in the path."""
    parts = [p for p in path.split("/") if p]
    if "/" not in pattern:
        return any(fnmatch.fnmatchcase(part, pattern) for part in parts)
    wanted = [p for p in pattern.split("/") if p]
    return len(parts) >= len(wanted) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts, wanted))


def _collect(sources: list[str], excludes: list[str], errors: dict[str, str]) -> dict[str, str]:
    """The regular files below SOURCES, not excluded: absolute path -> content in base64. ERRORS:
    the files that cannot be read, by path, with why."""
    files: dict[str, str] = {}
    existing = 0
    for source in sources:
        if not os.path.lexists(source):
            sys.stderr.write(f"{source} does not exist, skipping\n")
            continue
        existing += 1
        for folder, dirs, names in os.walk(source):
            dirs[:] = sorted(d for d in dirs if not any(_pattern_matches(p, os.path.join(folder, d)) for p in excludes))
            for name in sorted(names):
                path = os.path.join(folder, name)
                if any(_pattern_matches(p, path) for p in excludes):
                    continue
                if path in errors:
                    message = f"open {path}: {errors[path]}"
                    sys.stderr.write(json.dumps({"message_type": "error", "error": {"message": message},
                                                 "during": "archival", "item": path}) + "\n")
                    continue
                if stat.S_ISREG(os.lstat(path).st_mode):
                    with open(path, "rb") as f:
                        files[path] = base64.b64encode(f.read()).decode()
    if not existing:
        raise Fatal("Fatal: all source directories/files do not exist")
    return files


def cmd_backup(args, values, flags) -> int:
    repo = open_repository()
    tags = values.get("--tag", [])
    started = now()
    if "--stdin" in flags:
        name = values.get("--stdin-filename", ["stdin"])[0]
        data = sys.stdin.buffer.read()
        files = {"/" + name: base64.b64encode(data).decode()}
        paths = ["/" + name]
    else:
        if not args:
            raise Fatal("Fatal: nothing to backup, please specify source files/dirs")
        vanished = set(fault().get("vanished", []))
        errors = {path: "no such file or directory" for path in vanished} if fault().get("reported") else {}
        errors.update(fault().get("unreadable", {}))
        files = {path: content for path, content in _collect(args, values.get("--exclude", []), errors).items()
                 if path not in vanished}
        paths = sorted(args)
    hostname = values.get("--host", [os.uname().nodename])[0]
    parents = [s for s in snapshots(repo) if s["paths"] == paths and s["hostname"] == hostname]
    parent = parents[-1]["files"] if parents else {}
    new = [p for p in files if p not in parent]
    changed = [p for p in files if p in parent and parent[p] != files[p]]
    size = {p: len(base64.b64decode(files[p])) for p in files}
    snapshot_id = secrets.token_hex(32)
    snapshot = {
        "time": now(),
        "tree": secrets.token_hex(32),
        "paths": paths,
        "hostname": hostname,
        "username": "root",
        "tags": tags,
        "id": snapshot_id,
        "short_id": snapshot_id[:8],
        "files": files,
        "digests": {p: hashlib.sha256(files[p].encode()).hexdigest() for p in files},
        "summary": {"backup_start": started, "backup_end": now(), "files_new": len(new),
                    "files_changed": len(changed), "files_unmodified": len(files) - len(new) - len(changed),
                    "total_files_processed": len(files), "total_bytes_processed": sum(size.values())},
    }
    (repo / "snapshots" / snapshot_id).write_text(json.dumps(snapshot))
    if "--json" in flags:
        print(json.dumps({
            "message_type": "summary", "files_new": len(new), "files_changed": len(changed),
            "files_unmodified": len(files) - len(new) - len(changed), "dirs_new": 0, "dirs_changed": 0,
            "dirs_unmodified": 0, "data_blobs": len(new) + len(changed), "tree_blobs": 1,
            "data_added": sum(size[p] for p in new + changed), "data_added_packed": sum(size[p] for p in new + changed),
            "total_files_processed": len(files), "total_bytes_processed": sum(size.values()), "total_duration": 0.01,
            "backup_start": started, "backup_end": now(), "snapshot_id": snapshot_id,
        }))
    else:
        print(f"snapshot {snapshot_id[:8]} saved")
    # restic's exit code 3: the snapshot is saved, but some files could not be read.
    return 3 if "--stdin" not in flags and errors else 0


def _keep(group: list[dict], days: int, weeks: int, months: int) -> set[str]:
    """restic's --keep-daily, --keep-weekly and --keep-monthly over GROUP, newest first: the newest
    snapshot of each of the last N days, weeks and months that have one."""
    kept: set[str] = set()
    for count, bucket in ((days, "%Y-%m-%d"), (weeks, "%G-%V"), (months, "%Y-%m")):
        seen: list[str] = []
        for snap in group:
            key = datetime.fromisoformat(snap["time"]).strftime(bucket)
            if key in seen:
                continue
            if len(seen) >= count:
                break
            seen.append(key)
            kept.add(snap["id"])
    return kept


def cmd_forget(args, values, flags) -> None:
    repo = open_repository()
    tags = values.get("--tag", [])
    keep = [int(values.get(f"--keep-{what}", ["0"])[0]) for what in ("daily", "weekly", "monthly")]
    if not any(keep):
        raise Fatal("Fatal: no policy was specified, no snapshots will be removed")
    chosen = [s for s in snapshots(repo) if not tags or set(s["tags"]) & set(tags)]
    groups: dict[tuple, list[dict]] = {}
    for snap in chosen:
        groups.setdefault((snap["hostname"], tuple(snap["paths"])), []).append(snap)
    removed = 0
    for group in groups.values():
        group.sort(key=lambda s: s["time"], reverse=True)
        kept = _keep(group, *keep)
        for snap in group:
            if snap["id"] not in kept:
                (repo / "snapshots" / snap["id"]).unlink()
                removed += 1
    print(f"removed {removed} snapshots" + (", pruned the repository" if "--prune" in flags else ""))


def cmd_check(args, values, flags) -> None:
    repo = open_repository()
    subset = values.get("--read-data-subset")
    if subset:
        number = subset[0].removesuffix("%")
        if not subset[0].endswith("%") or not 0 < float(number) <= 100:
            raise Fatal(f"Fatal: invalid --read-data-subset {subset[0]!r}")
    for snap in snapshots(repo):
        for path, content in snap["files"].items():
            if hashlib.sha256(content.encode()).hexdigest() != snap["digests"][path]:
                raise Fatal(f"error: pack of {path} in snapshot {snap['short_id']}: ciphertext verification failed\n"
                            "Fatal: repository contains errors")
    print("no errors were found")


def cmd_dump(args, values, flags) -> None:
    repo = open_repository()
    if len(args) != 2:
        raise Fatal("Fatal: no file and no snapshot ID specified")
    snap = find_snapshot(repo, args[0])
    content = snap["files"].get(args[1])
    if content is None:
        raise Fatal(f"Fatal: cannot dump file: path \"{args[1]}\" not found in snapshot")
    data = base64.b64decode(content)
    if fault().get("garble") and data:
        data = bytes([data[0] ^ 0xFF]) + data[1:]
    sys.stdout.buffer.write(data)


def cmd_snapshots(args, values, flags) -> None:
    repo = open_repository()
    chosen = [find_snapshot(repo, a) for a in args] if args else snapshots(repo)
    shown = [{k: v for k, v in s.items() if k not in ("files", "digests")} for s in chosen]
    print(json.dumps(shown) if "--json" in flags else "\n".join(s["short_id"] for s in shown))


def cmd_restore(args, values, flags) -> None:
    repo = open_repository()
    if len(args) != 1 or "--target" not in values:
        raise Fatal("Fatal: please specify a snapshot and a --target")
    name, _, sub = args[0].partition(":")
    snap = find_snapshot(repo, name)
    sub = sub.rstrip("/")
    chosen = {}
    for path, content in snap["files"].items():
        if not sub:
            chosen[path.lstrip("/")] = content
        elif path.startswith(sub + "/"):
            chosen[path[len(sub) + 1:]] = content
    if sub and not chosen:
        raise Fatal(f"Fatal: path {sub} not found in snapshot {snap['short_id']}")
    target = Path(values["--target"][0])
    for relative, content in chosen.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(content))
    if "--json" in flags:
        print(json.dumps({"message_type": "summary", "files_restored": len(chosen)}))


COMMANDS = {
    "init": cmd_init, "cat": cmd_cat, "unlock": cmd_unlock, "backup": cmd_backup, "forget": cmd_forget,
    "check": cmd_check, "dump": cmd_dump, "snapshots": cmd_snapshots, "restore": cmd_restore,
}

_fault: dict = {}


def fault() -> dict:
    return _fault


def _stop(signum: int, frame: object) -> None:
    """SIGTERM or SIGINT: end, as restic does, after its lock is removed (the finally of locked)."""
    raise SystemExit(128 + signum)


def main(argv: list[str]) -> int:
    global _fault, _hostname, _values
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    state = state_dir()
    hostname = (state / "hostname").read_text() if (state / "hostname").exists() else None
    _hostname = hostname or _hostname
    with open(state / "calls.jsonl", "a") as log:
        log.write(json.dumps({"argv": argv, "env": dict(os.environ), "pid": os.getpid(), "hostname": hostname}) + "\n")
    try:
        command, args, values, flags = parse(argv)
        _values = values
        name = "backup-stdin" if command == "backup" and "--stdin" in flags else command
        faults = json.loads((state / "faults.json").read_text()) if (state / "faults.json").exists() else {}
        _fault = faults.get(name, {})
        waiting = contextlib.nullcontext()
        if command in LOCKS and (_fault.get("block") or _fault.get("sleep")):
            with contextlib.suppress(Fatal):  # no repository yet: it waits without a lock
                waiting = locked(open_repository(), LOCKS[command], values, flags)
        with waiting:
            if _fault.get("block"):
                (state / f"blocked-{name}").write_text(str(os.getpid()))
                deadline = time.monotonic() + 60
                while not (state / f"release-{name}").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
            if _fault.get("sleep"):
                time.sleep(float(_fault["sleep"]))
        if "code" in _fault:
            sys.stderr.write(_fault.get("stderr", "Fatal: failed") + "\n")
            return int(_fault["code"])
        handler = COMMANDS.get(command)
        if handler is None:
            raise Fatal(f"Fatal: unknown command \"{command}\" for \"restic\"")
        if command in LOCKS:
            with locked(open_repository(), LOCKS[command], values, flags):
                return handler(args, values, flags) or 0
        return handler(args, values, flags) or 0
    except Fatal as e:
        sys.stderr.write(f"{e}\n")
        return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
