"""A fake host for the move of Docker's data (src/system/host/unos-docker-move.sh, ADR-0487).

``FakeMoveHost(folder)`` makes a folder with stubs of the programs that the script runs on the host
(docker, systemctl, btrfs, rsync, mountpoint, containerd, timeout and rm), which run this module as a
program, and its state in ``state.json``: whether the pool is mounted, Docker and unos.service run, the
containers and images, and failures to inject. Docker's data root is daemon.json's ``data-root`` (its
default without it) and containerd's root config.toml's, read at each start of Docker, as on a host;
``docker info`` and ``containerd config dump`` answer them. ``rsync`` copies the folder, ``btrfs
subvolume`` makes and removes folders. Every call is logged in ``calls``, one line per program.

The script itself runs as it is, with the system's sh and the other programs it uses (cp, mv, sed).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROGRAMS = ("docker", "systemctl", "btrfs", "rsync", "mountpoint", "containerd")
UNOS = ("unos-supervisor", "unos-webui")


@dataclass
class FakeMoveHost:
    folder: Path
    # More of the script's environment, such as a mountinfo of the test's.
    extra_env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.bin = self.folder / "bin"
        self.bin.mkdir(parents=True)
        for name in PROGRAMS:
            stub = self.bin / name
            stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{__file__}" "{self.state_path}" {name} "$@"\n')
            stub.chmod(0o755)
        # timeout DURATION COMMAND...: the command alone, its options and duration dropped.
        timeout = self.bin / "timeout"
        timeout.write_text('#!/bin/sh\nwhile [ "${1#-}" != "$1" ]; do shift; done\nshift\nexec "$@"\n')
        timeout.chmod(0o755)
        # GNU rm's --one-file-system, which BSD rm lacks: logged, then the system's rm without it.
        rm = self.bin / "rm"
        rm.write_text(f'#!/bin/sh\necho "rm $*" >> "{self.folder / "calls"}"\n'
                      'for a; do shift; [ "$a" = --one-file-system ] || set -- "$@" "$a"; done\n'
                      'exec /bin/rm "$@"\n')
        rm.chmod(0o755)
        self.save({
            "mounted": True, "docker": True, "containerd": True, "unos_active": True,
            "daemon_json": "", "containerd_config": "", "docker_default": "", "containerd_default": "",
            "docker_root": "", "containerd_root": "",
            "containers": {"unos-supervisor": True, "unos-webui": True, "jellyfin": True, "stopped-app": False},
            "images": ["sha256:aa", "sha256:bb"], "fail": {}, "open_noatime": True,
        })

    @property
    def state_path(self) -> Path:
        return self.folder / "state.json"

    def load(self) -> dict:
        return json.loads(self.state_path.read_text())

    def save(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state))

    def set(self, **values) -> None:
        state = self.load()
        state.update(values)
        self.save(state)

    def fail(self, call: str, message: str) -> None:
        """Make the call (``docker start jellyfin``, ``rsync``: the start of its logged line) fail."""
        state = self.load()
        state["fail"][call] = message
        self.save(state)

    def calls(self) -> list[str]:
        path = self.folder / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def env(self) -> dict[str, str]:
        return {**os.environ, "PATH": f"{self.bin}:/usr/bin:/bin", **self.extra_env}


# --- the stubs: ``python docker_move_fake.py <state> <program> <args>`` ----------------------------


def _roots(state: dict) -> tuple[str, str]:
    """Docker's and containerd's roots as their configuration names them now."""
    docker_root = state["docker_default"]
    text = Path(state["daemon_json"]).read_text() if Path(state["daemon_json"]).exists() else ""
    if text.strip():
        docker_root = json.loads(text).get("data-root", docker_root)
    containerd_root = state["containerd_default"]
    if Path(state["containerd_config"]).exists():
        import tomllib

        containerd_root = tomllib.loads(Path(state["containerd_config"]).read_text()).get("root", containerd_root)
    return docker_root, containerd_root


def _main(state_path: Path, program: str, args: list[str]) -> int:
    state = json.loads(state_path.read_text())
    line = " ".join([program, *args])
    with open(state_path.parent / "calls", "a") as log:
        log.write(line + "\n")
    for call, message in state["fail"].items():
        if line.startswith(call):
            print(message, file=sys.stderr)
            return 1
    code = _run(state, program, args)
    state_path.write_text(json.dumps(state))
    return code


def _run(state: dict, program: str, args: list[str]) -> int:
    if program == "mountpoint":
        return 0 if state["mounted"] else 32
    if program == "btrfs":
        path = Path(args[-1])
        if args[:2] == ["subvolume", "create"]:
            path.mkdir()
            return 0
        if args[:2] == ["subvolume", "delete"]:
            shutil.rmtree(path)
            return 0
    if program == "rsync":
        if args == ["--help"]:
            print("  --open-noatime           avoid changing the atime on opened files" if state["open_noatime"] else "")
            return 0
        source, target = args[-2], args[-1]
        shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
        return 0
    if program == "containerd" and args == ["config", "dump"]:
        print(f"version = 3\nroot = '{state['containerd_root']}'\nstate = '/run/containerd'")
        return 0
    if program == "systemctl":
        return _systemctl(state, args)
    if program == "docker":
        return _docker(state, args)
    print(f"the fake host does not know: {program} {' '.join(args)}", file=sys.stderr)
    return 127


def _systemctl(state: dict, args: list[str]) -> int:
    if args[:2] == ["is-active", "--quiet"]:
        return 0 if args[2] == "unos.service" and state["unos_active"] else 3
    if args == ["daemon-reload"]:
        return 0
    action, units = args[0], args[1:]
    if action == "stop":
        if "unos.service" in units:
            # Its ExecStop: docker compose down, which removes UNOS's containers.
            for name in UNOS:
                state["containers"].pop(name, None)
            state["unos_active"] = False
        if "docker.service" in units:
            state["docker"] = False
            for name in state["containers"]:
                state["containers"][name] = False
        if "containerd.service" in units:
            state["containerd"] = False
        return 0
    if action == "start":
        if "containerd.service" in units:
            state["containerd"] = True
            state["containerd_root"] = _roots(state)[1]
        if "docker.service" in units:
            state["docker"] = True
            state["docker_root"] = _roots(state)[0]
        if "unos.service" in units:
            # Its ExecStart: docker compose up, which makes UNOS's containers again.
            for name in UNOS:
                state["containers"][name] = True
            state["unos_active"] = True
        return 0
    return 127


def _docker(state: dict, args: list[str]) -> int:
    if not state["docker"]:
        print("Cannot connect to the Docker daemon at unix:///var/run/docker.sock", file=sys.stderr)
        return 1
    if args[0] == "ps":
        print("\n".join(f"{name}-id" for name, running in state["containers"].items() if running))
        return 0
    if args[:2] == ["image", "ls"]:
        print("\n".join(state["images"]))
        return 0
    if args[0] == "info":
        print(state["docker_root"])
        return 0
    if args[0] == "stop":
        for container in args[1:]:
            state["containers"][container.removesuffix("-id")] = False
        return 0
    name = args[-1].removesuffix("-id")
    if name not in state["containers"]:
        print(f"Error: No such container: {args[-1]}", file=sys.stderr)
        return 1
    if args[0] == "inspect":
        print(f"{'true' if state['containers'][name] else 'false'} /{name}")
        return 0
    if args[0] == "start":
        state["containers"][name] = True
        print(args[-1])
        return 0
    return 127


def run_script(script: Path, folder: Path, fake: FakeMoveHost) -> subprocess.CompletedProcess[str]:
    """The script with the fake's stubs first on PATH."""
    return subprocess.run(["/bin/sh", str(script), str(folder)], env=fake.env(), capture_output=True, text=True,
                          timeout=60)


if __name__ == "__main__":
    sys.exit(_main(Path(sys.argv[1]), sys.argv[2], sys.argv[3:]))
