"""A fake restic, for the backend tests of the off-device copy and for the local stand-in (ADR-0337).

restic is not installed on the workstation. This script stands in for it where the tests and
scripts/standin/run_supervisor.py put it on PATH, through a small wrapper (``install``): it keeps a
repository of its own with what the commands of src/offsite/ need, so that what ``backup`` stored
comes back from ``dump``, ``restore`` and ``snapshots``:

* the repository is the folder that RESTIC_REPOSITORY names (``local:<path>`` or a path), or, for
  ``s3:<endpoint>/<bucket>[/<prefix>]``, a folder below ``<state>/s3``; it holds ``config``, a
  ``keys/key`` with the digest of the password and one JSON file per snapshot in ``snapshots/``,
  with the files of the snapshot in it (the tests copy small folders);
* the commands init, cat config, unlock, backup (of folders, with --exclude, or of standard input
  with --stdin and --stdin-filename), forget with --tag, --keep-daily, --keep-weekly and
  --keep-monthly, check, dump, snapshots and restore, with restic's exit codes: 10 for a
  repository that does not exist, 12 for a wrong password, 1 for other failures;
* global options anywhere: --no-cache, --cache-dir, --limit-upload, --limit-download, --json and
  --quiet; backup prints its summary as restic does with --json --quiet.

``<state>`` is FAKE_RESTIC_STATE, which the wrapper sets. Each call appends a line to
``<state>/calls.jsonl``: its arguments, its environment and its process id. ``<state>/faults.json``
changes what a command does, by the command's name (``backup-stdin`` for the configuration's
backup): ``{"code": N, "stderr": "..."}`` fails it, ``{"sleep": S}`` waits S seconds first,
``{"block": true}`` writes ``<state>/blocked-<name>`` and waits for ``<state>/release-<name>``,
``{"garble": true}`` makes dump change a byte of what it writes, and ``{"skip": [paths]}`` makes
backup leave those files out.

Standard library only: it runs with the stand-in's Python and the tests' alike.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import secrets
import shlex
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

GLOBAL_FLAGS = {"--no-cache", "--json", "--quiet", "-q"}
GLOBAL_VALUES = {"--cache-dir", "--limit-upload", "--limit-download"}
COMMAND_VALUES = {
    "--stdin-filename", "--tag", "--exclude", "--keep-daily", "--keep-weekly", "--keep-monthly", "--target",
    "--host", "--group-by",
}


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
    for path in (restic, ionice):
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
        if arg in GLOBAL_FLAGS or arg in ("--stdin", "--prune", "--read-data"):
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


def repository() -> Path:
    where = location()
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
    return datetime.now(timezone.utc).astimezone().isoformat()


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
    open_repository()
    print("successfully removed 0 locks")


def _pattern_matches(pattern: str, path: str) -> bool:
    """restic's --exclude, as far as the copy uses it: a pattern with "/" is anchored at the root and
    matches the path or a folder above it; one without matches any name in the path."""
    parts = [p for p in path.split("/") if p]
    if "/" not in pattern:
        return any(fnmatch.fnmatchcase(part, pattern) for part in parts)
    wanted = [p for p in pattern.split("/") if p]
    return len(parts) >= len(wanted) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts, wanted))


def _collect(sources: list[str], excludes: list[str], skip: list[str]) -> dict[str, str]:
    """The regular files below SOURCES, not excluded: absolute path -> content in base64."""
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
                if any(_pattern_matches(p, path) for p in excludes) or path in skip:
                    continue
                if stat.S_ISREG(os.lstat(path).st_mode):
                    with open(path, "rb") as f:
                        files[path] = base64.b64encode(f.read()).decode()
    if not existing:
        raise Fatal("Fatal: all source directories/files do not exist")
    return files


def cmd_backup(args, values, flags) -> None:
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
        files = _collect(args, values.get("--exclude", []), fault().get("skip", []))
        paths = sorted(args)
    hostname = os.uname().nodename
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


def main(argv: list[str]) -> int:
    global _fault
    state = state_dir()
    with open(state / "calls.jsonl", "a") as log:
        log.write(json.dumps({"argv": argv, "env": dict(os.environ), "pid": os.getpid()}) + "\n")
    try:
        command, args, values, flags = parse(argv)
        name = "backup-stdin" if command == "backup" and "--stdin" in flags else command
        faults = json.loads((state / "faults.json").read_text()) if (state / "faults.json").exists() else {}
        _fault = faults.get(name, {})
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
        handler(args, values, flags)
    except Fatal as e:
        sys.stderr.write(f"{e}\n")
        return e.code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
