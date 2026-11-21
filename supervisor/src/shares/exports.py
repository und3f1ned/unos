"""exports(5) as exportfs reads it (pure): what an export file exports, and where exportfs stops.

A port of the reader of nfs-utils 2.8 (support/nfs/xio.c, getexportent and parseopts of
support/nfs/exports.c), ADR-0292:

* A backslash at the end of a line continues it: exportfs reads it as one space, also inside a
  word, and skips the blanks that start the next line.
* A ``#`` where a path or a client may start begins a comment to the end of the line; inside a
  word it is a character of the word.
* Words end at an unquoted blank; ``"`` turns quoting on and off and is dropped. A backslash
  with three octal digits whose value is below 256 is that byte, also inside quotes.
* A line is a path, then clients: ``client(options)``, ``client`` without options, or
  ``(options)`` for everyone. A word that starts with ``-`` before a client gives default
  options to every later client of the line; one at the end of a line, and a path without
  clients, export to everyone. exportfs's default is read-only.
* An entry exportfs cannot read (an unknown option, a bad option list, a path longer than
  1024 bytes, no path where one should start, ...) ends the reading of its file: neither it
  nor anything after it in that file is exported. The clients of its line before the problem
  were read, and are exported.

Paths and clients are shown as UTF-8, with U+FFFD for bytes that are not. The option list is
checked as nfs-utils 2.8 checks it; an older exportfs may know fewer options. The rules that
span several entries (duplicates, ``reexport=`` with ``fsid=``) are exportfs's own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# getpath reads a path into e_path[NFS_MAXPATHLEN + 1], getexport a client into exp[512]; a word
# that fills its buffer is an error.
PATH_BUFFER = 1025
CLIENT_BUFFER = 512
# Where a path should start, exportfs skips only blanks, newlines and comments: a carriage return
# (a blank line of a file with CRLF line ends) or an empty "" gives it no path, and it stops
# reading the file without a message.
NO_PATH = 'no path where exportfs expects one (a carriage return, or an empty "")'

_EOF = -1
_NUL, _TAB, _NL, _SPACE = 0, 0x09, 0x0A, 0x20
_QUOTE, _HASH, _BACKSLASH = 0x22, 0x23, 0x5C
_ISSPACE = frozenset(b" \t\n\v\f\r")
_PATH_SKIP = frozenset(b" \t\n")
_CLIENT_SKIP = frozenset(b" \t")
_BLANKS = frozenset(b" \t")

_FLAGS = frozenset({
    "ro", "rw", "secure", "insecure", "sync", "async", "nordirplus", "security_label",
    "nohide", "hide", "crossmnt", "nocrossmnt", "wdelay", "no_wdelay",
    "root_squash", "no_root_squash", "all_squash", "no_all_squash",
    "subtree_check", "no_subtree_check", "auth_nlm", "no_auth_nlm",
    "secure_locks", "insecure_locks", "acl", "no_acl", "pnfs", "no_pnfs",
    "mountpoint", "mp",
})
_FLAVORS = frozenset({"krb5", "krb5i", "krb5p", "unix", "sys", "null", "none"})
_XPRTSEC = frozenset({"none", "tls", "mtls"})
_REEXPORT = frozenset({"auto-fsidnum", "predefined-fsidnum", "none"})
# strtol and strtoul: leading white space, a sign, then digits (base 10; base 0 for fsid=).
_STRTOL = re.compile(r"[ \t\n\v\f\r]*[+-]?[0-9]+")
_STRTOUL_BASE0 = re.compile(r"[ \t\n\v\f\r]*[+-]?(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)")
# parsesquash: an id, or a range of two; each an optional '-' and digits, read by atoi.
_SQUASH = re.compile(r"(-?[0-9]*)(?:-(-?[0-9]*))?")
_MINUS_ONE = re.compile(r"-0*1")


@dataclass(frozen=True)
class Client:
    name: str                   # "*" for everyone: also a line without clients, or "(options)"
    options: tuple[str, ...]    # the line's default options, then the client's own, as written
    access: str | None          # "read" or "write": the last ro or rw; None with sec= (per flavor)


@dataclass(frozen=True)
class Entry:
    path: str
    clients: tuple[Client, ...]
    first_line: int             # the line of the path
    last_line: int              # the line that ends the entry (continuations make it longer)


@dataclass(frozen=True)
class Problem:
    line: int
    message: str


@dataclass(frozen=True)
class Parsed:
    entries: tuple[Entry, ...]  # what exportfs exports from the file, in its order
    problem: Problem | None     # where exportfs stopped reading the file, if it did


def _text(word: bytes) -> str:
    return word.decode("utf-8", "replace")


class _Reader:
    """The file as xio.c reads it: getc with one character pushed back, continuations, words."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _getc(self) -> int:
        if self.pos >= len(self.data):
            return _EOF
        c = self.data[self.pos]
        self.pos += 1
        return c

    def _ungetc(self, c: int) -> None:
        # Only ever the character just read (xio.c pushes back what it got from getc).
        if c != _EOF:
            self.pos -= 1

    def xgetc(self) -> int:
        """A character; a backslash and a newline (and the blanks after them) read as one space."""
        c = self._getc()
        if c != _BACKSLASH:
            return c
        c = self._getc()
        if c != _NL:
            self._ungetc(c)
            return _BACKSLASH
        while (c := self._getc()) in (_SPACE, _TAB):
            pass
        self._ungetc(c)
        return _SPACE

    def _skip_comment(self) -> int:
        while (c := self._getc()) not in (_EOF, _NL):
            pass
        return c

    def skip(self, skipped: frozenset[int]) -> None:
        """xskip: the characters of ``skipped`` and comments. NUL counts as one of them: strchr
        finds the end of the string it searches."""
        while (c := self.xgetc()) != _EOF:
            if c == _HASH:
                c = self._skip_comment()
                if c == _EOF:
                    break
            if c != _NUL and c not in skipped:
                break
        self._ungetc(c)

    def word(self, buffer: int) -> tuple[int, bytes]:
        """xgettok without a separator: (1, word), (0, b"") when there is none, (-1, b"") when the
        word fills the buffer. A newline that ends it is pushed back; a NUL ends it as C reads
        the buffer."""
        word = bytearray()
        quoted = False
        c = _NUL
        while len(word) < buffer:
            c = self.xgetc()
            if c == _EOF or (not quoted and (c == _NUL or c in _ISSPACE)):
                break
            if c == _QUOTE:
                quoted = not quoted
                continue
            word.append(c)
            if len(word) >= 4 and word[-4] == _BACKSLASH and all(0x30 <= b <= 0x37 for b in word[-3:]):
                value = int(word[-3:].decode(), 8)
                if value < 256:
                    del word[-4:]
                    word.append(value)
                    c = value
        if c == _NL:
            self._ungetc(c)
        if not word:
            return 0, b""
        if len(word) >= buffer:
            return -1, b""
        return 1, bytes(word).split(b"\0", 1)[0]

    def line_at(self, pos: int) -> int:
        return self.data.count(b"\n", 0, pos) + 1


def _option_problem(option: str) -> str | None:
    """parseopts: why exportfs refuses one option, or None."""
    if option in _FLAGS:
        return None
    key, equals, value = option.partition("=")
    if not equals:
        return f'unknown keyword "{option}"'
    if key in ("anonuid", "anongid"):
        return None if _STRTOL.fullmatch(value) else f"bad {key}"
    if key in ("squash_uids", "squash_gids"):
        # The option ends at the next comma, so the list holds one id or range.
        match = _SQUASH.match(value)
        assert match is not None  # the pattern matches the empty string too
        if any(part is not None and _MINUS_ONE.fullmatch(part) for part in match.groups()):
            return "uid/gid -1 not permitted"
        return None if match.end() == len(value) else "bad uid/gid list"
    if key == "fsid":
        if value == "root" or _STRTOUL_BASE0.fullmatch(value):
            return None
        return None if sum(ch in "0123456789abcdefABCDEF" for ch in value) == 32 else "bad fsid"
    if key in ("mountpoint", "mp", "refer", "replicas"):
        return None
    if key == "sec":
        bad = [name for name in value.split(":") if name not in _FLAVORS]
        return f'unknown flavor "{bad[0]}"' if bad else None
    if key == "xprtsec":
        bad = [name for name in value.split(":") if name not in _XPRTSEC]
        return f'unknown xprtsec mode "{bad[0]}"' if bad else None
    if key == "reexport":
        return None if value in _REEXPORT else f'unknown reexport mode "{value}"'
    return f'unknown keyword "{option}"'


def _options(text: bytes) -> tuple[list[str], str | None]:
    """parseopts: the options of a list (split at commas, blanks before each skipped), and the
    problem of the first one that exportfs refuses."""
    options: list[str] = []
    i, end = 0, len(text)
    while i < end and text[i] in _BLANKS:
        i += 1
    while i < end:
        comma = text.find(b",", i)
        if comma < 0:
            raw, i = text[i:], end
        else:
            raw, i = text[i:comma], comma + 1
        option = _text(raw)
        problem = _option_problem(option)
        if problem is not None:
            return options, problem
        options.append(option)
        while i < end and text[i] in _BLANKS:
            i += 1
    return options, None


def _access(options: list[str]) -> str | None:
    if any(option.startswith("sec=") for option in options):
        return None  # ro and rw then apply to the flavors of the sec= before them
    writable = False
    for option in options:
        if option in ("ro", "rw"):
            writable = option == "rw"
    return "write" if writable else "read"


def parse(data: bytes) -> Parsed:
    """The entries of an export file, read as exportfs reads them (getexportent, one client at a
    time), and the problem that ends its reading, if any."""
    reader = _Reader(data)
    entries: list[Entry] = []
    path: bytes | None = None
    first_line = 0
    clients: list[Client] = []
    defaults: list[str] = []

    def finish(end: int) -> None:
        if path is not None and clients:
            entries.append(Entry(_text(path), tuple(clients), first_line, reader.line_at(end)))

    def stop(pos: int, message: str) -> Parsed:
        finish(pos)
        return Parsed(tuple(entries), Problem(reader.line_at(pos), message))

    first = True
    while True:
        new_line = first
        if not first:
            reader.skip(_CLIENT_SKIP)
            start = reader.pos
            ok, word = reader.word(CLIENT_BUFFER)
            new_line = ok == 0
        if new_line:
            finish(reader.pos)
            path, clients, defaults = None, [], []
            reader.skip(_PATH_SKIP)
            start = reader.pos
            ok, word = reader.word(PATH_BUFFER)
            if ok == 0:
                return Parsed(tuple(entries), None) if start == len(data) else stop(start, NO_PATH)
            if ok < 0:
                return stop(start, "a path longer than 1024 bytes")
            path, first_line = word, reader.line_at(start)
            reader.skip(_CLIENT_SKIP)
            start = reader.pos
            ok, word = reader.word(CLIENT_BUFFER)
        if ok < 0:
            return stop(start, "a client longer than 511 bytes")
        first = False
        if word.startswith(b"-"):
            added, problem = _options(word[1:])
            if problem is not None:
                return stop(start, problem)
            defaults = defaults + added
            reader.skip(_CLIENT_SKIP)
            start = reader.pos
            ok, word = reader.word(CLIENT_BUFFER)
            if ok < 0:
                return stop(start, "a client longer than 511 bytes")
        name = word
        own: list[str] = []
        paren = word.find(b"(")
        if paren >= 0:
            name = word[:paren] if paren > 0 else b"*"
            inside = word[paren + 1:]
            close = inside.find(b")")
            if close < 0 or close != len(inside) - 1:
                return stop(start, "syntax error: bad option list")
            own, problem = _options(inside[:-1])
            if problem is not None:
                return stop(start, problem)
        options = defaults + own
        clients.append(Client(_text(name) or "*", tuple(options), _access(options)))
