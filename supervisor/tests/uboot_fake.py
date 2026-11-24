"""A small interpreter of the U-Boot commands that the SD standby's chooser uses (ADR-0471), for the
tests of ``src/standby/bootscript.py``.

It runs a script on a fake board: files on the partitions of its MMC devices, the raw sectors of
those devices, the memory that ``load`` fills, and U-Boot's environment. It knows only what the
boards' loaders have and the chooser needs: ``setenv``, ``echo``, ``if``/``then``/``else``/``fi`` with
``test`` (``-z``, ``-n``, ``=``, ``-e``) and ``load`` as conditions, ``load mmc``, ``mmc
dev``/``read``/``write``, ``env import -t``, ``env export -t``, ``source`` (the BPI-W3's handover) and
``booti`` (the BPI-F3's, ADR-0489). Any other command, ``setexpr``, ``itest``, ``bootcount``,
``fatwrite``, ``saveenv`` and ``sysboot`` among them, fails the test, as does a statement that is not
one per line. The tests check that each board's chooser uses only its profile's commands.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

SECTOR = 512
_VARIABLE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


class Booted(Exception):
    """A sourced boot script booted its kernel: the board left U-Boot."""

    def __init__(self, handover: dict[str, str]):
        super().__init__(handover)
        self.handover = handover


@dataclass
class Board:
    """The fake board: ``files[(dev, part)][path]`` (bytes), ``sectors[(dev, block)]`` (512 bytes),
    devices that cannot be read at all, and the environment."""

    files: dict[tuple[int, int], dict[str, bytes]] = field(default_factory=dict)
    sectors: dict[tuple[int, int], bytes] = field(default_factory=dict)
    unreadable: set[int] = field(default_factory=set)
    env: dict[str, str] = field(default_factory=lambda: {
        "scriptaddr": "0x00500000", "kernel_addr_r": "0x00400000", "pxefile_addr_r": "0x00600000",
        "ramdisk_addr_r": "0x00700000", "fdt_addr_r": "0x00800000",
    })
    # What a sourced script or a booti does: boot its kernel, or fail and return.
    sourced_scripts_return: bool = False
    memory: dict[str, bytes] = field(default_factory=dict)
    current_mmc: int | None = None
    commands: list[str] = field(default_factory=list)
    echoed: list[str] = field(default_factory=list)
    sourced: list[dict[str, str]] = field(default_factory=list)
    booted: list[dict[str, str]] = field(default_factory=list)

    def counter(self, dev: int, block: int) -> bytes:
        return self.sectors.get((dev, block), b"\0" * SECTOR)


ALLOWED = {"setenv", "echo", "test", "load", "mmc", "env", "source", "booti"}


def _expand(text: str, env: dict[str, str]) -> str:
    return _VARIABLE.sub(lambda match: env.get(match.group(1), ""), text)


def _words(line: str, env: dict[str, str]) -> list[str]:
    """The words of a statement after U-Boot's expansion of ${...}, quotes grouping words."""
    return shlex.split(_expand(line, env), posix=True)


def _dev_part(spec: str) -> tuple[int, int]:
    if ":" in spec:
        dev, part = spec.split(":", 1)
        return int(dev, 16), int(part, 16)  # U-Boot reads both as hex
    return int(spec, 16), 1


def _import_text(data: bytes, wanted: list[str]) -> dict[str, str | None]:
    """``env import -t``: name=value lines up to the first NUL, only the names in ``wanted`` when any.
    A name of ``wanted`` that the data lacks comes back as None: U-Boot's himport_r deletes it."""
    text = data.split(b"\0", 1)[0].decode("latin-1")
    found: dict[str, str | None] = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if not wanted or name in wanted:
            found[name] = value
    return {**dict.fromkeys(wanted), **found}


class Interpreter:
    def __init__(self, board: Board):
        self.board = board

    def run(self, script: str) -> None:
        lines = [line.strip() for line in script.splitlines()]
        lines = [line for line in lines if line and not line.startswith("#")]
        self._block(lines, 0, len(lines))

    # --- blocks ------------------------------------------------------------------------------------

    def _block(self, lines: list[str], start: int, end: int) -> None:
        index = start
        while index < end:
            line = lines[index]
            if line.startswith("if "):
                assert line.endswith("; then"), f"an if must end with '; then' on its line: {line!r}"
                else_at, fi_at = self._matching(lines, index, end)
                if self._condition(line[3:-len("; then")]):
                    self._block(lines, index + 1, else_at if else_at is not None else fi_at)
                elif else_at is not None:
                    self._block(lines, else_at + 1, fi_at)
                index = fi_at + 1
                continue
            assert line not in ("else", "fi"), f"an {line} without its if"
            assert ";" not in line.replace("; then", ""), f"one statement per line: {line!r}"
            self._statement(line)
            index += 1

    @staticmethod
    def _matching(lines: list[str], at: int, end: int) -> tuple[int | None, int]:
        depth, else_at = 0, None
        for index in range(at + 1, end):
            line = lines[index]
            if line.startswith("if "):
                depth += 1
            elif line == "fi":
                if depth == 0:
                    return else_at, index
                depth -= 1
            elif line == "else" and depth == 0:
                else_at = index
        raise AssertionError(f"an if without its fi: {lines[at]!r}")

    # --- conditions and statements ------------------------------------------------------------------

    def _condition(self, text: str) -> bool:
        words = _words(text, self.board.env)
        self.board.commands.append(words[0])
        if words[0] == "test":
            return self._test(words[1:])
        if words[0] in ("load", "mmc", "env"):
            return self._command(words)
        raise AssertionError(f"a condition the chooser may not use: {text!r}")

    def _test(self, args: list[str]) -> bool:
        if args[:1] == ["-z"]:
            return (args[1] if len(args) > 1 else "") == ""
        if args[:1] == ["-n"]:
            return (args[1] if len(args) > 1 else "") != ""
        if args[:1] == ["-e"]:
            assert args[1] == "mmc", args
            dev, part = _dev_part(args[2])
            if dev in self.board.unreadable:
                return False
            return args[3] in self.board.files.get((dev, part), {})
        if len(args) == 3 and args[1] == "=":
            return args[0] == args[2]
        raise AssertionError(f"a test the chooser may not use: {args!r}")

    def _statement(self, line: str) -> None:
        words = _words(line, self.board.env)
        self.board.commands.append(words[0])
        assert words[0] in ALLOWED, f"a command the W3's loader lacks or the chooser may not use: {words[0]}"
        if words[0] == "setenv":
            if len(words) == 2:
                self.board.env.pop(words[1], None)
            else:
                self.board.env[words[1]] = " ".join(words[2:])
            return
        if words[0] == "echo":
            self.board.echoed.append(" ".join(words[1:]))
            return
        if words[0] == "source":
            self._source(words[1])
            return
        if words[0] == "booti":
            self._booti(words[1:])
            return
        self._command(words)

    def _command(self, words: list[str]) -> bool:
        board = self.board
        if words[0] == "load":
            assert words[1] == "mmc", words
            dev, part = _dev_part(words[2])
            path = words[4].lstrip("/")
            if dev in board.unreadable or path not in board.files.get((dev, part), {}):
                return False
            data = board.files[(dev, part)][path]
            board.memory[words[3]] = data
            board.env["filesize"] = f"{len(data):x}"
            return True
        if words[0] == "mmc":
            if words[1] == "dev":
                board.current_mmc = int(words[2])
                return True
            assert words[1] in ("read", "write") and len(words) == 5, words
            assert words[4] == "1", "the chooser reads and writes one sector"
            assert board.current_mmc is not None, "mmc read or write before mmc dev"
            block = int(words[3], 16)  # U-Boot reads the block number as hex
            if board.current_mmc in board.unreadable:
                return False
            if words[1] == "read":
                board.memory[words[2]] = board.counter(board.current_mmc, block)
            else:
                board.sectors[(board.current_mmc, block)] = board.memory[words[2]][:SECTOR].ljust(SECTOR, b"\0")
            return True
        if words[0] == "env":
            assert words[2] == "-t", f"the chooser uses env {words[1]} -t only: {words}"
            if words[1] == "import":
                data = board.memory.get(words[3], b"")
                size = int(words[4], 16)
                for name, value in _import_text(data[:size], words[5:]).items():
                    if value is None or value == "":
                        board.env.pop(name, None)  # "name=" and a listed name not in the data delete it
                    else:
                        board.env[name] = value
                return True
            if words[1] == "export":
                names = words[4:]
                assert names, "env export of named variables only"
                text = "".join(f"{name}={board.env[name]}\n" for name in names if name in board.env)
                board.memory[words[3]] = text.encode() + b"\0"
                return True
        raise AssertionError(f"unknown command {words!r}")

    def _source(self, address: str) -> None:
        handover = {name: self.board.env.get(name, "") for name in (
            "devtype", "devnum", "distro_bootpart", "prefix", "extraboardargs")}
        handover["script"] = self.board.memory.get(address, b"").decode("latin-1")
        self.board.sourced.append(handover)
        if not self.board.sourced_scripts_return:
            raise Booted(handover)


    def _booti(self, args: list[str]) -> None:
        """booti <kernel> <initrd>:<size> <fdt>, as sysboot passes them: the files loaded at those
        addresses and the command line, ``bootargs``."""
        assert len(args) == 3 and ":" in args[1], f"booti with the kernel, initrd:size and device tree: {args}"
        initrd, size = args[1].split(":", 1)
        memory = self.board.memory
        assert int(size, 16) == len(memory.get(initrd, b"")), "the initrd's size is the one loaded"
        handover = {"kernel": memory.get(args[0], b"").decode("latin-1"), "initrd": memory.get(initrd, b"").decode("latin-1"),
                    "fdt": memory.get(args[2], b"").decode("latin-1"), "bootargs": self.board.env.get("bootargs", "")}
        self.board.booted.append(handover)
        if not self.board.sourced_scripts_return:
            raise Booted(handover)


def run(board: Board, script: str) -> dict[str, str] | None:
    """Run ``script`` on ``board``: the handover of the boot script or kernel that booted, or None when none did."""
    try:
        Interpreter(board).run(script)
    except Booted as booted:
        return booted.handover
    return None
