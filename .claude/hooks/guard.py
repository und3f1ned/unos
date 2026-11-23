#!/usr/bin/env python3
"""PreToolUse guard for the shell commands of Claude Code sessions in this repository (ADR-0211).

.claude/settings.json runs this script before every command of the Bash tool and of the desktop
app's terminal tool (mcp__terminal__run_in_terminal, whose command runs in the owner's login
shell) and passes the hook input as JSON on standard input (tool_name, tool_input.command, cwd;
the terminal tool's tool_input.cwd and the Bash tool's tool_input.run_in_background too). The
script splits the command into simple commands (at ; && || | & newlines, subshells and command
substitutions, with heredoc bodies kept apart), parses each with shlex, follows wrappers (sudo,
env, timeout, xargs, bash -c, eval, ...) and treats the remote command of ssh,
scripts/dev/ssh.sh and dev.sh as commands on the dev device. It blocks, with exit code 2 and one
line on stderr that names the rule and what to do instead:

  push-main        git push to main or master (also --all, and a push without a refspec that git's
                   configuration sends there: @{push}, the upstream, push.default matching),
                   gh pr merge
  device-push      git push on the dev device
  force-push       --force, --force-with-lease, +refspec, --mirror
  delete-branch    --delete, :branch, --prune
  skip-hooks       --no-verify, git commit -n, another core.hooksPath, a green run recorded by
                   hand, which the pre-commit hook and the Stop hook would take for a passed make
                   check: green-run.py and record in one command, a copy of green-run.py, a program
                   on standard input given record, code (-c, -e) that names stop-check or both
                   green-run and record, and a write into the folder tmp/stop-check (a redirection,
                   the target of cp, mv, ln, install, rsync, ditto, dd's of=, sed -i, and any path
                   in it given to a command that does not only read or remove files). Best effort
                   against a deliberate way around it (ADR-0383)
  e2e-target       Playwright runs whose BASE_URL or HTTP_URL is not local, Playwright on the device
  disk-wipe        mkfs, wipefs -a, dd or a redirection to a disk, blkdiscard, sgdisk, sfdisk,
                   fdisk, parted, shred on a disk, cryptsetup luksFormat, btrfs device remove,
                   the pool create and delete calls of scripts/dev/api.py, ...
  device-docker    on the device: docker build, pull, run, create, compose build/pull/run/create/up,
                   prunes, app installs through scripts/dev/api.py
  device-packages  on the device: apt and apt-get install/upgrade/remove/..., dpkg -i, pip install,
                   npm install or ci
  power            reboot, poweroff, shutdown, halt, /proc/sysrq-trigger (here and on the device)
  secret-argv      sshpass -p, curl -u user:password, --password=..., user:password@ in URLs, a
                   literal assigned to a secret's name (..._PASSWORD, ..._PASS, ..._TOKEN,
                   ..._SECRET, ..._API_KEY, ..._PRIVATE_KEY, ..._ACCESS_KEY, ..._AUTHKEY; not
                   ..._FILE, ..._LENGTH, ...), a literal Authorization header, zip -P
  dev-env          showing, copying or replacing dev.env, and showing, copying or extracting the
                   device backups of tmp/dev/backup; recursive searches, copies and tar or zip
                   archives of a tree that holds either, also of a linked worktree's main
                   checkout, whose dev.env and backups the worktree's scripts use
  background-deadline  a command that the Bash tool runs in the background (run_in_background)
                   and that is not one call of timeout with a deadline of at most 7200 s, -k
                   included: [cd DIR &&]... [NAME=VALUE ...] timeout [-k DUR] [-s TERM, INT,
                   HUP, QUIT or KILL] [-p] [-v] DURATION COMMAND [ARGS] [REDIRECTIONS], with no
                   ; && || | & or newline after it and no $(...), backticks or <(...) outside
                   single quotes; loops and pipelines go inside timeout <seconds> bash -c '...'.
                   Claude Code wakes the session only when a background command ends

Everything else is allowed (exit 0). An internal error never blocks: the script prints a
warning and exits 1, which Claude Code reports without stopping the command. Every denial adds
one line to the git-ignored tmp/guard/denials.log of the project: the UTC time, the rule and
the tool, never the command, which may hold a secret (best effort: a failed write blocks
nothing); the monthly agent-setup pass reads it (docs/maintenance.md). The cases are in
supervisor/tests/test_agent_guard.py. Standard library only; Python 3.8 or later.

Known limits: the guard reads the command line, not what it runs at run time.
- Commands built from variables or files are checked only as far as the line shows them: a
  variable set earlier on the same line is followed ($SSH 'docker ps'), one from the environment
  or a file is not (eval "$CMD", bash -c "$(cat steps.sh)").
- The contents of the scripts and programs a command runs are not read (./x.sh, python3 x.py, a
  make recipe): the scripts in scripts/dev, scripts/standin and the Makefile are trusted as they
  are, and a new script that does what a rule blocks must not be run to get around it.
- Arguments that arrive on standard input, from a file or from a command's output are unknown:
  echo main | xargs git push origin, find . -exec cat {} +, tar -T list, cat $(ls *.env).
- Where a push without a refspec goes is asked from git in the command's directory, with the
  command's git -c push settings; settings in the environment (GIT_CONFIG_*) are not followed.
  A path to a secret is recognized by its name (dev.env, tmp/dev/backup); a copy elsewhere, or
  a path built in a program's code, is not.
- timeout ends a background command at its deadline with SIGTERM, or the signal of -s: a
  command that catches or ignores it runs on unless -k also sends SIGKILL, and a process that
  leaves timeout's process group (setsid, a daemon) outlives it. The outer shell opens the
  redirections before timeout starts: one of a FIFO waits for its other end without a deadline.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import time
from urllib.parse import urlsplit

LOCAL, DEVICE = "local", "device"
# The owner's secrets on this machine (rule dev-env): dev.env, and the device backups that
# scripts/dev/deploy.sh keeps in tmp/dev/backup of the main checkout (ADR-0223).
DEV_ENV, BACKUP = "dev.env", "tmp/dev/backup"
BACKUP_RULE = ("the device backups in tmp/dev/backup hold the device's JWT secret, password hashes and "
               "database, which are never shown, copied or extracted (ADR-0212, ADR-0223). Only "
               "scripts/dev/deploy.sh rollback reads them; ask the owner")
# The record of the green run (rule skip-hooks): make check and make check-fast write it when they
# pass, and the hooks skip make check-fast for a tree in its state (scripts/green-run.py, ADR-0383).
STOP_CHECK = "tmp/stop-check"
GREEN_RULE = ("make check and make check-fast record a green run in tmp/stop-check when they pass, and the "
              "pre-commit hook and the Stop hook then skip make check-fast (ADR-0383). Run make check-fast "
              "instead")
# Commands that may name the folder of the green run: they only read files, print, or remove them,
# which makes the hooks run make check-fast. sed without -i and find without -exec read.
_GREEN_READS = frozenset({
    "cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "ls", "stat", "wc", "file", "diff",
    "cmp", "test", "[", "[[", "echo", "printf", "realpath", "readlink", "basename", "dirname", "du", "sha256sum",
    "shasum", "md5", "md5sum", "xxd", "hexdump", "od", "strings", "cut", "sort", "uniq", "tr", "nl", "jq", "awk",
    "git", "rm", "rmdir", "unlink", "touch", "chmod", "mkdir", "sed", "find", "true", "false", ":"})
_GREEN_COPIERS = frozenset({"cp", "mv", "ln", "install", "rsync", "ditto", "gcp", "gmv"})
SUBST = "__guard_subst__"  # stands for the output of a command substitution
MAX_DEPTH = 16  # nested scripts (substitutions, bash -c, remote commands)
MAX_STDIN_FILE = 1 << 20  # a local file sent to a remote shell is read up to this size

# --- tokens -------------------------------------------------------------------------------

_OPERATORS = (";;&", "<<<", "<<-", "&>>", ";;", ";&", "&&", "||", "|&", ">>", "<<", "<>", ">&",
              "<&", "&>", ">|", ";", "&", "|", "(", ")", "<", ">")
_REDIRECTS = {"<", ">", ">>", "<<", "<<-", "<<<", "<>", ">&", "<&", "&>", "&>>", ">|"}
_OUTPUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<>"}
_ANSI_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b",
                 "f": "\f", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


class Blocked(Exception):
    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


def _operator_at(s: str, i: int) -> str:
    for op in _OPERATORS:
        if s.startswith(op, i):
            return op
    return s[i]


def _skip_arithmetic(s: str, i: int, nested: list) -> int:
    """The index after the '))' that closes $(( or (( whose content starts at i. The command
    substitutions in it run (echo $(( $(cmd) + 1 ))): they are appended to `nested`."""
    start, depth = i, 0
    while i < len(s):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            if depth == 0:
                end = i + 2 if s.startswith("))", i) else i + 1
                break
            depth -= 1
        i += 1
    else:
        end = len(s)
    _substitutions(s[start:i], nested)
    return end


def _extract(s: str, start: int, stop: str) -> tuple:
    """The raw text of $(...) or `...` whose content starts at start, and the index after it."""
    _, _, end, found = _lex(s, start, stop, [])
    inner = s[start:end - 1] if found else s[start:end]
    if stop == "`":
        inner = re.sub(r"\\([`\\$])", r"\1", inner)
    return inner, end


def _double_quoted(s: str, i: int, nested: list) -> tuple:
    """A double-quoted word starting after its opening quote, substitutions replaced by SUBST."""
    part = ['"']
    while i < len(s) and s[i] != '"':
        if s[i] == "\\" and i + 1 < len(s):
            part.append(s[i:i + 2])
            i += 2
        elif s.startswith("$((", i):
            i = _skip_arithmetic(s, i + 3, nested)
            part.append("0")
        elif s.startswith("$(", i):
            inner, i = _extract(s, i + 2, ")")
            nested.append(inner)
            part.append(SUBST)
        elif s[i] == "`":
            inner, i = _extract(s, i + 1, "`")
            nested.append(inner)
            part.append(SUBST)
        else:
            part.append(s[i])
            i += 1
    part.append('"')
    return "".join(part), min(i + 1, len(s))


def _ansi_c(s: str, i: int) -> tuple:
    """A $'...' word whose content starts at i, as a plain single-quoted word."""
    buf = []
    while i < len(s) and s[i] != "'":
        if s[i] == "\\" and i + 1 < len(s):
            buf.append(_ANSI_ESCAPES.get(s[i + 1], "\\" + s[i + 1]))
            i += 2
        else:
            buf.append(s[i])
            i += 1
    return shlex.quote("".join(buf)), min(i + 1, len(s))


def _heredoc_word(s: str, j: int) -> tuple:
    """The delimiter of a heredoc at j: (word without quotes, quoted, end)."""
    word, quoted = [], False
    while j < len(s) and s[j] not in " \t\n;&|()<>":
        if s.startswith(("$'", '$"'), j):  # <<$'EOF' is quoted as <<'EOF' is
            j += 1
        elif s[j] in "'\"":
            end = s.find(s[j], j + 1)
            end = len(s) if end < 0 else end
            word.append(s[j + 1:end])
            quoted, j = True, end + 1
        elif s[j] == "\\" and j + 1 < len(s):
            word.append(s[j + 1])
            quoted, j = True, j + 2
        else:
            word.append(s[j])
            j += 1
    return "".join(word), quoted, j


def _heredoc_body(s: str, i: int, delimiter: str, strip_tabs: bool) -> tuple:
    lines = []
    while i < len(s):
        end = s.find("\n", i)
        line = s[i:] if end < 0 else s[i:end]
        following = len(s) if end < 0 else end + 1
        if (line.lstrip("\t") if strip_tabs else line) == delimiter:
            return "\n".join(lines), following
        lines.append(line)
        i = following
    return "\n".join(lines), len(s)


def _substitutions(text: str, nested: list) -> None:
    """The command substitutions of an unquoted heredoc body: they run."""
    i = 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text.startswith("$((", i):
            i = _skip_arithmetic(text, i + 3, nested)
        elif text.startswith("$(", i):
            inner, i = _extract(text, i + 2, ")")
            nested.append(inner)
        elif text[i] == "`":
            inner, i = _extract(text, i + 1, "`")
            nested.append(inner)
        else:
            i += 1


def _lex(s: str, i: int, stop, nested: list) -> tuple:
    """Shell tokens of s from i to `stop` (')' or '`' at depth 0) or the end.

    Returns (tokens, heredoc bodies, end index, stop found). A token is ("word", raw text
    with its quotes), ("op", separator) or ("redir", operator). The commands of substitutions
    are appended to `nested`; a substitution itself becomes SUBST in its word.
    """
    tokens: list = []
    bodies: list = []
    pending: list = []
    word: list = []
    depth = 0

    def flush() -> None:
        if word:
            tokens.append(("word", "".join(word)))
            word.clear()

    n = len(s)
    while i < n:
        c = s[i]
        if stop is not None and c == stop and depth == 0:
            flush()
            return tokens, bodies, i + 1, True
        if c == "\\":
            if s.startswith("\\\n", i):
                i += 2
                continue
            word.append(s[i:i + 2])
            i += 2
        elif c == "'":
            end = s.find("'", i + 1)
            if end < 0:
                word.append(s[i:] + "'")
                i = n
            else:
                word.append(s[i:end + 1])
                i = end + 1
        elif s.startswith("$'", i):
            text, i = _ansi_c(s, i + 2)
            word.append(text)
        elif c == '"':
            text, i = _double_quoted(s, i + 1, nested)
            word.append(text)
        elif s.startswith("$((", i):
            i = _skip_arithmetic(s, i + 3, nested)
            word.append("0")
        elif s.startswith("$(", i):
            inner, i = _extract(s, i + 2, ")")
            nested.append(inner)
            word.append(SUBST)
        elif c == "`":
            inner, i = _extract(s, i + 1, "`")
            nested.append(inner)
            word.append(SUBST)
        elif c in "<>" and s.startswith("(", i + 1) and not word:
            inner, i = _extract(s, i + 2, ")")
            nested.append(inner)
            word.append("/dev/fd/63")
        elif c in " \t":  # a carriage return is part of a word to bash and zsh
            flush()
            i += 1
        elif c == "\n":
            flush()
            tokens.append(("op", "\n"))
            i += 1
            for delimiter, strip_tabs, expands in pending:
                body, i = _heredoc_body(s, i, delimiter, strip_tabs)
                if expands:
                    _substitutions(body, nested)
                bodies.append(body)
            pending = []
        elif c == "#" and not word:
            end = s.find("\n", i)
            i = n if end < 0 else end
        elif s.startswith("((", i) and not word and (not tokens or tokens[-1][0] == "op"):
            i = _skip_arithmetic(s, i + 2, nested)
            tokens.append(("op", ";"))
        elif c in ";&|()<>":
            if c in "<>" and word and "".join(word).isdigit():
                word.clear()  # a file descriptor: 2>&1
            else:
                flush()
            op = _operator_at(s, i)
            i += len(op)
            if op == "(":
                depth += 1
            elif op == ")":
                depth -= 1
            if op in _REDIRECTS:
                tokens.append(("redir", op))
                if op in ("<<", "<<-"):
                    while i < n and s[i] in " \t":
                        i += 1
                    delimiter, quoted, i = _heredoc_word(s, i)
                    tokens.append(("word", shlex.quote(delimiter)))
                    pending.append((delimiter, op == "<<-", not quoted))
            else:
                tokens.append(("op", op))
        else:
            word.append(c)
            i += 1
    flush()
    bodies.extend("" for _ in pending)
    return tokens, bodies, n, False


class Word(str):
    """A word of a command; `glob` is True when it has unquoted *, ? or [."""

    glob = False


def _words(raw: str) -> list:
    """The Words of a raw token (with its quotes): one, or those of its brace expansion, as bash
    makes them before the command runs (cp dev.env{,.bak} is cp dev.env dev.env.bak)."""
    words = []
    for text in ([raw] if _ASSIGNMENT.match(raw) else _brace_expansion(raw) or [raw]):
        word = Word(_unquote(text))
        word.glob = _has_unquoted_glob(text)
        words.append(word)
    return words


def _brace_expansion(raw: str, limit: int = 64) -> list:
    """The raw words that the first unquoted {a,b,...} of a raw token expands to (each expanded
    further), or [] when it has none. ${...} is a parameter, not a list."""
    i, quote, depth, start, commas = 0, None, 0, -1, []
    while i < len(raw):
        c = raw[i]
        if quote:
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"':
                i += 1
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "$" and raw.startswith("${", i):
            end = raw.find("}", i)
            i = len(raw) if end < 0 else end
        elif c == "{":
            if depth == 0:
                start, commas = i, []
            depth += 1
        elif c == "}" and depth:
            depth -= 1
            if depth == 0 and commas:
                bounds = [start] + commas + [i]
                parts = [raw[a + 1:b] for a, b in zip(bounds, bounds[1:])]
                words: list = []
                for part in parts:
                    alternative = raw[:start] + part + raw[i + 1:]
                    words.extend(_brace_expansion(alternative, limit) or [alternative])
                    if len(words) >= limit:
                        break
                return words[:limit]
        elif c == "," and depth == 1:
            commas.append(i)
        i += 1
    return []


def _has_unquoted_glob(raw: str) -> bool:
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            i += 2
        elif c == "'":
            end = raw.find("'", i + 1)
            i = len(raw) if end < 0 else end + 1
        elif c == '"':
            j = i + 1
            while j < len(raw) and raw[j] != '"':
                j += 2 if raw[j] == "\\" else 1
            i = j + 1
        elif c in "*?[":
            return True
        else:
            i += 1
    return False


def _unquote(raw: str) -> str:
    lexer = shlex.shlex(raw, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        parts = list(lexer)
    except ValueError:
        return raw
    return " ".join(parts)


def _split_words(text: str) -> list:
    try:
        return shlex.split(text, comments=False, posix=True)
    except ValueError:
        return text.split()


class Simple:
    """One simple command: its words, redirections and what it gets on standard input."""

    def __init__(self) -> None:
        self.words: list = []
        self.redirects: list = []  # (operator, target)
        self.stdin: list = []  # heredoc bodies and here-strings


def _simple_commands(tokens: list, bodies: list) -> list:
    commands, current, heredoc = [], Simple(), 0
    i = 0
    while i < len(tokens):
        kind, value = tokens[i]
        if kind == "op":
            if current.words or current.redirects or current.stdin:
                commands.append(current)
            current = Simple()
            i += 1
            continue
        if kind == "redir":
            has_target = i + 1 < len(tokens) and tokens[i + 1][0] == "word"
            target = _unquote(tokens[i + 1][1]) if has_target else ""
            if value in ("<<", "<<-"):
                current.stdin.append(bodies[heredoc] if heredoc < len(bodies) else "")
                heredoc += 1
            elif value == "<<<":
                current.stdin.append(target)
            else:
                current.redirects.append((value, target))
            i += 2 if has_target else 1
            continue
        current.words.extend(_words(value))
        i += 1
    if current.words or current.redirects or current.stdin:
        commands.append(current)
    return commands


# --- small predicates -------------------------------------------------------------------

_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\+?=(.*)$", re.S)
_VARIABLE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_NOT_LITERAL = re.compile(r"(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$[0-9@*#?$!-]|" + SUBST + ")+")
_DEV_ENV = re.compile(r"(?<![\w.-])dev\.env(?![\w.-])")
_URL_PASSWORD = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]*:[^/\s@]+@")
# A device backup of scripts/dev/deploy.sh: tmp/dev/backup of a checkout (ADR-0223), as a path
# and in the code of an interpreter.
_BACKUP = re.compile(r"(?:^|/)tmp/dev/backup(?:/|$)")
_BACKUP_TEXT = re.compile(r"(?<![\w.-])tmp/dev/backup(?![\w.-])")
_SECRET_WORDS = ("PASSWORD", "PASSWD", "PASSPHRASE", "TOKEN", "SECRET", "AUTHKEY", "APIKEY", "PRIVATEKEY",
                 "ACCESSKEY")
_SECRET_KEY_KINDS = {"API", "PRIVATE", "ACCESS", "AUTH"}  # API_KEY, PRIVATE_KEY, ACCESS_KEY, AUTH_KEY
_NOT_SECRET_PASS = {"BYPASS", "COMPASS", "ENCOMPASS", "ASKPASS", "SURPASS", "OVERPASS", "UNDERPASS",
                    "TRESPASS", "MULTIPASS", "SUBPASS"}
_NOT_SECRET_LAST = {"FILE", "PATH", "DIR", "FD", "CMD", "COMMAND", "HELPER", "REQUIRE", "LENGTH", "LEN",
                    "MIN", "MAX", "POLICY", "TTL", "EXPIRY", "EXPIRE", "EXPIRES", "MINUTES", "SECONDS",
                    "HOURS", "DAYS", "LIFETIME", "TIMEOUT", "URL", "NAME", "ID", "TYPE", "MODE",
                    "ALGORITHM", "HEADER", "PROMPT", "METHOD", "STDIN", "ENV", "ENABLED", "DISABLED",
                    "REQUIRED", "LOCATION", "ROUNDS", "LIMIT", "COUNT", "SIZE", "BYTES"}
_SECRET_OPTION_SUFFIXES = ("-file", "-path", "-stdin", "-fd", "-env", "-dir", "-cmd", "-command",
                           "-helper", "-prompt", "-length", "-min", "-max", "-policy", "-name", "-id",
                           "-type", "-ttl", "-expiry", "-url", "-header", "-store", "-source")
_SAFE_DEVICES = {"/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom", "/dev/stdin",
                 "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/ptmx", "/dev/console"}
_SAFE_DEVICE_DIRS = ("/dev/fd/", "/dev/shm/", "/dev/pts/", "/dev/mqueue/", "/dev/tty")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "webui"}
# The tools whose tool_input.command is a shell command line (.claude/settings.json's matcher).
COMMAND_TOOLS = ("Bash", "mcp__terminal__run_in_terminal")
SYSRQ = "/proc/sysrq-trigger"  # a write there reboots or powers off at once
DENIALS_LOG = os.path.join("tmp", "guard", "denials.log")  # in the project directory
_KEYWORDS = {"!", "{", "}", "then", "do", "else", "elif", "if", "while", "until", "time", "coproc"}
_HEADERS = {"for", "select", "case", "function", "esac", "fi", "done", "in"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "mksh"}
_INTERPRETERS = re.compile(r"^(python[0-9.]*|node|nodejs|perl|ruby|php|deno|bun|osascript|lua)$")


def _is_secret_name(name: str) -> bool:
    """Whether a variable (or a key of a YAML or JSON file) names a secret. Its parts, split at
    _ - . and where a capital follows a small letter (apiKey), are checked: one ending in
    PASSWORD, PASSWD, PASSPHRASE, TOKEN, SECRET, PASS (not BYPASS, ASKPASS, ...), AUTHKEY or
    APIKEY, or KEY after API, PRIVATE, ACCESS or AUTH. A name whose last part says something
    about the secret (..._FILE, ..._LENGTH, ..._ID, ...) holds none. The secret scan of
    supervisor/tests/test_repo_tooling.py uses this rule too."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    parts = [part for part in re.split(r"[_.-]+", spaced.upper()) if part]
    if not parts or parts[-1] in _NOT_SECRET_LAST or any(part.endswith("ASKPASS") for part in parts):
        return False
    for i, part in enumerate(parts):
        if part.endswith(_SECRET_WORDS):
            return True
        if part.endswith("PASS") and part not in _NOT_SECRET_PASS:
            return True
        if part == "KEY" and i and parts[i - 1] in _SECRET_KEY_KINDS:
            return True
    return False


def _is_literal(value: str) -> bool:
    return bool(value) and not _NOT_LITERAL.fullmatch(value)


def _is_secret_option(key: str) -> bool:
    key = key.lower()
    if key.endswith(_SECRET_OPTION_SUFFIXES):
        return False
    return bool(re.search(r"(^|[-_])pass$|password$|passwd$|passphrase$|secret|token|api-?key", key))


def _is_device_path(path: str) -> bool:
    if not path.startswith("/dev/") or path in _SAFE_DEVICES:
        return False
    return not path.startswith(_SAFE_DEVICE_DIRS)


def _is_local_url(value: str) -> bool:
    try:
        host = urlsplit(value).hostname
    except ValueError:
        return False
    return host is not None and host.lower() in LOCAL_HOSTS


def _cluster(arg: str) -> str:
    """The letters of a short-option cluster such as -rn, or '' for anything else."""
    return arg[1:] if len(arg) > 1 and arg[0] == "-" and arg[1] != "-" else ""


def _positionals(args: list, value_options: set) -> list:
    """The arguments that are not options or option values."""
    out, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            out.extend(args[i + 1:])
            break
        if arg.startswith("-") and len(arg) > 1:
            i += 2 if (arg in value_options and "=" not in arg) else 1
            continue
        out.append(arg)
        i += 1
    return out


# The options of scripts/dev/api.py that take a value (its argparse parser, which also takes a
# long option by a unique abbreviation: --par for --param).
_API_VALUE_OPTIONS = ("--param", "--timeout")


def _api_positionals(args: list) -> list:
    """METHOD, PATH and the body of scripts/dev/api.py as its parser reads them: the value of
    --param KEY=VALUE or --timeout SECONDS is not the method."""
    value_options = {arg for arg in args
                     if len(arg) > 2 and "=" not in arg and any(o.startswith(arg) for o in _API_VALUE_OPTIONS)}
    return _positionals(args, value_options)


def _after_options(args: list, value_options: set = frozenset()) -> int:
    """The index of the first argument that is not an option (the wrapped command)."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            return i + 1
        if not arg.startswith("-") or arg == "-":
            return i
        i += 2 if arg in value_options else 1
    return i


_TAR_NAMES = ("tar", "gtar", "bsdtar")
# Short options of tar that take a value, and what the value is.
_TAR_VALUE_LETTERS = {"f": "file", "C": "dir", "T": "list", "X": "xlist", "b": "", "H": "", "I": "", "K": "",
                      "L": "", "N": "", "V": "", "g": "", "F": ""}
_TAR_LONG_VALUES = {"--file": "file", "--directory": "dir", "--files-from": "list", "--exclude-from": "xlist",
                    "--exclude": "exclude", "--blocking-factor": "", "--format": "", "--use-compress-program": "",
                    "--newer": "", "--after-date": "", "--label": "", "--listed-incremental": "",
                    "--starting-file": "", "--tape-length": "", "--owner": "", "--group": "", "--mode": "",
                    "--mtime": "", "--transform": "", "--xform": "", "--strip-components": "", "--level": "",
                    "--options": "", "--info-script": "", "--new-volume-script": "", "--rsh-command": "",
                    "--suffix": "", "--exclude-tag": "", "--exclude-tag-all": "", "--exclude-tag-under": "",
                    "--record-size": "", "--index-file": ""}
_TAR_LONG_MODES = {"--create": "c", "--append": "r", "--update": "u", "--catenate": "A", "--concatenate": "A",
                   "--extract": "x", "--get": "x", "--list": "t", "--diff": "d", "--compare": "d", "--delete": "D"}


class _TarPlan:
    def __init__(self) -> None:
        self.modes: set = set()
        self.items: list = []  # (kind, value) in command-line order: file, dir, list, xlist, exclude, source


def _tar_plan(args: list) -> _TarPlan:
    """What a tar command line does: its modes (c, r, u, x, t, ...) and its arguments in order."""
    plan, rest = _TarPlan(), list(args)

    def value_of(kind: str, value: str) -> None:
        if kind:
            plan.items.append((kind, value))

    if rest and not rest[0].startswith("-"):  # the old style: tar czf archive.tgz dir
        letters, rest = rest[0], rest[1:]
        for letter in letters:
            if letter in "cruAxtdD":
                plan.modes.add(letter)
            if letter in _TAR_VALUE_LETTERS and rest:
                value_of(_TAR_VALUE_LETTERS[letter], rest.pop(0))
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--":
            plan.items.extend(("source", value) for value in rest[i + 1:])
            break
        if arg.startswith("--"):
            key, equals, value = arg.partition("=")
            if key in _TAR_LONG_MODES:
                plan.modes.add(_TAR_LONG_MODES[key])
            elif key in _TAR_LONG_VALUES:
                if not equals and i + 1 < len(rest):
                    i += 1
                    value = rest[i]
                value_of(_TAR_LONG_VALUES[key], value)
            elif key == "--exclude-vcs-ignores":
                plan.items.extend((("exclude", "dev.env"), ("exclude", "tmp")))  # git ignores both
        elif arg.startswith("-") and len(arg) > 1:
            letters = arg[1:]
            for j, letter in enumerate(letters):
                if letter in "cruAxtdD":
                    plan.modes.add(letter)
                if letter in _TAR_VALUE_LETTERS:
                    value = letters[j + 1:]
                    if not value and i + 1 < len(rest):
                        i += 1
                        value = rest[i]
                    value_of(_TAR_VALUE_LETTERS[letter], value)
                    break
        else:
            plan.items.append(("source", arg))
        i += 1
    return plan


class _ZipPlan:
    def __init__(self) -> None:
        self.archive = None
        self.sources: list = []
        self.excludes: list = []  # the patterns of -x
        self.includes: list = []  # the patterns of -i
        self.recursive = False  # -r or -R: without it a directory is added without its files
        self.unknown = False  # patterns from a file (-x@list, -i@list)


def _zip_plan(args: list) -> _ZipPlan:
    """What a zip command line adds: zip [options] archive [files] [-x patterns] [-i patterns]."""
    plan, listing, i = _ZipPlan(), None, 0
    values = {"-b", "-n", "-t", "-tt", "-O", "--out", "-P", "--password", "-Z", "--compression-method",
              "-s", "--split-size", "-sp", "-ll", "-ld"}
    while i < len(args):
        arg = args[i]
        if arg in ("-x", "--exclude", "-i", "--include"):
            listing = plan.excludes if arg in ("-x", "--exclude") else plan.includes
        elif arg.startswith(("-x@", "-i@")):
            plan.unknown, listing = True, None
        elif arg.startswith(("-x", "-i")) and len(arg) > 2:
            (plan.excludes if arg[1] == "x" else plan.includes).append(arg[2:])
        elif arg.startswith("-") and len(arg) > 1:
            listing = None
            if arg in values:
                i += 1
            elif arg in ("--recurse-paths", "--recurse-patterns") or (
                    not arg.startswith("--") and set(arg[1:]) & set("rR")):
                plan.recursive = True
        elif listing is not None:
            listing.append(arg)
        elif plan.archive is None:
            plan.archive = arg
        else:
            plan.sources.append(arg)
        i += 1
    return plan


def _joined(cwd, path: str):
    """The absolute path that `path` names in `cwd`, or None when that cannot be told. $PWD
    and $HOME at the start are known; other variables and substitutions are not."""
    if cwd is None:
        return None
    for names, value in ((("$PWD", "${PWD}"), cwd), (("$HOME", "${HOME}"), os.path.expanduser("~"))):
        for name in names:
            if path == name or path.startswith(name + "/"):
                path = value + path[len(name):]
    if "$" in path or SUBST in path:
        return None
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))


def _git(cwd, *args: str):
    """The output of a git command that only reads, run in cwd; None when it fails. git takes no
    optional locks: the guard may run beside another git command."""
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        result = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5,
                                env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _main_checkout(directory):
    """The main checkout of the linked git worktree (git worktree add) that holds `directory`:
    the directory of git's common directory, as scripts/dev/env.sh finds it. The scripts of a
    worktree without a dev.env of its own use the main checkout's (ADR-0210). None in the main
    checkout itself, outside git, and for a bare repository or a separate git directory."""
    output = _git(directory, "rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir")
    lines = [line for line in (output or "").splitlines() if line and not line.startswith("--")]
    if len(lines) != 2:
        return None
    common, own = (os.path.realpath(os.path.join(directory, line)) for line in lines)
    if common == own or os.path.basename(common) != ".git":
        return None
    return os.path.dirname(common)


def _inside(path: str, directory: str) -> bool:
    """Whether `path` is `directory` or lies inside it (both absolute)."""
    path, directory = os.path.realpath(path), os.path.realpath(directory)
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


def _left_out(secret: str, excludes: list, includes: list, folders: list = (), files_only: bool = False) -> bool:
    """Whether the patterns of a recursive read or an archive leave a secret out: an exclusion
    that matches it (for a device backup also one of the folders tmp, dev, backup on its path),
    or inclusions that all miss it. `folders` are patterns for folders only (grep --exclude-dir);
    with `files_only` the other patterns match file names only (grep --exclude, --include)."""
    def parts(pattern: str) -> list:
        return [part for part in re.sub(r"^(\./)+", "", pattern.strip()).split("/") if part]

    def names_file(pattern: str, name: str) -> bool:
        # tmp/* does not name the dev.env beside tmp; */dev.env and dev.env do.
        found = parts(pattern)
        return bool(found) and fnmatch.fnmatchcase(name, found[-1]) and all(
            set(part) <= {"*"} for part in found[:-1])

    files = ("dev.env",) if secret == DEV_ENV else ("device-state.tar",)
    dirs = () if secret == DEV_ENV else ("tmp", "dev", "backup")
    folder_patterns = list(folders) + ([] if files_only else list(excludes))
    if any(names_file(pattern, name) for pattern in excludes for name in files):
        return True
    if any(fnmatch.fnmatchcase(name, part) for pattern in folder_patterns for part in parts(pattern)
           if set(part) - {"*"} for name in dirs):
        return True
    return bool(includes) and not any(names_file(pattern, name) or fnmatch.fnmatchcase(name, pattern)
                                      for pattern in includes for name in files)


# --- the guard ------------------------------------------------------------------------------


class State:
    def __init__(self, ctx: str, cwd, env: dict, depth: int, stdin: list = ()) -> None:
        self.ctx = ctx
        self.cwd = cwd
        self.env = dict(env)
        self.depth = depth
        self.stdin = list(stdin)


class Guard:
    def __init__(self, cwd, project_dir) -> None:
        self.cwd = cwd
        self.project_dir = project_dir
        self._secret_places = None

    # entry points

    def check(self, command: str) -> None:
        self.check_script(command, State(LOCAL, self.cwd, {}, 0))

    def check_script(self, text: str, state: State) -> None:
        if state.depth > MAX_DEPTH:
            return
        nested: list = []
        tokens, bodies, _, _ = _lex(text, 0, None, nested)
        for inner in nested:
            self.check_script(inner, State(state.ctx, state.cwd, state.env, state.depth + 1))
        for simple in _simple_commands(tokens, bodies):
            self.check_simple(simple, state)

    def check_simple(self, simple: Simple, state: State) -> None:
        self.check_redirects(simple, state)
        words = list(simple.words)
        while words and words[0] in _KEYWORDS:
            words.pop(0)
            if words and words[0] == "-p":
                words.pop(0)
        if not words or words[0] in _HEADERS:
            return
        assignments = []
        while words and _ASSIGNMENT.match(words[0]):
            name, value = _ASSIGNMENT.match(words[0]).groups()
            assignments.append((name, value))
            words.pop(0)
        for name, value in assignments:
            self.check_assignment(name, value)
        if not words:
            state.env.update(assignments)
            return
        env = dict(state.env)
        env.update(assignments)
        self.check_command(words, env, simple, state, 0)

    def check_redirects(self, simple: Simple, state: State) -> None:
        for op, target in simple.redirects:
            if op in _OUTPUT_REDIRECTS and target == SYSRQ:
                raise Blocked("power", f"writing to {SYSRQ} reboots or powers off the machine at once: an "
                              "owner action (ADR-0211). Ask the owner")
            if op in _OUTPUT_REDIRECTS and self.names_stop_check(target, state):
                raise Blocked("skip-hooks", f"writing into {STOP_CHECK}: {GREEN_RULE}")
            if op in _OUTPUT_REDIRECTS and _is_device_path(target):
                raise Blocked("disk-wipe", f"writing to {target} overwrites a disk: an owner action "
                              "(ADR-0214). Ask the owner; storage code is tested against the fake host "
                              "of supervisor/tests")
            secret = self.names_secret(target, state)
            if secret == DEV_ENV:
                if op in _OUTPUT_REDIRECTS:
                    if self.exists(target, state):
                        raise Blocked("dev-env", "writing into dev.env replaces the owner's secrets "
                                      "(ADR-0212). Ask the owner to change it")
                else:
                    raise Blocked("dev-env", "reading dev.env into a command shows its secrets "
                                  "(ADR-0212). The scripts in scripts/dev read it themselves")
            elif secret == BACKUP and (op not in _OUTPUT_REDIRECTS or self.exists(target, state)):
                what = "writing into" if op in _OUTPUT_REDIRECTS else "reading"
                raise Blocked("dev-env", f"{what} {target}: {BACKUP_RULE}")

    def check_assignment(self, name: str, value: str) -> None:
        if _is_secret_name(name) and _is_literal(value):
            raise Blocked("secret-argv", f"{name}=... puts a secret on the command line, in the "
                          "transcript (ADR-0212). Keep it in dev.env, which the scripts in scripts/dev "
                          "read, or ask the owner")

    # commands and wrappers

    def check_command(self, words: list, env: dict, simple: Simple, state: State, hops: int) -> None:
        if hops > 24 or not words:
            return
        if hops and _ASSIGNMENT.match(words[0]):
            # Assignments that a wrapper passes on to the command: sudo BASE_URL=... npx ...
            env = dict(env)
            while words and _ASSIGNMENT.match(words[0]):
                name, value = _ASSIGNMENT.match(words[0]).groups()
                self.check_assignment(name, value)
                env[name] = value
                words = words[1:]
            if not words:
                return
        variable = _VARIABLE.match(words[0])
        if variable and variable.group(1) in env:
            expanded = _split_words(env[variable.group(1)])
            self.check_command(expanded + words[1:], env, simple, state, hops + 1)
            return
        path = words[0]
        name = os.path.basename(path).lower()
        args = words[1:]
        if name in ("export", "declare", "typeset", "local", "readonly"):
            for arg in args:
                match = _ASSIGNMENT.match(arg)
                if match:
                    self.check_assignment(*match.groups())
                    state.env[match.group(1)] = match.group(2)
            return
        if name == "unset":
            for arg in args:
                state.env.pop(arg, None)
            return
        if name in ("cd", "pushd"):
            if state.ctx == LOCAL:
                target = next((arg for arg in args if not arg.startswith("-") or arg == "-"), "~")
                state.cwd = _joined(state.cwd, target) or self.project_dir
            return
        inner = self.unwrap(name, path, args, env, simple, state)
        if inner is not None:
            inner_words, inner_env = inner
            self.check_command(inner_words, inner_env, simple, state, hops + 1)
            return
        self.check_rules(name, path, args, env, simple, state)

    def unwrap(self, name: str, path: str, args: list, env: dict, simple: Simple, state: State):
        """The command a wrapper runs, as (words, env); None when `name` is not a wrapper (or
        runs a script string, which is checked here)."""
        if name in ("sudo", "doas"):
            values = {"-u", "-g", "-h", "-p", "-C", "-U", "-r", "-t", "-D", "-R", "-T", "--user",
                      "--group", "--host", "--prompt", "--close-from", "--other-user", "--role",
                      "--type", "--chdir", "--chroot", "--command-timeout"}
            if any(arg in ("-e", "--edit") for arg in args[:_after_options(args, values)]):
                return None
            return args[_after_options(args, values):], env
        if name == "env":
            env = dict(env)
            i = 0
            while i < len(args):
                arg = args[i]
                if arg in ("-u", "--unset", "-C", "--chdir", "-P"):
                    i += 2
                elif arg in ("-S", "--split-string") and i + 1 < len(args):
                    return _split_words(args[i + 1]) + args[i + 2:], env
                elif arg.startswith("--split-string="):
                    return _split_words(arg.split("=", 1)[1]) + args[i + 1:], env
                elif arg == "--":
                    i += 1
                    break
                elif arg.startswith("-") and len(arg) > 1:
                    i += 1
                elif arg == "-":
                    i += 1
                else:
                    break
            while i < len(args) and _ASSIGNMENT.match(args[i]):
                key, value = _ASSIGNMENT.match(args[i]).groups()
                self.check_assignment(key, value)
                env[key] = value
                i += 1
            return args[i:], env
        wrapper_values = {
            "nice": {"-n", "--adjustment"}, "nohup": set(), "setsid": set(), "command": set(),
            "builtin": set(), "exec": {"-a"}, "unbuffer": set(), "caffeinate": {"-t", "-w"},
            "stdbuf": {"-i", "-o", "-e"}, "ionice": {"-c", "-n", "--class", "--classdata"},
            "time": {"-o", "--output", "-f", "--format"}, "gtime": {"-o", "--output", "-f", "--format"},
            "chronic": set(), "catchsegv": set(),
        }
        if name in wrapper_values:
            options = args[:_after_options(args, wrapper_values[name])]
            if name == "command" and any(arg in ("-v", "-V") for arg in options):
                return None
            if name == "ionice" and any(arg in ("-p", "--pid", "-P", "--pgid", "-u", "--uid") for arg in options):
                return None
            return args[_after_options(args, wrapper_values[name]):], env
        if name in ("timeout", "gtimeout"):
            i = _after_options(args, {"-s", "--signal", "-k", "--kill-after"})
            return args[i + 1:], env  # after the duration
        if name == "taskset":
            if any(arg in ("-c", "--cpu-list") for arg in args):
                return args[_after_options(args, {"-c", "--cpu-list"}):], env
            return args[_after_options(args) + 1:], env  # after the mask
        if name == "chrt":
            i = _after_options(args, {"-T", "-P", "-D", "--sched-runtime", "--sched-period",
                                      "--sched-deadline"})
            return args[i + 1:], env  # after the priority
        if name == "sshpass":
            self.check_secret_args(name, args)
            return args[_after_options(args, {"-f", "-d", "-P", "-p"}):], env
        if name == "nsenter":
            return args[_after_options(args, {"-t", "--target", "-S", "--setuid", "-G", "--setgid"}):], env
        if name == "chroot":
            i = _after_options(args)
            return args[i + 1:], env
        if name in ("flock", "lockf"):
            values = {"-w", "--timeout", "-E", "--conflict-exit-code", "-t"}
            i = _after_options(args, values)
            rest = args[i + 1:]  # after the lock file
            if rest[:1] in (["-c"], ["--command"]) and len(rest) > 1:
                self.check_script(rest[1], self.nested_state(state, env))
                return None
            return rest, env
        if name == "xargs":
            values = {"-n", "-L", "-I", "-P", "-s", "-d", "-E", "-a", "--max-args", "--max-lines",
                      "--max-procs", "--max-chars", "--delimiter", "--eof", "--arg-file", "--replace"}
            return args[_after_options(args, values):], env
        if name == "watch":
            values = {"-n", "--interval", "-d", "-q", "--equexit"}
            rest = args[_after_options(args, values):]
            if "-x" in args or "--exec" in args:
                return rest, env
            self.check_script(" ".join(rest), self.nested_state(state, env))
            return None
        if name == "busybox" and args:
            return args, env
        if name == "eval":
            self.check_script(" ".join(args), self.nested_state(state, env))
            return None
        if name in ("su", "runuser"):
            for i, arg in enumerate(args):
                if arg in ("-c", "--command") and i + 1 < len(args):
                    self.check_script(args[i + 1], self.nested_state(state, env))
                    return None
                if arg.startswith("--command="):
                    self.check_script(arg.split("=", 1)[1], self.nested_state(state, env))
                    return None
            if name == "runuser" and "-u" in args:
                i = args.index("-u") + 2
                if i < len(args) and args[i] == "--":
                    i += 1
                return args[i:], env
            return None
        if name in _SHELLS:
            return self.unwrap_shell(args, env, simple, state)
        if _INTERPRETERS.match(name) and not name.startswith(("osascript",)):
            i = _after_options(args, {"-W", "-X", "--check-hash-based-pycs"})
            options = args[:i]
            if i < len(args) and args[i] != "-" and not any(opt in ("-c", "-m", "-e", "-E", "--eval", "-p",
                                                                 "--print") for opt in options):
                script = args[i]
                if os.path.basename(script) in ("api.py", "ssh.sh", "dev.sh", "sd.sh"):
                    return args[i:], env
            return None
        if name in ("ssh", "ssh.sh", "dev.sh"):
            if name == "ssh":
                remote = self.ssh_remote(args)
            elif args[:1] in (["-h"], ["--help"]):
                remote = None
            else:
                remote = args
            if remote is not None:
                self.check_remote(" ".join(remote), simple, state)
            return None
        if name == "sd.sh" and "--" in args:
            return args[args.index("--") + 1:], env
        if name in ("docker", "podman"):
            host, context, rest = self.docker_globals(args, env)
            if rest[:1] == ["exec"]:
                values = {"-e", "--env", "--env-file", "-u", "--user", "-w", "--workdir", "--detach-keys"}
                options = rest[1:]
                self.check_docker_env(options)
                i = _after_options(options, values)
                return options[i + 1:], env  # after the container
            return None
        return None

    def unwrap_shell(self, args: list, env: dict, simple: Simple, state: State):
        i, command_mode = 0, False
        while i < len(args):
            arg = args[i]
            if arg == "--":
                i += 1
                break
            if arg in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                i += 2
                continue
            if arg.startswith("--"):
                i += 1
                continue
            if arg[:1] in ("-", "+") and len(arg) > 1:
                if arg[0] == "-" and "c" in arg[1:]:
                    command_mode = True
                i += 1
                continue
            break
        rest = args[i:]
        if command_mode:
            if rest:
                self.check_script(rest[0], self.nested_state(state, env))
            return None
        if rest:
            if os.path.basename(rest[0]) in ("ssh.sh", "dev.sh", "sd.sh", "api.py"):
                return rest, env
            return None
        # The shell reads its script from standard input.
        for body in simple.stdin or state.stdin:
            self.check_script(body, self.nested_state(state, env))
        return None

    def nested_state(self, state: State, env: dict) -> State:
        return State(state.ctx, state.cwd, env, state.depth + 1, state.stdin)

    def ssh_remote(self, args: list):
        """The remote command words of an ssh command line, [] for none, None without a host."""
        value_letters = set("BbcDEeFIiJLlmOopQRSWw")
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--":
                i += 1
                break
            if arg.startswith("-") and len(arg) > 1:
                letters = arg[1:]
                for j, letter in enumerate(letters):
                    if letter in value_letters:
                        if j == len(letters) - 1:
                            i += 1
                        break
                i += 1
                continue
            break
        if i >= len(args):
            return None
        return args[i + 1:]

    def check_remote(self, remote: str, simple: Simple, state: State) -> None:
        stdin = list(simple.stdin)
        for op, target in simple.redirects:
            if op == "<" and state.ctx == LOCAL:
                path = _joined(state.cwd, target)
                try:
                    if path and os.path.isfile(path) and os.path.getsize(path) <= MAX_STDIN_FILE:
                        with open(path, encoding="utf-8", errors="replace") as handle:
                            stdin.append(handle.read())
                except OSError:
                    pass
        device = State(DEVICE, None, {}, state.depth + 1, stdin)
        if remote.strip():
            self.check_script(remote, device)
        else:
            for body in stdin:  # the login shell reads its script from standard input
                self.check_script(body, State(DEVICE, None, {}, state.depth + 1))

    # rules

    def check_rules(self, name: str, path: str, args: list, env: dict, simple: Simple, state: State) -> None:
        self.check_disks(name, args)
        self.check_power(name, args)
        if name in ("docker", "podman", "docker-compose"):
            self.check_docker(name, args, env, state)
        if state.ctx == DEVICE:
            self.check_packages(name, args)
        if name == "git":
            self.check_git(args, env, state)
        if state.ctx == LOCAL:
            self.check_green_record(name, args, simple, state)
        if name == "gh":
            positionals = [arg for arg in args if not arg.startswith("-")]
            if positionals[:2] == ["pr", "merge"]:
                raise Blocked("push-main", "gh pr merge merges into the owner's branch: the owner "
                              "merges pull requests (ADR-0208). Ask the owner")
        if self.is_playwright_run(name, args):
            self.check_playwright(name, args, env, state)
        if name == "api.py":
            # scripts/dev/api.py, also as ./api.py after cd scripts/dev
            real = (_joined(state.cwd, path) if state.ctx == LOCAL else None) or path
            if os.path.basename(os.path.dirname(real)) == "dev":
                self.check_api(args)
        self.check_secret_args(name, args)
        self.check_dev_env(name, args, simple, state)
        if name in ("make", "gmake"):
            for arg in args:
                match = _ASSIGNMENT.match(arg)
                if match:
                    self.check_assignment(*match.groups())

    def check_disks(self, name: str, args: list) -> None:
        def block(what: str) -> None:
            raise Blocked("disk-wipe", f"{what} erases or repartitions a disk: an owner action "
                          "(ADR-0214). Ask the owner; storage code is tested against the fake host of "
                          "supervisor/tests")

        only_help = all(arg in ("-h", "--help", "-V", "--version") for arg in args)
        if re.fullmatch(r"mkfs(\..+)?|mke2fs|mkswap|mkdosfs|mkntfs|mkexfatfs|newfs(_.+)?", name):
            if not only_help:
                block(name)
        elif name == "wipefs":
            letters = "".join(_cluster(arg) for arg in args)
            erases = "a" in letters or "o" in letters or any(arg.startswith(("--all", "--offset"))
                                                             for arg in args)
            if erases and "n" not in letters and "--no-act" not in args:
                block("wipefs")
        elif name in ("dd", "dcfldd"):
            for arg in args:
                if arg.startswith("of=") and _is_device_path(arg[3:]):
                    block(f"{name} {arg}")
        elif name == "blkdiscard":
            if not only_help:
                block(name)
        elif name == "sgdisk":
            read_only = {"-p", "--print", "-v", "--verify", "-i", "--info", "-O", "--print-mbr", "-L",
                         "--list-types", "-?", "--help", "--usage", "-V", "--version", "-P", "--pretend"}
            options = [arg.split("=", 1)[0] for arg in args if arg.startswith("-")]
            if "-P" not in options and "--pretend" not in options and any(opt not in read_only
                                                                          for opt in options):
                block("sgdisk")
        elif name == "sfdisk":
            read_only = {"-l", "--list", "-d", "--dump", "-J", "--json", "-F", "--list-free", "-s",
                         "--show-size", "-g", "--show-geometry", "-V", "--verify", "-h", "--help", "-v",
                         "--version", "-T", "--list-types", "-n", "--no-act"}
            if not any(arg in read_only for arg in args):
                block("sfdisk")
        elif name in ("fdisk", "cfdisk", "gdisk", "cgdisk"):
            if not any(arg in ("-l", "--list", "-x", "--list-details", "-h", "--help", "-V",
                               "--version") for arg in args):
                block(name)
        elif name == "parted":
            destructive = {"mklabel", "mktable", "mkpart", "mkpartfs", "rm", "resize", "resizepart",
                           "rescue", "move", "cp", "mkfs", "name", "set", "toggle", "disk_set",
                           "disk_toggle", "type"}
            if any(arg.lower() in destructive for arg in args):
                block("parted " + next(arg for arg in args if arg.lower() in destructive))
            listing = any(arg in ("-l", "--list", "-h", "--help", "-v", "--version") for arg in args)
            commands = [arg for arg in _positionals(args, {"-a", "--align"})[1:]]
            if not listing and not commands:
                block("parted (interactive)")
        elif name == "shred":
            for arg in args:
                if _is_device_path(arg):
                    block(f"shred {arg}")
        elif name == "cryptsetup":
            actions = [arg for arg in _positionals(args, {"--type", "-c", "--cipher", "-s", "--key-size",
                                                          "-h", "--hash", "-d", "--key-file", "-S",
                                                          "--key-slot", "-i", "--iter-time", "--header"})
                       if arg.lower() in ("luksformat", "lukserase", "erase")]
            if actions:
                block("cryptsetup " + actions[0])
        elif name == "btrfs":
            words = [arg for arg in args if not arg.startswith("-")]
            if len(words) >= 2 and "device".startswith(words[0]) and words[0]:
                if (len(words[1]) >= 3 and "remove".startswith(words[1])) or "delete".startswith(words[1]):
                    block("btrfs device " + words[1])
            if len(words) >= 2 and len(words[0]) >= 3 and "replace".startswith(words[0]) \
                    and "start".startswith(words[1]):
                block("btrfs replace start")
        elif name == "mdadm":
            letters = "".join(_cluster(arg) for arg in args)
            if any(arg in ("--create", "--zero-superblock", "--build") for arg in args) or "C" in letters \
                    or "B" in letters:
                block("mdadm --create or --zero-superblock")
        elif name == "nvme":
            if args[:1] and args[0] in ("format", "sanitize"):
                block("nvme " + args[0])
        elif name == "hdparm":
            for arg in args:
                if arg.startswith(("--security-erase", "--trim-sector-ranges", "--write-sector",
                                   "--fwdownload", "--make-bad-sector")):
                    block("hdparm " + arg)
        elif name == "diskutil":
            verbs = [arg for arg in args if not arg.startswith("-")][:2]
            if verbs[:1] and verbs[0].lower() == "apfs":
                verbs = verbs[1:]
            destructive = {"erasedisk", "erasevolume", "reformat", "zerodisk", "randomdisk",
                           "secureerase", "partitiondisk", "splitpartition", "mergepartitions",
                           "deletecontainer", "deletevolume", "resizevolume", "erase"}
            if verbs[:1] and verbs[0].lower() in destructive:
                block("diskutil " + verbs[0])
        elif name in ("tee", "cp", "ddrescue", "install"):
            files = _positionals(args, {"-t", "--target-directory", "-m", "--mode", "-o", "-g", "-a",
                                        "--append"} if name != "tee" else set())
            targets = files if name == "tee" else (files[1:2] if name == "ddrescue" else files[-1:])
            for arg in targets:
                if _is_device_path(arg):
                    block(f"{name} to {arg}")

    def check_power(self, name: str, args: list) -> None:
        def block(what: str) -> None:
            raise Blocked("power", f"{what} is an owner action (ADR-0211). Ask the owner")

        if name in ("reboot", "poweroff", "halt"):
            if not args or not all(arg in ("--help", "--version") for arg in args):
                block(name)
        elif name == "shutdown":
            if not any(arg in ("-c", "--cancel", "--help", "--show") for arg in args):
                block("shutdown")
        elif name in ("systemctl", "loginctl"):
            verbs = [arg for arg in args if not arg.startswith("-")]
            power = {"reboot", "poweroff", "halt", "kexec", "soft-reboot", "suspend", "hibernate",
                     "hybrid-sleep", "suspend-then-hibernate", "emergency", "rescue"}
            if verbs[:1] and verbs[0] in power:
                block(f"{name} {verbs[0]}")
            if verbs[:1] and verbs[0] in ("isolate", "start") and any(
                    verb.split(".")[0] in power for verb in verbs[1:]):
                block(f"{name} {' '.join(verbs[:2])}")
        elif name in ("init", "telinit"):
            if args[:1] and args[0] in ("0", "6"):
                block(f"{name} {args[0]}")
        elif name == "tee" and SYSRQ in args:
            block(f"tee {SYSRQ}")

    def docker_globals(self, args: list, env: dict) -> tuple:
        host, context = env.get("DOCKER_HOST"), env.get("DOCKER_CONTEXT")
        values = {"--config", "-l", "--log-level", "--tlscacert", "--tlscert", "--tlskey"}
        i = 0
        while i < len(args) and args[i].startswith("-"):
            arg = args[i]
            if arg in ("-H", "--host", "-c", "--context"):
                value = args[i + 1] if i + 1 < len(args) else ""
                if arg in ("-H", "--host"):
                    host = value
                else:
                    context = value
                i += 2
                continue
            if arg.startswith(("--host=", "--context=")):
                key, value = arg.split("=", 1)
                host, context = (value, context) if key == "--host" else (host, value)
            elif arg.startswith("-H") and len(arg) > 2:
                host = arg[2:]
            elif arg in values:
                i += 1
            i += 1
        return host, context, args[i:]

    def check_docker_env(self, options: list) -> None:
        for i, arg in enumerate(options):
            value = None
            if arg in ("-e", "--env", "--build-arg") and i + 1 < len(options):
                value = options[i + 1]
            elif arg.startswith(("--env=", "--build-arg=")):
                value = arg.split("=", 1)[1]
            elif arg.startswith("-e") and len(arg) > 2 and "=" in arg:
                value = arg[2:]
            match = _ASSIGNMENT.match(value or "")
            if match:
                self.check_assignment(*match.groups())

    def check_docker(self, name: str, args: list, env: dict, state: State) -> None:
        if name == "docker-compose":
            host, context, rest = env.get("DOCKER_HOST"), env.get("DOCKER_CONTEXT"), ["compose"] + args
        else:
            host, context, rest = self.docker_globals(args, env)
        self.check_docker_env(rest)
        remote_host = bool(host) and not host.startswith(("unix://", "npipe://", "fd://"))
        remote_context = bool(context) and context not in ("default", "desktop-linux", "colima",
                                                           "orbstack", "rancher-desktop")
        if state.ctx != DEVICE and not remote_host and not remote_context:
            return
        what = self.docker_change(rest)
        if what:
            raise Blocked("device-docker", f"{what} on the dev device changes its containers or images "
                          "and writes its system disk (ADR-0214). Deploys go through scripts/dev/deploy.sh when "
                          "the owner asks; anything else is an owner action (ADR-0211)")

    @staticmethod
    def docker_change(rest: list):
        sub = rest[0] if rest else ""
        following = rest[1] if len(rest) > 1 else ""
        if sub in ("build", "pull", "run", "create", "load", "import"):
            return f"docker {sub}"
        if sub in ("image", "container", "system", "volume", "network", "builder", "buildx"):
            changes = {"image": ("build", "pull", "prune", "load", "import"),
                       "container": ("run", "create", "prune"),
                       "system": ("prune",), "volume": ("prune",), "network": ("prune",),
                       "builder": ("build", "prune"), "buildx": ("build", "b", "bake", "prune")}
            if following in changes[sub]:
                return f"docker {sub} {following}"
        if sub == "compose":
            options = rest[1:]
            values = {"-f", "--file", "-p", "--project-name", "--profile", "--env-file",
                      "--project-directory", "--ansi", "--progress", "--parallel"}
            if "--dry-run" in options:
                return None
            positionals = _positionals(options, values)
            if positionals[:1] and positionals[0] in ("build", "pull", "run", "create", "up"):
                return f"docker compose {positionals[0]}"
        return None

    def check_packages(self, name: str, args: list) -> None:
        def block(what: str) -> None:
            raise Blocked("device-packages", f"{what} on the dev device changes its system and writes "
                          "its system disk: an owner action (ADR-0211, ADR-0214). Ask the owner")

        if name in ("apt", "apt-get", "aptitude"):
            dry = {"-s", "--simulate", "--just-print", "--dry-run", "--recon", "--no-act"}
            actions = _positionals(args, {"-o", "--option", "-c", "--config-file", "-t",
                                          "--target-release", "--default-release", "-a",
                                          "--host-architecture"})
            changes = {"install", "reinstall", "upgrade", "dist-upgrade", "full-upgrade", "remove",
                       "purge", "autoremove", "autopurge", "build-dep", "update", "safe-upgrade",
                       "satisfy", "dselect-upgrade"}
            if actions[:1] and actions[0] in changes and not any(arg in dry for arg in args):
                block(f"{name} {actions[0]}")
        elif name == "dpkg":
            letters = "".join(_cluster(arg) for arg in args)
            if any(arg in ("--install", "--remove", "--purge", "--unpack", "--configure") for arg in args) \
                    or set(letters) & set("irP"):
                block("dpkg --install or --remove")
        elif name in ("apk",):
            if args[:1] and args[0] in ("add", "del", "upgrade", "update", "fix"):
                block(f"apk {args[0]}")
        elif name in ("npm", "pnpm", "yarn", "bun"):
            rest = args[_after_options(args, {"--prefix", "-C", "--dir", "--cwd", "-w", "--workspace"}):]
            installs = {"install", "i", "in", "ins", "inst", "insta", "instal", "isnt", "isnta", "isntal",
                        "isntall", "ci", "clean-install", "cit", "install-clean", "install-test", "it", "add",
                        "update", "up", "upgrade", "udpate"}
            if (rest[:1] and rest[0] in installs) or (name == "yarn" and not rest):
                block(f"{name} {rest[0] if rest else 'install'}")
        elif re.fullmatch(r"pip[0-9.]*|pipx", name) or (name == "uv" and args[:1] == ["pip"]) or (
                re.fullmatch(r"python[0-9.]*", name) and "-m" in args
                and args[args.index("-m") + 1:args.index("-m") + 2] in (["pip"], ["pipx"])):
            if name == "uv":
                rest = args[1:]
            elif name.startswith("python"):
                rest = args[args.index("-m") + 2:]
            else:
                rest = args
            actions = _positionals(rest, {"--proxy", "--cache-dir", "--log", "--python", "--timeout",
                                          "--retries", "--exists-action", "--cert", "--client-cert"})
            if actions[:1] and actions[0] in ("install", "sync", "inject"):
                block(f"pip {actions[0]}")

    def check_git(self, args: list, env: dict, state: State) -> None:
        cwd, configs, i = state.cwd, [], 0
        while i < len(args) and args[i].startswith("-"):
            arg = args[i]
            if arg == "-C" and i + 1 < len(args):
                cwd = _joined(cwd, args[i + 1]) or cwd
                i += 2
            elif arg == "-c" and i + 1 < len(args):
                configs.append(args[i + 1])
                i += 2
            elif arg in ("--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env",
                         "--super-prefix", "--list-cmds"):
                i += 2
            else:
                i += 1
        configs += [f"{env[key]}=" for key in env if key.startswith("GIT_CONFIG_KEY_")]
        configs += [env.get("GIT_CONFIG_PARAMETERS", "").strip("'")]
        for item in configs:
            key, _, value = item.partition("=")
            if key.strip().lower() == "core.hookspath" and not self.is_repo_hooks(value, cwd):
                raise Blocked("skip-hooks", "another core.hooksPath skips the git hooks of "
                              "scripts/git-hooks, the commit checks (ADR-0209, ADR-0216). Fix what the "
                              "hook reports, then commit again")
        if i >= len(args):
            return
        sub, rest = args[i], args[i + 1:]
        if sub == "push":
            self.check_push(rest, cwd, state, configs)
        elif sub == "commit":
            self.check_commit(rest)
        elif sub == "config":
            self.check_git_config(rest, cwd)
        elif any(arg == "--no-verify" or (len(arg) >= 7 and "--no-verify".startswith(arg)) for arg in rest):
            self.block_no_verify(f"git {sub} --no-verify")

    @staticmethod
    def names_stop_check(path: str, state: State) -> bool:
        """Whether `path` is the folder of the green run or lies in it, here or in another checkout."""
        full = (_joined(state.cwd, path) if state.ctx == LOCAL else None) or path
        for candidate in (path, full):
            normal = os.path.normpath(candidate) if candidate else ""
            if (normal == STOP_CHECK or normal.startswith(STOP_CHECK + "/") or normal.endswith("/" + STOP_CHECK)
                    or f"/{STOP_CHECK}/" in normal):
                return True
        return False

    def check_green_record(self, name: str, args: list, simple: Simple, state: State) -> None:
        """A green run recorded by hand (rule skip-hooks): see the module's docstring."""
        def block(what: str) -> None:
            raise Blocked("skip-hooks", f"{what}: {GREEN_RULE}")

        reads = name in _GREEN_READS and not (
            (name == "sed" and any(arg.startswith(("-i", "--in-place")) for arg in args))
            or (name == "find" and any(arg in ("-exec", "-execdir", "-ok", "-okdir") for arg in args)))
        named = [name, *args] + [target for _, target in simple.redirects]
        if not reads and "record" in args and any("green-run" in word for word in named):
            block(f"{name} with green-run and record")
        if name in _GREEN_COPIERS and any("green-run" in os.path.basename(arg) for arg in args):
            block(f"a copy of green-run.py by {name}")
        if _INTERPRETERS.match(name):
            code = [args[i + 1] for i in range(len(args) - 1) if args[i] in ("-c", "-e", "-E", "--eval", "-p", "-r")]
            if any(STOP_CHECK in text or "stop-check" in text or ("green-run" in text and "record" in text)
                   for text in code + list(simple.stdin)):
                block(f"{name} code that names stop-check or records a green run")
            if "record" in args and "-" in args[:args.index("record")]:
                block(f"{name} reading a program on standard input given record")
        if name in _GREEN_COPIERS:
            operands = [arg for arg in args if not arg.startswith("-")]
            targets = operands[-1:] + [args[i + 1] for i in range(len(args) - 1) if args[i] == "-t"]
            targets += [arg.split("=", 1)[1] for arg in args if arg.startswith("--target-directory=")]
        elif name == "dd":
            targets = [arg[3:] for arg in args if arg.startswith("of=")]
        elif reads:
            targets = []
        else:
            targets = list(args) + [arg.split("=", 1)[1] for arg in args if "=" in arg]
        if any(self.names_stop_check(path, state) for path in targets):
            block(f"{name} writing into {STOP_CHECK}")

    @staticmethod
    def block_no_verify(what: str) -> None:
        raise Blocked("skip-hooks", f"{what} skips the git hooks, the commit checks (ADR-0209, "
                      "ADR-0216). Fix what the hook reports, then commit again")

    def check_commit(self, rest: list) -> None:
        values = {"-m", "--message", "-F", "--file", "-C", "--reuse-message", "-c", "--reedit-message",
                  "--fixup", "--squash", "-t", "--template", "--author", "--date", "--cleanup",
                  "--trailer", "--pathspec-from-file"}
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg == "--":
                break
            if arg.startswith("--"):
                if "=" not in arg and arg in values:
                    i += 1
                elif "=" not in arg and len(arg) >= 7 and "--no-verify".startswith(arg):
                    self.block_no_verify("git commit --no-verify")
            elif arg.startswith("-") and len(arg) > 1:
                letters = arg[1:]
                for j, letter in enumerate(letters):
                    if letter == "n":
                        self.block_no_verify("git commit -n")
                    if letter in "mFCct":
                        if j == len(letters) - 1:
                            i += 1
                        break
                    if letter in "uS":
                        break
            i += 1

    def is_repo_hooks(self, value: str, cwd) -> bool:
        """Whether a core.hooksPath value names this repository's scripts/git-hooks."""
        value = value.strip().rstrip("/")
        if value == "scripts/git-hooks":
            return True
        # The same directory as an absolute path: $PWD/scripts/git-hooks or /path/to/repo/scripts/git-hooks.
        path = _joined(cwd, value) if value.startswith(("/", "$PWD/", "${PWD}/")) else None
        roots = {os.path.normpath(root) for root in (cwd, self.project_dir) if root}
        return bool(path) and any(path == os.path.join(root, "scripts", "git-hooks") for root in roots)

    def check_git_config(self, rest: list, cwd=None) -> None:
        positionals = _positionals(rest, {"-f", "--file", "--blob", "--type", "--default", "--comment"})
        if positionals[:1] and positionals[0] in ("set", "unset", "get", "list", "edit", "rename-section",
                                                  "remove-section"):
            verb, positionals = positionals[0], positionals[1:]
        else:
            verb = "unset" if any(arg in ("--unset", "--unset-all") for arg in rest) else (
                "get" if any(arg.startswith("--get") or arg in ("-l", "--list") for arg in rest) else "set")
        if not positionals or positionals[0].lower() != "core.hookspath":
            return
        if verb == "unset" or (verb == "set" and len(positionals) > 1
                               and not self.is_repo_hooks(positionals[1], cwd)):
            raise Blocked("skip-hooks", "changing core.hooksPath skips the git hooks of scripts/git-hooks "
                          "(ADR-0209, ADR-0216). make setup sets it to scripts/git-hooks")

    def check_push(self, rest: list, cwd, state: State, configs: list = ()) -> None:
        if state.ctx == DEVICE:
            raise Blocked("device-push", "git push on the dev device: pushes come from the workstation, "
                          "never from the device (ADR-0208)")
        force = delete = everything = no_verify = dry = tags = False
        positionals, i = [], 0
        while i < len(rest):
            arg = rest[i]
            if arg == "--":
                positionals.extend(rest[i + 1:])
                break
            if arg.startswith("--"):
                key = arg.split("=", 1)[0]
                if key in ("--force", "--force-with-lease", "--force-if-includes"):
                    force = True
                elif key == "--mirror":
                    force = delete = everything = True
                elif key in ("--delete", "--prune"):
                    delete = True
                elif key in ("--all", "--branches"):
                    everything = True
                elif key == "--dry-run":
                    dry = True
                elif key == "--tags":
                    tags = True
                elif len(key) >= 7 and "--no-verify".startswith(key):
                    no_verify = True
                elif key in ("--repo", "--receive-pack", "--exec", "--push-option") and "=" not in arg:
                    i += 1
            elif arg.startswith("-") and len(arg) > 1:
                letters = arg[1:]
                for j, letter in enumerate(letters):
                    if letter == "f":
                        force = True
                    elif letter == "d":
                        delete = True
                    elif letter == "n":
                        dry = True
                    elif letter == "o":
                        if j == len(letters) - 1:
                            i += 1
                        break
            else:
                positionals.append(arg)
            i += 1
        if dry:
            return  # a dry run pushes nothing
        destinations, specs, k = [], positionals[1:], 0
        while k < len(specs):
            spec = specs[k]
            if spec == "tag":
                k += 2
                continue
            if spec.startswith("+"):
                force, spec = True, spec[1:]
            source, _, destination = spec.partition(":")
            if ":" in spec and not source:
                delete = True
            destination = destination or source
            if destination in ("HEAD", "@"):
                destination = self.current_branch(cwd) or destination
            destinations.append(re.sub(r"^(refs/)?heads/", "", destination))
            k += 1
        if not specs and not everything and not tags:
            destinations.extend(self.push_targets(cwd, configs))
        if everything or any(destination in ("main", "master") for destination in destinations):
            raise Blocked("push-main", "pushing to main or master: those branches are the owner's "
                          "(ADR-0208). Push your own branch (git push origin <branch>); the owner merges "
                          "pull requests")
        if force:
            raise Blocked("force-push", "a force push (--force, --force-with-lease, +refspec, --mirror) "
                          "rewrites pushed history (ADR-0208). Add a new commit instead, or ask the owner")
        if delete:
            raise Blocked("delete-branch", "deleting a remote branch (--delete, :branch, --prune) is the "
                          "owner's decision (ADR-0208). Ask the owner")
        if no_verify:
            self.block_no_verify("git push --no-verify")

    @staticmethod
    def current_branch(cwd):
        return (_git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD") or "").strip() or None

    def push_targets(self, cwd, configs=()) -> list:
        """The branches that a git push without a refspec updates in cwd, as git's configuration
        decides: the refspecs of remote.<name>.push; every branch that exists on both sides
        (main too) for push.default matching; else the branch that @{push} names, which for
        push.default upstream is the upstream (a branch made from origin/main without --no-track
        tracks main); else, when @{push} cannot be resolved, the upstream for push.default
        upstream or tracking, and the current branch. git -c settings of the command count."""
        given = []  # the push settings of git -c, which the git commands below get too
        for item in configs:
            key = item.partition("=")[0].strip().lower()
            if key in ("push.default", "remote.pushdefault") or re.fullmatch(
                    r"(remote|branch)\..+\.(push|pushremote|merge|remote)", key):
                given += ["-c", item]
        targets = []
        listed = _git(cwd, *given, "config", "--get-regexp", r"^remote\..*\.push$") or ""
        for line in listed.splitlines():
            spec = line.partition(" ")[2].strip().lstrip("+")
            source, colon, destination = spec.partition(":")
            if colon and not source and not destination:
                return ["main"]  # ":" pushes every branch that exists on both sides
            destination = re.sub(r"^(refs/)?heads/", "", destination or source)
            if destination in ("HEAD", "@"):
                destination = self.current_branch(cwd) or destination
            if fnmatch.fnmatchcase("main", destination) or fnmatch.fnmatchcase("master", destination):
                return ["main"]  # also a wildcard refspec such as refs/heads/*:refs/heads/*
            targets.append(destination)
        default = (_git(cwd, *given, "config", "--get", "push.default") or "").strip().lower()
        if default == "matching":
            return ["main"]
        branch = self.current_branch(cwd)
        push = (_git(cwd, *given, "rev-parse", "--symbolic-full-name", "@{push}") or "").strip()
        if push.startswith("refs/heads/"):  # the remote "." (this repository)
            return targets + [push[len("refs/heads/"):]]
        if push.startswith("refs/remotes/"):
            rest = push[len("refs/remotes/"):]
            remotes = sorted((_git(cwd, "remote") or "").split(), key=len, reverse=True)
            remote = next((name for name in remotes if rest.startswith(name + "/")), rest.split("/", 1)[0])
            return targets + [rest[len(remote) + 1:]]
        if branch and default in ("upstream", "tracking"):
            merge = (_git(cwd, *given, "config", "--get", f"branch.{branch}.merge") or "").strip()
            if merge:
                return targets + [re.sub(r"^refs/heads/", "", merge)]
        return targets + [branch]

    @staticmethod
    def is_playwright_run(name: str, args: list) -> bool:
        def runs_tests(rest: list) -> bool:
            i = _after_options(rest, {"-p", "--package", "-c", "--call", "-w", "--workspace"})
            return rest[i:i + 1] in (["playwright"], ["@playwright/test"]) and rest[i + 1:i + 2] == ["test"]

        if name == "playwright":
            return args[:1] == ["test"]
        if name in ("npx", "bunx", "pnpx"):
            return runs_tests(args)
        if name in ("npm", "pnpm", "yarn", "bun"):
            rest = args[_after_options(args, {"--prefix", "-C", "--dir", "-w", "--workspace", "--cwd"}):]
            if not rest:
                return False
            if rest[0] in ("exec", "x", "dlx"):
                return runs_tests(rest[1:])
            if rest[0] in ("test", "t", "tst") or (name != "npm" and rest[0].startswith("test")):
                return True
            if rest[0] in ("run", "run-script", "rum", "urn"):
                return any(arg.startswith("test") for arg in rest[1:2])
            return rest[0] == "playwright" and rest[1:2] == ["test"]
        if name in ("make", "gmake"):
            return "e2e-local" in [arg for arg in args if not arg.startswith("-") and "=" not in arg]
        return name == "e2e.sh"

    def check_playwright(self, name: str, args: list, env: dict, state: State) -> None:
        if state.ctx == DEVICE:
            raise Blocked("e2e-target", "Playwright on the dev device: E2E specs run only on the "
                          "workstation, against the local stand-in (ADR-0215). Run make e2e-local")
        env = dict(env)
        if name in ("make", "gmake"):
            env.update(m.groups() for m in map(_ASSIGNMENT.match, args) if m)
        for key in ("BASE_URL", "HTTP_URL"):
            value = env.get(key, "")
            if value and not _is_local_url(value):
                raise Blocked("e2e-target", f"Playwright with {key}={value}: E2E specs run only against "
                              "the local stand-in, never against the device (ADR-0215). Run make e2e-local "
                              "(scripts/standin/e2e.sh)")

    def check_api(self, args: list) -> None:
        positionals = _api_positionals(args)
        if len(positionals) < 2:
            return
        method = positionals[0].upper()
        path = re.sub(r"^(https?://[^/]+)?/*(api/v1/*)?", "", positionals[1].split("?", 1)[0]).strip("/")
        if method == "POST" and path in ("system/reboot", "system/shutdown", "power/reboot",
                                         "power/shutdown", "power/suspend"):
            raise Blocked("power", f"POST /{path} on the dev device is an owner action (ADR-0211). Ask the "
                          "owner")
        if (method == "POST" and path == "storage/pools") or (
                method == "DELETE" and re.fullmatch(r"storage/pools/[^/]+", path)):
            raise Blocked("disk-wipe", f"{method} /{path} formats disks or destroys a pool on the dev "
                          "device: an owner action (ADR-0214). Ask the owner")
        if method == "POST" and path in ("apps/install", "containers", "containers/images/pull",
                                         "updates/install"):
            raise Blocked("device-docker", f"POST /{path} pulls images or creates containers on the dev "
                          "device and writes its system disk: an owner action (ADR-0211, ADR-0214)")

    def check_secret_args(self, name: str, args: list) -> None:
        def block(what: str) -> None:
            raise Blocked("secret-argv", f"{what} puts a secret on the command line, in the transcript "
                          "and the process list (ADR-0212). Let the scripts in scripts/dev read dev.env, "
                          "or pass the secret on standard input")

        if name == "sshpass":
            for arg in args[:_after_options(args, {"-f", "-d", "-P"})]:
                if arg.startswith("-p") or arg.startswith("--password"):
                    block("sshpass -p")
        if name in ("curl", "wget"):
            curl_values = set("AbcCdDeEFHKmoPQrtTwxXyYz")
            for i, arg in enumerate(args):
                value = None
                if arg in ("--user", "--proxy-user") and i + 1 < len(args):
                    value = args[i + 1]
                elif arg.startswith(("--user=", "--proxy-user=")):
                    value = arg.split("=", 1)[1]
                elif name == "curl" and _cluster(arg):
                    letters = _cluster(arg)
                    for j, letter in enumerate(letters):
                        if letter in "uU":
                            value = letters[j + 1:] or (args[i + 1] if i + 1 < len(args) else "")
                            break
                        if letter in curl_values:
                            break
                if value and ":" in value and value.split(":", 1)[1]:
                    block(f"{name} -u user:password")
            for i, arg in enumerate(args):
                header = None
                if arg in ("-H", "--header") and i + 1 < len(args):
                    header = args[i + 1]
                elif arg.startswith("--header="):
                    header = arg.split("=", 1)[1]
                elif name == "curl" and arg.startswith("-H") and len(arg) > 2:
                    header = arg[2:]
                credentials = re.match(r"\s*(?:proxy-)?authorization\s*:\s*\S+\s+(\S+)", header or "", re.I)
                if credentials and _is_literal(credentials.group(1)):
                    block("an Authorization header with a literal token")
        if name in ("zip", "unzip") and "-P" in args[:-1]:
            block(f"{name} -P <password>")
        if name in ("7z", "7za", "7zz") and any(arg.startswith("-p") and len(arg) > 2 for arg in args):
            block(f"{name} -p<password>")
        if name in ("smbclient", "rpcclient", "smbcacls", "smbcquotas", "smbget", "smbtree", "net"):
            for i, arg in enumerate(args):
                value = None
                if arg in ("-U", "--user") and i + 1 < len(args):
                    value = args[i + 1]
                elif arg.startswith("--user="):
                    value = arg.split("=", 1)[1]
                elif arg.startswith("-U") and len(arg) > 2:
                    value = arg[2:]
                if value and "%" in value and value.split("%", 1)[1]:
                    block(f"{name} -U user%password")
        if name in ("docker", "podman") and "login" in args:
            for i, arg in enumerate(args):
                if arg == "-p" and i + 1 < len(args):
                    block("docker login -p")
        if name.startswith("mount"):
            for i, arg in enumerate(args):
                if arg == "-o" and i + 1 < len(args) and re.search(r"(^|,)(password|pass)=[^,]", args[i + 1]):
                    block("mount -o password=")
        if name == "openssl":
            for i, arg in enumerate(args):
                if arg in ("-pass", "-passin", "-passout") and i + 1 < len(args) \
                        and args[i + 1].startswith("pass:"):
                    block(f"openssl {arg} pass:")
        for i, arg in enumerate(args):
            if arg.startswith("--") and "=" in arg:
                key, value = arg[2:].split("=", 1)
                if value and _is_secret_option(key) and not (
                        key.lower() == "secret" and re.match(r"(id|type|src|source|env)=", value)):
                    block(f"--{key}=...")
            elif arg in ("--password", "--passwd", "--pass") and i + 1 < len(args) \
                    and not args[i + 1].startswith("-"):
                block(f"{arg} <value>")
            if _URL_PASSWORD.search(arg):
                block("a URL with user:password@")

    def names_secret(self, word: str, state: State):
        """DEV_ENV when a word is a path of a file named dev.env (also NAME=path, ref:path and a
        glob that matches an existing dev.env), BACKUP when it is a path in a device backup
        (tmp/dev/backup, on this machine), else None. Text with spaces, such as a message, is not
        a path."""
        if not word or any(ch.isspace() for ch in word):
            return None
        tail = word.rsplit("=", 1)[-1].rsplit(":", 1)[-1]
        if os.path.basename(tail.rstrip("/")) == "dev.env":
            return DEV_ENV
        if state.ctx != LOCAL:
            return None
        path = _joined(state.cwd or self.project_dir, word.rsplit("=", 1)[-1])
        if _BACKUP.search((path or word).replace(os.sep, "/")):
            return BACKUP
        if not getattr(word, "glob", False):
            return None
        directory, pattern = os.path.split(word)
        if not pattern or not fnmatch.fnmatchcase("dev.env", pattern):
            return None
        base = _joined(state.cwd or self.project_dir, directory or ".")
        return DEV_ENV if base and os.path.exists(os.path.join(base, "dev.env")) else None

    def exists(self, word: str, state: State) -> bool:
        """Whether the file that `word` names exists (True when that cannot be told)."""
        if state.ctx != LOCAL:
            return True
        path = _joined(state.cwd or self.project_dir, word)
        return path is None or os.path.lexists(path)

    def check_dev_env(self, name: str, args: list, simple: Simple, state: State) -> None:
        def block(what: str, secret: str = DEV_ENV) -> None:
            if secret == BACKUP:
                raise Blocked("dev-env", f"{what}: {BACKUP_RULE}")
            raise Blocked("dev-env", f"{what}: dev.env holds the owner's secrets, which are never shown, "
                          "copied or replaced (ADR-0212). The scripts in scripts/dev read it; ask the owner "
                          "to change it")

        if _INTERPRETERS.match(name):
            code = [args[i + 1] for i in range(len(args) - 1)
                    if args[i] in ("-c", "-e", "-E", "--eval", "-p", "--print", "-r")]
            if any(_DEV_ENV.search(text) for text in code + simple.stdin):
                block(f"{name} code that reads dev.env")
            if state.ctx == LOCAL and any(_BACKUP_TEXT.search(text) for text in code + simple.stdin):
                block(f"{name} code that reads tmp/dev/backup", BACKUP)
        self.check_recursive_reads(name, args, state)
        self.check_archives(name, args, state)
        secrets = [(arg, self.names_secret(arg, state)) for arg in self.file_arguments(name, args)]
        backups = [arg for arg, secret in secrets if secret == BACKUP]
        if backups and not (
                name in ("ls", "stat", "file", "test", "[", "[[", "du", "realpath", "readlink", "basename",
                         "dirname", "echo", "printf", "true", "false", ":")
                or (name == "find" and not any(arg.startswith(("-exec", "-ok", "-delete", "-fprint", "-fls"))
                                               for arg in args))
                or (name == "git" and _positionals(args, {"-C", "-c", "--git-dir", "--work-tree"})[:1] in (
                    ["check-ignore"], ["ls-files"], ["status"], ["log"], ["grep"]))):
            block(f"{name} {backups[0]}", BACKUP)
        named = [arg for arg, secret in secrets if secret == DEV_ENV]
        if not named:
            return
        if name in ("ls", "stat", "file", "test", "[", "[[", "chmod", "realpath", "readlink", "basename",
                    "dirname", "du", "touch", "echo", "printf", "true", "false", ":", "lsattr", "getfacl",
                    "xattr", "id"):
            return
        if name == "find":
            if not any(arg in ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0",
                               "-fprintf", "-fls") for arg in args):
                return
            block("find -exec on dev.env")
        if name == "git":
            positionals = _positionals(args, {"-C", "-c", "--git-dir", "--work-tree"})
            if positionals[:1] and positionals[0] in ("check-ignore", "ls-files", "status", "log", "grep"):
                return  # git grep reads tracked files only; check_recursive_reads covers --no-index
            block(f"git {positionals[0] if positionals else ''} on dev.env")
        if name in ("cp", "install", "ln", "mv"):
            files = _positionals(args, {"-m", "--mode", "-o", "--owner", "-g", "--group", "-S",
                                        "--suffix", "-t", "--target-directory"})
            if files and named == [files[-1]] and not any(_DEV_ENV.search(arg) for arg in files[:-1]):
                if not self.exists(files[-1], state):
                    return  # creating dev.env (from dev.env.example)
                block(f"{name} over an existing dev.env")
            block(f"{name} dev.env")
        if name in ("source", "."):
            block(f"{name} dev.env (its secrets would be in the shell)")
        block(f"{name} {named[0]}")

    @staticmethod
    def file_arguments(name: str, args: list) -> list:
        """The arguments that may be files: the pattern of grep and the program of sed or awk
        are left out, so that `grep -n dev.env .gitignore` does not count as reading dev.env."""
        if name in ("grep", "egrep", "fgrep", "ggrep", "rg", "ag", "ack", "ack-grep"):
            values = {"-e", "-f", "-m", "-A", "-B", "-C", "-d", "-D", "-g", "-t", "-T", "--regexp",
                      "--file", "--glob", "--type", "--max-count", "--include", "--exclude",
                      "--exclude-dir", "--context", "--after-context", "--before-context"}
            given = any(arg in ("-e", "-f", "--regexp", "--file") or arg.startswith(("--regexp=", "--file="))
                        for arg in args)
            positionals = _positionals(args, values)
            return positionals if given else positionals[1:]
        if name in ("sed", "gsed"):
            values = {"-e", "--expression", "-f", "--file", "-l", "--line-length"}
            given = any(arg in ("-e", "--expression", "-f", "--file") for arg in args)
            positionals = _positionals(args, values)
            return positionals if given else positionals[1:]
        if name in ("awk", "gawk", "mawk", "nawk"):
            values = {"-F", "-v", "-f", "--field-separator", "--assign", "--file"}
            positionals = _positionals(args, values)
            return positionals if any(arg in ("-f", "--file") for arg in args) else positionals[1:]
        if name in _TAR_NAMES:
            plan = _tar_plan(args)
            return [value for kind, value in plan.items if kind in ("file", "source")]
        if name == "zip":
            plan = _zip_plan(args)
            return ([plan.archive] if plan.archive else []) + plan.sources
        if name == "rsync":
            # The patterns of --exclude dev.env and the like name no file that is copied.
            out, i = [], 0
            while i < len(args):
                if args[i] in ("--exclude", "--include", "--filter", "-f"):
                    i += 2
                    continue
                if not args[i].startswith(("--exclude=", "--include=", "--filter=")):
                    out.append(args[i])
                i += 1
            return out
        return args

    def check_recursive_reads(self, name: str, args: list, state: State) -> None:
        if state.ctx != LOCAL:
            return
        roots, excludes, includes, folders = None, [], [], []
        files_only = False  # grep's --exclude and --include match file names, never folders
        letters = "".join(_cluster(arg) for arg in args if not arg[2:3].isdigit())
        if name in ("grep", "egrep", "fgrep", "ggrep"):
            recursive = "r" in letters or "R" in letters or any(
                arg in ("--recursive", "--dereference-recursive", "--directories=recurse")
                for arg in args)
            if not recursive:
                return
            values = {"-e", "-f", "-m", "-A", "-B", "-C", "-d", "-D", "--regexp", "--file", "--max-count",
                      "--after-context", "--before-context", "--context", "--directories", "--devices",
                      "--label", "--include", "--exclude", "--exclude-dir", "--exclude-from",
                      "--binary-files", "--group-separator"}
            files_only = True
            for i, arg in enumerate(args):
                if arg.startswith("--exclude-from"):
                    return  # cannot be told; the exclusions may cover dev.env
                for option, found in (("--exclude", excludes), ("--include", includes), ("--exclude-dir", folders)):
                    if arg == option and i + 1 < len(args):
                        found.append(args[i + 1])
                    elif arg.startswith(option + "="):
                        found.append(arg.split("=", 1)[1])
            positionals = _positionals(args, values)
            pattern_given = "e" in letters or "f" in letters or any(
                arg.startswith(("--regexp", "--file")) for arg in args)
            roots = positionals if pattern_given else positionals[1:]
        elif name in ("rg", "ag"):
            unrestricted = "u" in letters or "U" in letters or any(
                arg in ("--unrestricted", "--no-ignore", "--no-ignore-vcs", "--skip-vcs-ignores")
                for arg in args)
            if not unrestricted:
                return
            for i, arg in enumerate(args):
                if arg in ("-g", "--glob", "--iglob") and i + 1 < len(args):
                    glob = args[i + 1]
                    (excludes if glob.startswith("!") else includes).append(glob.lstrip("!"))
                elif arg.startswith(("--glob=", "--iglob=")):
                    glob = arg.split("=", 1)[1]
                    (excludes if glob.startswith("!") else includes).append(glob.lstrip("!"))
            values = {"-e", "-f", "-g", "--glob", "--iglob", "-t", "-T", "--type", "--type-not", "-m",
                      "-A", "-B", "-C", "-j", "-M", "-d", "--max-depth", "-r", "--replace", "--sort",
                      "--sortr", "--pre", "--pre-glob", "--ignore-file", "-E", "--encoding"}
            positionals = _positionals(args, values)
            pattern_given = "e" in letters or "f" in letters
            roots = positionals if pattern_given else positionals[1:]
        elif name in ("ack", "ack-grep"):
            positionals = _positionals(args, {"--match", "-t", "--type", "-m", "-A", "-B", "-C"})
            roots = positionals[1:]
        elif name == "git":
            positionals = _positionals(args, {"-C", "-c", "-e", "-f", "-m", "-A", "-B"})
            if positionals[:1] != ["grep"]:
                return
            unignored = ("--no-index" in args and "--exclude-standard" not in args) or (
                "--untracked" in args and "--no-exclude-standard" in args)
            if not unignored:
                return
            roots = positionals[2:]
        elif name in ("rsync", "scp", "cp"):
            recursive = "r" in letters or "R" in letters or "a" in letters or any(
                arg in ("--recursive", "--archive") for arg in args)
            if not recursive:
                return
            for i, arg in enumerate(args):
                if arg.startswith(("--exclude-from", "--filter", "--files-from")) or arg == "-F":
                    return
                if arg == "--exclude" and i + 1 < len(args):
                    excludes.append(args[i + 1])
                elif arg.startswith("--exclude="):
                    excludes.append(arg.split("=", 1)[1])
            positionals = _positionals(args, {"-e", "--rsh", "-P", "-i", "-o", "-l", "-S", "-J", "-c",
                                              "--chmod", "--rsync-path", "-t", "--target-directory"})
            roots = positionals[:-1]
        else:
            return
        for root in roots or ["."]:
            if ":" in root.split("/")[0] and name in ("rsync", "scp"):
                continue  # a remote source
            path = _joined(state.cwd or self.project_dir, root)
            for secret in self.held_secrets(path):
                if _left_out(secret, excludes, includes, folders, files_only):
                    continue
                label = "git grep" if name == "git" else name
                if secret == BACKUP:
                    raise Blocked("dev-env", f"{label} over {root}, which holds the device backups of "
                                  "tmp/dev/backup, would show or copy the device's JWT secret, password hashes "
                                  "and database (ADR-0212, ADR-0223). Use git grep or rg (they skip ignored "
                                  "files), or leave tmp/ out (grep --exclude-dir=tmp, rsync --exclude=tmp/)")
                raise Blocked("dev-env", f"{label} over {root}, which holds dev.env, would show or copy the "
                              "owner's secrets (ADR-0212). Use git grep or rg (they skip ignored files), or "
                              "exclude it (--exclude=dev.env)")

    def check_archives(self, name: str, args: list, state: State) -> None:
        """tar and zip archives whose sources hold dev.env or a device backup: the archive would
        copy the secrets."""
        if state.ctx != LOCAL:
            return
        base = state.cwd or self.project_dir
        sources, excludes, includes = [], [], []
        if name in _TAR_NAMES:
            plan = _tar_plan(args)
            if not plan.modes & set("cru"):
                return  # it lists or extracts
            for kind, value in plan.items:
                if kind == "xlist":
                    return  # exclusions from a file: cannot be told
                if kind == "dir":
                    base = _joined(base, value)
                elif kind == "exclude":
                    excludes.append(value)
                elif kind == "source":
                    sources.append((_joined(base, value) if base else None, value))
        elif name == "zip":
            plan = _zip_plan(args)
            if not plan.recursive or plan.unknown:
                return  # a directory without its files, or patterns from a file that cannot be told
            excludes, includes = plan.excludes, plan.includes
            sources = [(_joined(base, value), value) for value in plan.sources]
        else:
            return
        for path, label in sources:
            for secret in self.held_secrets(path):
                if _left_out(secret, excludes, includes):
                    continue
                if secret == BACKUP:
                    raise Blocked("dev-env", f"{name} of {label}, which holds the device backups of tmp/dev/backup, "
                                  "would copy the device's JWT secret, password hashes and database (ADR-0212, "
                                  "ADR-0223). Leave tmp/ out (tar --exclude=tmp, zip -x 'tmp/*'), or archive the "
                                  "tracked files with git archive")
                raise Blocked("dev-env", f"{name} of {label}, which holds dev.env, would copy the owner's "
                              "secrets (ADR-0212). Exclude it (tar --exclude=dev.env, zip -x dev.env), or "
                              "archive the tracked files with git archive")

    def secret_places(self) -> list:
        """(path, secret) of the owner's secrets that this session's scripts use: dev.env and the
        device backups in tmp/dev/backup, of the project directory and, when it is a linked git
        worktree, of its main checkout, whose dev.env and backups the worktree's scripts use
        (ADR-0210). A search, copy or archive of a folder that holds one of them would show or
        copy it, also from the worktree (grep -r ..)."""
        if self._secret_places is None:
            roots = [self.project_dir] if self.project_dir else []
            main = _main_checkout(self.project_dir) if self.project_dir else None
            if main:
                roots.append(main)
            places = []
            for root in roots:
                for relative, secret in (("dev.env", DEV_ENV), (os.path.join("tmp", "dev", "backup"), BACKUP)):
                    if os.path.lexists(os.path.join(root, relative)):
                        places.append((os.path.join(root, relative), secret))
            self._secret_places = places
        return self._secret_places

    def held_secrets(self, path) -> list:
        """The secrets that a folder holds, in it or below it: DEV_ENV for a dev.env in it or for
        one of secret_places() inside it, BACKUP for a device backup."""
        if not path or not os.path.isdir(path):
            return []
        held = []
        if os.path.exists(os.path.join(path, "dev.env")):
            held.append(DEV_ENV)
        real = os.path.realpath(path).replace(os.sep, "/")
        # In a backup, or above one: a checkout's root, its tmp or its tmp/dev.
        below = "backup" if real.endswith("/tmp/dev") else "dev/backup" if real.endswith("/tmp") else "tmp/dev/backup"
        if _BACKUP.search(real + "/") or os.path.isdir(os.path.join(path, *below.split("/"))):
            held.append(BACKUP)
        for place, secret in self.secret_places():
            if secret not in held and _inside(place, path):
                held.append(secret)
        return held


# --- background commands (rule background-deadline, ADR-0335) -------------------------------

MAX_DEADLINE = 2 * 3600  # seconds
_TIMEOUTS = {"timeout", "gtimeout", "/opt/homebrew/bin/timeout", "/opt/homebrew/bin/gtimeout"}
# The signals that end a process unless it catches them; others (CHLD, CONT, STOP, 0, ...) leave
# the command running past its deadline.
_ENDING_SIGNALS = {"HUP", "INT", "QUIT", "KILL", "TERM", "1", "2", "3", "9", "15"}
_DURATION = re.compile(r"(\d+(?:\.\d*)?|\.\d+)([smhd]?)", re.ASCII)
_DURATION_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def _seconds(duration: str):
    """The seconds of a duration of GNU timeout (a number with an optional unit), or None."""
    match = _DURATION.fullmatch(duration)
    return float(match.group(1)) * _DURATION_SECONDS[match.group(2)] if match else None


def _shown(word: str, otherwise: str) -> str:
    """`word` as a message shows it when it is a plain name or number, else `otherwise`: a word
    of the command may hold a secret or a line break."""
    return f"`{word}`" if re.fullmatch(r"[A-Za-z0-9_./+,=%-]{1,40}", word) else otherwise


def check_background(command: str) -> None:
    """Rule background-deadline: a command that the Bash tool runs in the background is one call
    of timeout that ends it within MAX_DEADLINE seconds (ADR-0335).

        [cd DIR &&]... [NAME=VALUE ...] timeout [OPTIONS] DURATION COMMAND [ARGS] [REDIRECTIONS]

    Claude Code wakes the session when such a command ends, so one that never ends stalls the
    session unseen. Nothing else stands at the top level: no ; && || | & ( ) or newline but the
    && after a cd DIR, and no $(...), backticks or <(...) outside single quotes, which the outer
    shell runs before timeout starts. A heredoc's body is the command's input; a ; or newlines at
    the end, and blank lines or comments at the start, are no commands. DURATION is more than 0,
    and with the -k of SIGKILL at most MAX_DEADLINE; -s names a signal that ends a process, and
    --foreground, which would leave the command's children running, is refused."""

    def block(problem: str) -> None:
        raise Blocked("background-deadline", f"{problem}: a background command that never ends never wakes "
                      "the session (ADR-0335). Write it as `timeout <seconds> bash -c '<command>'`, with a "
                      f"deadline of at most {MAX_DEADLINE} s, and put loops, pipelines, ; && || and $(...) "
                      "inside the single quotes, where bash runs them")

    nested: list = []
    tokens, bodies, _, _ = _lex(command, 0, None, nested)
    while tokens and tokens[-1] in (("op", "\n"), ("op", ";")):
        tokens.pop()
    while True:
        while tokens and tokens[0] == ("op", "\n"):  # blank lines, comments, a line after &&
            tokens.pop(0)
        if not (len(tokens) > 3 and tokens[0][0] == tokens[1][0] == "word" and tokens[2] == ("op", "&&")
                and _unquote(tokens[0][1]) == "cd"):
            break
        tokens = tokens[3:]
    end = next((i for i, (kind, _) in enumerate(tokens) if kind == "op"), len(tokens))
    words = [word for simple in _simple_commands(tokens[:end], bodies) for word in simple.words]
    while words and _ASSIGNMENT.match(words[0]):
        words.pop(0)
    if not words:
        block("the command does not start with timeout")
    if words[0] == "cd":
        block("a cd before timeout must be `cd DIR &&`")
    if words[0] not in _TIMEOUTS:
        block(f"{_shown(words[0], 'its first word')} is not timeout")
    if end < len(tokens):
        operator = tokens[end][1]
        name = "a newline" if operator == "\n" else f"`{operator}`"
        block(f"{name} after the call of timeout makes the call only part of the command")
    if nested:
        block("`$(...)`, backticks and `<(...)` outside single quotes run in the outer shell, before timeout "
              "starts")
    kill_after, i = 0.0, 1
    while i < len(words) and words[i].startswith("-") and words[i] != "-":
        option, value = words[i], None
        if option == "--":
            i += 1
            break
        if option.startswith(("--kill-after=", "--signal=")):
            option, value = option.split("=", 1)
        elif option[:2] in ("-k", "-s") and len(option) > 2:
            option, value = option[:2], option[2:]
        if option in ("-k", "--kill-after", "-s", "--signal") and value is None:
            i += 1
            value = words[i] if i < len(words) else ""
        if option in ("-k", "--kill-after"):
            kill_after = _seconds(value)
            if kill_after is None:
                block(f"the {option} of timeout takes a duration (seconds, or a number with s, m, h or d)")
        elif option in ("-s", "--signal"):
            if (value[3:] if value.upper().startswith("SIG") else value).upper() not in _ENDING_SIGNALS:
                block(f"the signal {_shown(value, 'of ' + option)} may not end the command; give TERM, INT, HUP, "
                      "QUIT or KILL")
        elif option in ("-f", "--foreground"):
            block(f"`{option}` leaves the children of the command running after the deadline")
        elif option not in ("-p", "--preserve-status", "-v", "--verbose"):
            block(f"{_shown(option, 'an option')} is not an option of timeout that this rule knows")
        i += 1
    if i >= len(words):
        block("timeout has no duration")
    seconds = _seconds(words[i])
    if seconds is None:
        shown = _shown(words[i], "")
        block(f"timeout's duration {shown + ' ' if shown else ''}is not a number with an optional unit s, m, h "
              "or d")
    if seconds <= 0:
        block("a deadline of 0 turns timeout off")
    total = seconds + kill_after
    if total > MAX_DEADLINE:
        number = int(total) if total.is_integer() else total
        block(f"a deadline of {number} s{', with the SIGKILL of -k,' if kill_after else ''} is longer than "
              f"{MAX_DEADLINE} s")
    if i + 1 >= len(words):
        block("timeout has no command to run")


def log_denial(rule: str, tool: str) -> None:
    """Add a line to tmp/guard/denials.log of the project: the UTC time, the rule and the tool.
    Never the command: for secret-argv it holds the secret. Best effort: nothing here blocks."""
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project or not os.path.isdir(project):
        return
    try:
        path = os.path.join(project, DENIALS_LOG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = "{} {} {}\n".format(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), rule,
                                   re.sub(r"[^A-Za-z0-9_.-]", "_", tool)[:100] or "unknown")
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, line.encode())
        finally:
            os.close(descriptor)
    except Exception:  # the log must neither block nor unblock a command
        pass


def main() -> int:
    tool = "unknown"
    try:
        data = json.load(sys.stdin)
        tool = data.get("tool_name")
        if tool not in COMMAND_TOOLS:
            return 0
        tool_input = data.get("tool_input") or {}
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return 0
        cwd = data.get("cwd") or os.getcwd()
        if isinstance(tool_input.get("cwd"), str) and tool_input["cwd"].strip():
            # The terminal tool opens its tab in tool_input.cwd (absolute, ~ or relative to cwd).
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(tool_input["cwd"])))
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
        Guard(cwd, project_dir).check(command)
        if tool == "Bash" and tool_input.get("run_in_background"):
            check_background(command)
    except Blocked as blocked:
        print(f"Blocked by .claude/hooks/guard.py ({blocked.rule}): {blocked.message}.", file=sys.stderr)
        log_denial(blocked.rule, str(tool))
        return 2
    except Exception as error:  # an error of the guard must never block a command
        print(f"guard.py: internal error, the command was not checked ({type(error).__name__}: {error})",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
