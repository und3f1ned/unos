#!/usr/bin/env python3
"""The working tree against the dev device's checkout, file by file (scripts/dev/deploy.sh).

  tree.py status                 the files that differ between the working tree and the device
  tree.py sync [--dry-run]       make the device checkout's files equal to the working tree's
  tree.py rollback [--dry-run]   put back what the last sync replaced or deleted on the device
  tree.py verify COMPONENT...    status 1 unless these components' runtime files are equal
  tree.py manifest [DIR]         a checkout's manifest as JSON (this file runs on the device too)
  tree.py compare A.json B.json  compare two manifests
  tree.py bundle OUT --rootfs-dists FILE [--machine M] [--skip DIST]... [-- PYTEST_ARGS...]
                                 for linux-tests.sh: the working tree and the test dependencies

A checkout's files are git's view of it: tracked and untracked files that are not ignored
(git ls-files -co --exclude-standard), without tracked files deleted from the tree. A file is
compared by its type (file or symlink), its executable bit and its sha256. git's index and HEAD
are neither compared nor changed: the images are built from the files.

Runtime files are the ones the images and the deployment are made of (RUNTIME below, by
component); the other files (docs, tests, tooling) do not change what runs on the device.

The device's checkout is read in a private mount namespace, for one command, in which the root
filesystem is bind-remounted read-only and noatime (ADR-0221): neither git (an index refresh),
Python (bytecode) nor reading (access times) can write to the device's SD card (ADR-0214). A
sync writes only the files that differ. Before it changes anything it saves the device's
versions of them on the workstation for a rollback (ADR-0223): in UNOS_DEV_STATE_DIR/rollback,
by default tmp/dev/rollback of the main checkout, which git ignores and its worktrees share.

Standard library only; Python 3.9 or later (on the device, its own python3 runs it).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# What the images and the deployment are made of, by component (fnmatch patterns: * also
# matches /). supervisor: the build context of supervisor/Dockerfile as used; webui: the
# inputs of `npm run build` and the final image; deploy: the compose file and the scripts.
RUNTIME: dict[str, tuple[str, ...]] = {
    "supervisor": (
        "supervisor/Dockerfile",
        "supervisor/.dockerignore",
        "supervisor/requirements.txt",
        "supervisor/src/*",
    ),
    "webui": (
        "webui/Dockerfile",
        "webui/.dockerignore",
        "webui/Caddyfile",
        "webui/package.json",
        "webui/package-lock.json",
        "webui/svelte.config.js",
        "webui/vite.config.ts",
        "webui/tailwind.config.js",
        "webui/postcss.config.js",
        "webui/tsconfig.json",
        "webui/src/*",
        "webui/static/*",
    ),
    "deploy": (
        "deploy/docker-compose.yml",
        "deploy/install.sh",
        "deploy/update.sh",
        "deploy/uninstall.sh",
    ),
}

Entry = dict  # {"kind": "file" | "link" | "other", "x": bool, "size": int, "sha256": str}
Manifest = dict  # path -> Entry

NOATIME = getattr(os, "O_NOATIME", 0)


class TreeError(Exception):
    pass


# --- manifests ------------------------------------------------------------------------------


def git_files(root: str | os.PathLike) -> list[str]:
    """Tracked and untracked files of a checkout that git does not ignore, as git lists them."""
    # safe.directory: on the device the checkout may belong to another user than root. Without
    # the GIT_* variables that git exports to hooks and to `git rebase -x` (GIT_DIR,
    # GIT_INDEX_FILE, ...): they would make git list another repository's files.
    result = subprocess.run(
        ["git", "-c", f"safe.directory={os.path.realpath(root)}", "--no-optional-locks",
         "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=root,
        env={name: value for name, value in os.environ.items() if not name.startswith("GIT_")},
        capture_output=True,
    )
    if result.returncode != 0:
        raise TreeError(f"git ls-files failed in {root}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return sorted({p for p in result.stdout.decode("utf-8", "surrogateescape").split("\0") if p})


def file_sha256(path: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | NOATIME)
    except PermissionError:  # O_NOATIME needs the file's owner (or root)
        fd = os.open(path, os.O_RDONLY)
    digest = hashlib.sha256()
    with os.fdopen(fd, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(root: str | os.PathLike, path: str) -> Entry | None:
    """The entry of one file, or None when it does not exist (a tracked file deleted)."""
    full = os.path.join(root, path)
    try:
        st = os.lstat(full)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        target = os.readlink(full).encode("utf-8", "surrogateescape")
        return {"kind": "link", "x": False, "size": len(target), "sha256": hashlib.sha256(target).hexdigest()}
    if stat.S_ISREG(st.st_mode):
        return {"kind": "file", "x": bool(st.st_mode & 0o111), "size": st.st_size, "sha256": file_sha256(full)}
    return {"kind": "other", "x": False, "size": 0, "sha256": ""}


def manifest(root: str | os.PathLike, paths: Iterable[str] | None = None) -> Manifest:
    files = git_files(root) if paths is None else sorted(set(paths))
    out: Manifest = {}
    for path in files:
        entry = file_entry(root, path)
        if entry is not None:
            out[path] = entry
    return out


# --- comparing ------------------------------------------------------------------------------


@dataclass
class Diff:
    changed: list[str] = field(default_factory=list)  # content or type differs
    mode: list[str] = field(default_factory=list)  # only the executable bit differs
    only_local: list[str] = field(default_factory=list)  # in the working tree, not on the device
    only_remote: list[str] = field(default_factory=list)  # on the device, not in the working tree

    def __bool__(self) -> bool:
        return bool(self.changed or self.mode or self.only_local or self.only_remote)

    def items(self) -> list[tuple[str, str]]:
        """(how, path) for every difference, sorted by path."""
        rows = [("changed", p) for p in self.changed] + [("mode", p) for p in self.mode]
        rows += [("only here", p) for p in self.only_local] + [("only on device", p) for p in self.only_remote]
        return sorted(rows, key=lambda row: row[1])

    def select(self, keep) -> Diff:
        return Diff(*([p for p in group if keep(p)] for group in (self.changed, self.mode, self.only_local, self.only_remote)))


def compare(local: Manifest, remote: Manifest) -> Diff:
    diff = Diff()
    for path in sorted(set(local) | set(remote)):
        a, b = local.get(path), remote.get(path)
        if b is None:
            diff.only_local.append(path)
        elif a is None:
            diff.only_remote.append(path)
        elif a["kind"] != b["kind"] or a["sha256"] != b["sha256"]:
            diff.changed.append(path)
        elif a["x"] != b["x"]:
            diff.mode.append(path)
    return diff


def component(path: str) -> str | None:
    """The runtime component a file belongs to, or None for docs, tests and tooling."""
    for name, patterns in RUNTIME.items():
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            return name
    return None


def status_lines(diff: Diff, here: str, there: str, counts: tuple[int, int]) -> list[str]:
    lines = [f"working tree {here}: {counts[0]} files; device checkout {there}: {counts[1]} files"]
    runtime = diff.select(lambda p: component(p) is not None)
    other = diff.select(lambda p: component(p) is None)
    if runtime:
        per = {name: 0 for name in RUNTIME}
        for _, path in runtime.items():
            per[component(path)] += 1
        summary = ", ".join(f"{name} {n}" for name, n in per.items())
        lines.append(f"runtime files: {len(runtime.items())} differ ({summary}): the device does not run this working tree")
        lines += [f"  {how:<15} {path}" for how, path in runtime.items()]
    else:
        lines.append("runtime files (" + ", ".join(RUNTIME) + "): equal")
    if other:
        lines.append(f"other files (docs, tests, tooling): {len(other.items())} differ")
        lines += [f"  {how:<15} {path}" for how, path in other.items()]
    else:
        lines.append("other files: equal")
    return lines


# --- writing --------------------------------------------------------------------------------


def check_paths(paths: Iterable[str]) -> None:
    """Refuse paths a sync must never write or delete."""
    for path in paths:
        parts = path.split("/")
        if not path or path.startswith("/") or ".." in parts or "" in parts or parts[0] == ".git" or "\n" in path:
            raise TreeError(f"refusing to sync the path {path!r}")


def make_tar(root: str | os.PathLike, paths: Iterable[str], prefix: str = "") -> bytes:
    """An uncompressed tar of files and symlinks, owned by root, mode 0644 or 0755."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        add_files(tar, root, paths, prefix)
    return buf.getvalue()


def add_files(tar: tarfile.TarFile, root: str | os.PathLike, paths: Iterable[str], prefix: str = "") -> None:
    for path in paths:
        full = os.path.join(root, path)
        info = tar.gettarinfo(full, arcname=prefix + path)
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        if info.isreg():
            info.mode = 0o755 if info.mode & 0o111 else 0o644
            with open(full, "rb") as f:
                tar.addfile(info, f)
        elif info.issym():
            tar.addfile(info)
        else:
            raise TreeError(f"not a file or a symlink: {path}")


def nul(paths: Iterable[str]) -> bytes:
    return b"".join(p.encode("utf-8", "surrogateescape") + b"\0" for p in paths)


# --- the device -----------------------------------------------------------------------------


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass
class Device:
    host: str
    repo: str
    state_dir: Path
    ssh: Path

    @classmethod
    def from_settings(cls) -> Device:
        """The settings of dev.env, as env.sh validates and resolves them."""
        here = Path(__file__).resolve().parent
        script = '. "$1" || exit 1; printf "%s\\0" "$UNOS_DEV_SSH_USER@$UNOS_DEV_HOST" "$UNOS_DEV_REPO" "$UNOS_DEV_STATE_DIR"'
        result = subprocess.run(["bash", "-c", script, "tree.py", str(here / "env.sh")], capture_output=True)
        if result.returncode != 0:
            raise TreeError(result.stderr.decode("utf-8", "replace").strip() or "could not read dev.env")
        host, repo, state = result.stdout.decode().split("\0")[:3]
        return cls(host=host, repo=repo, state_dir=Path(state), ssh=here / "ssh.sh")

    def run(self, command: str, data: bytes | None = None, timeout: int = 900) -> bytes:
        result = subprocess.run([str(self.ssh), command], input=data, capture_output=True, timeout=timeout)
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", "replace").strip()[-800:]
            raise TreeError(f"on the device ({self.host}), status {result.returncode}: {err}")
        return result.stdout

    def read_only(self, inner: str) -> str:
        """A command that runs INNER in the checkout (cwd), with the device's root filesystem
        read-only and noatime in a private mount namespace (DEV_RO_ROOT of env.sh)."""
        body = 'mount -o remount,bind,ro,noatime / && cd "$1" && ' + inner
        return "unshare --mount --propagation private sh -c " + shlex.quote(body) + " sh " + shlex.quote(self.repo)

    def manifest(self) -> Manifest:
        source = Path(__file__).read_bytes()
        out = self.run(self.read_only("python3 -I -B - manifest ."), data=source)
        return json.loads(out)

    def where(self) -> str:
        return f"{self.host}:{self.repo}"


def local_and_remote(dev: Device) -> tuple[Path, Manifest, Manifest]:
    root = repo_root()
    return root, manifest(root), dev.manifest()


def print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)
    sys.stdout.flush()


def cmd_status() -> int:
    dev = Device.from_settings()
    root, local, remote = local_and_remote(dev)
    print_lines(status_lines(compare(local, remote), str(root), dev.where(), (len(local), len(remote))))
    return 0


def cmd_verify(components: list[str]) -> int:
    unknown = [c for c in components if c not in RUNTIME]
    if unknown:
        raise TreeError(f"unknown component {unknown[0]!r}; known: {', '.join(RUNTIME)}")
    dev = Device.from_settings()
    _, local, remote = local_and_remote(dev)
    diff = compare(local, remote).select(lambda p: component(p) in components)
    if diff:
        print(f"the device's {', '.join(components)} files differ from the working tree (run deploy.sh sync first):")
        print_lines(f"  {how:<15} {path}" for how, path in diff.items())
        return 1
    print(f"the device's {', '.join(components)} files equal the working tree")
    return 0


def cmd_sync(dry_run: bool) -> int:
    dev = Device.from_settings()
    root, local, remote = local_and_remote(dev)
    diff = compare(local, remote)
    print_lines(status_lines(diff, str(root), dev.where(), (len(local), len(remote))))
    if not diff:
        print("nothing to sync: the device checkout equals the working tree")
        return 0
    write = diff.changed + diff.mode + diff.only_local
    delete = diff.only_remote
    check_paths(write + delete)
    size = sum(local[p]["size"] for p in write)
    print(f"sync: write {len(write)} files ({size / 1024:.1f} KiB) and delete {len(delete)} files in {dev.where()}")
    if dry_run:
        print("dry run: nothing was changed")
        return 0
    upload = make_tar(root, write) if write else b""
    backup = save_rollback(dev, diff)
    print(f"saved the device's versions for a rollback: {backup}")
    q = shlex.quote(dev.repo)
    if write:
        dev.run(f"tar -x --no-same-owner -f - -C {q}", data=upload)
    if delete:
        dev.run(f"cd {q} && xargs -0 -r rm -f --", data=nul(delete))
    left = compare(local, dev.manifest())
    if left:
        print_lines(f"  {how:<15} {path}" for how, path in left.items())
        raise TreeError("the device checkout still differs after the sync (did the working tree change meanwhile?)")
    print("the device checkout now equals the working tree")
    return 0


def save_rollback(dev: Device, diff: Diff) -> Path:
    base = dev.state_dir / "rollback"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = base / stamp
    target.mkdir(parents=True, mode=0o700)
    os.chmod(base, 0o700)
    restore = sorted(diff.changed + diff.mode + diff.only_remote)
    if restore:
        tar = dev.run(dev.read_only("tar --null --no-recursion -T - -cf -"), data=nul(restore))
        fd = os.open(target / "device-before.tar", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(tar)
    meta = {"device": dev.where(), "time": stamp, "restore": restore, "created": sorted(diff.only_local)}
    (target / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    (base / "latest").write_text(stamp + "\n")
    return target


def cmd_rollback(dry_run: bool) -> int:
    dev = Device.from_settings()
    base = dev.state_dir / "rollback"
    try:
        stamp = (base / "latest").read_text().strip()
    except FileNotFoundError:
        print(f"no sync to roll back (nothing in {base})")
        return 0
    target = base / stamp
    meta = json.loads((target / "meta.json").read_text())
    if meta["device"] != dev.where():
        raise TreeError(f"the last sync went to {meta['device']}, not {dev.where()}")
    restore, created = meta["restore"], meta["created"]
    check_paths(restore + created)
    print(f"rollback of the sync of {stamp}: restore {len(restore)} files, delete {len(created)} files in {dev.where()}")
    print_lines(f"  restore         {p}" for p in restore)
    print_lines(f"  delete          {p}" for p in created)
    if dry_run:
        print("dry run: nothing was changed")
        return 0
    q = shlex.quote(dev.repo)
    if restore:
        dev.run(f"tar -x --no-same-owner -f - -C {q}", data=(target / "device-before.tar").read_bytes())
    if created:
        dev.run(f"cd {q} && xargs -0 -r rm -f --", data=nul(created))
    os.replace(target, base / (stamp + ".applied"))
    (base / "latest").unlink()
    print("the device checkout is back to its state before that sync")
    return 0


# --- the test bundle of linux-tests.sh ------------------------------------------------------


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(requirements: Path) -> dict[str, str]:
    """name -> version of the NAME==VERSION lines of a requirements file (not its -r files)."""
    out: dict[str, str] = {}
    for raw in requirements.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*==\s*([^\s;\\]+)", line)
        if match:
            out[normalize(match.group(1))] = match.group(3)
    return out


def rootfs_dists(listing: str) -> tuple[str, dict[str, str]]:
    """The Python version and name -> version of the dist-info directories of a rootfs, from
    lines like python3.11/site-packages/fastapi-0.162.0.dist-info."""
    python, dists = "", {}
    for line in listing.split():
        match = re.match(r"python(\d+\.\d+)/site-packages/([^/]+)-([^-/]+)\.dist-info/?$", line)
        if match:
            python = match.group(1)
            dists[normalize(match.group(2))] = match.group(3)
    if not python:
        raise TreeError("no Python packages found in the rootfs listing")
    return python, dists


def target_environment(python: str, machine: str) -> dict[str, str]:
    return {
        "implementation_name": "cpython",
        "implementation_version": python + ".0",
        "os_name": "posix",
        "platform_machine": machine,
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": "Linux",
        "platform_version": "",
        "python_full_version": python + ".0",
        "python_version": python,
        "sys_platform": "linux",
        "extra": "",
    }


def requirement_name(requirement: str, environment: dict[str, str]) -> str | None:
    """The distribution a dependency names, or None when its marker excludes the target."""
    try:
        from packaging.requirements import Requirement
    except ImportError:  # without packaging: drop extras-only dependencies, keep the rest
        name, _, marker = requirement.partition(";")
        if "extra" in marker:
            return None
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", name)
        return normalize(match.group(1)) if match else None
    req = Requirement(requirement)
    if req.marker is not None and not req.marker.evaluate(environment):
        return None
    return normalize(req.name)


def test_dependencies(roots: Iterable[str], present: dict[str, str], environment: dict[str, str]) -> list:
    """The installed distributions (of this Python) that ROOTS need and the rootfs lacks."""
    from importlib import metadata

    todo, seen, found = [normalize(r) for r in roots], set(), []
    while todo:
        name = todo.pop()
        if name in seen or name in present:
            continue
        seen.add(name)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            raise TreeError(f"{name} is not installed for {sys.executable} (run make setup, or set PYTHON)") from None
        found.append(dist)
        for requirement in dist.requires or []:
            dep = requirement_name(requirement, environment)
            if dep:
                todo.append(dep)
    return sorted(found, key=lambda d: normalize(d.metadata["Name"]))


RUN_PY = '''\
"""Started by scripts/dev/linux-tests.sh in the chroot on the device: pytest with the arguments in /w/args."""
import os
import sys

os.chdir("/w/repo/supervisor")
sys.path.insert(0, "/w/repo/supervisor")
with open("/w/args", "rb") as f:
    args = [a.decode("utf-8", "surrogateescape") for a in f.read().split(b"\\0") if a]
import pytest

sys.exit(pytest.main(["-p", "no:cacheprovider", "--basetemp=/tmp/pt", "--ignore=tests/fixtures", "-rfE"]
                     + args))
'''


def cmd_bundle(out: Path, listing_file: Path, machine: str, skip: list[str], pytest_args: list[str]) -> int:
    root = repo_root()
    python, present = rootfs_dists(listing_file.read_text())
    stale = [
        f"{name} {present.get(name, 'missing')} there, {version} in supervisor/requirements.txt"
        for name, version in pins(root / "supervisor" / "requirements.txt").items()
        if present.get(name) != version
    ]
    for line in stale:
        print(f"tree.py: warning: the RAM rootfs differs from the supervisor image's pins: {line}", file=sys.stderr)
    roots = [name for name in pins(root / "supervisor" / "requirements-dev.txt") if name not in {normalize(s) for s in skip}]
    dists = test_dependencies(roots, present, target_environment(python, machine))
    files = [p for p in git_files(root) if os.path.lexists(root / p)]
    with tarfile.open(out, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        add_files(tar, root, files, prefix="repo/")
        for dist in dists:
            name = dist.metadata["Name"]
            if dist.files is None:
                raise TreeError(f"{name} lists no files (no RECORD)")
            for entry in dist.files:
                rel = str(entry)
                if rel.startswith("..") or "__pycache__" in entry.parts or rel.endswith(".pyc"):
                    continue
                if rel.endswith((".so", ".dylib", ".pyd")):
                    print(f"tree.py: warning: {name}: left out the compiled file {rel}", file=sys.stderr)
                    continue
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    info = tar.gettarinfo(str(source), arcname="deps/" + rel)
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mode = 0o644
                    with open(source, "rb") as f:
                        tar.addfile(info, f)
        for name, data in (("args", nul(pytest_args)), ("run.py", RUN_PY.encode())):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, int(time.time())
            tar.addfile(info, io.BytesIO(data))
    names = ", ".join(f"{d.metadata['Name']} {d.version}" for d in dists)
    print(f"bundle: {len(files)} files of the working tree; test dependencies for Python {python}: {names}")
    return 0


# --- command line ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    pytest_args: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, pytest_args = argv[:cut], argv[cut + 1:]
    parser = argparse.ArgumentParser(prog="tree.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("sync").add_argument("--dry-run", action="store_true")
    sub.add_parser("rollback").add_argument("--dry-run", action="store_true")
    sub.add_parser("verify").add_argument("components", nargs="+")
    sub.add_parser("manifest").add_argument("dir", nargs="?", default=".")
    p = sub.add_parser("compare")
    p.add_argument("local", type=Path)
    p.add_argument("remote", type=Path)
    p = sub.add_parser("bundle")
    p.add_argument("out", type=Path)
    p.add_argument("--rootfs-dists", type=Path, required=True)
    p.add_argument("--machine", default="aarch64")
    p.add_argument("--skip", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.command == "manifest":
            json.dump(manifest(args.dir), sys.stdout, sort_keys=True)
            return 0
        if args.command == "compare":
            local, remote = (json.loads(p.read_text()) for p in (args.local, args.remote))
            print_lines(status_lines(compare(local, remote), str(args.local), str(args.remote), (len(local), len(remote))))
            return 1 if compare(local, remote) else 0
        if args.command == "status":
            return cmd_status()
        if args.command == "verify":
            return cmd_verify(args.components)
        if args.command == "sync":
            return cmd_sync(args.dry_run)
        if args.command == "rollback":
            return cmd_rollback(args.dry_run)
        if args.command == "bundle":
            return cmd_bundle(args.out, args.rootfs_dists, args.machine, args.skip, pytest_args)
    except (TreeError, OSError, subprocess.TimeoutExpired) as e:
        print(f"tree.py: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
