"""System > Logs reads the host's journal, newest first (ADR-0257, ADR-0258).

Every source is a part of the host's systemd journal that journalctl prints on the host: the
commands go to ``FakeJournal`` through a patched ``src.host.run_on_host``. The fake keeps its
entries oldest first, as the journal does, and answers the options the router sends the way
journalctl does (--reverse, --lines, --priority, --since, --grep and --case-sensitive, --dmesg,
--unit and FIELD=VALUE matches); a command or an option it does not know fails the test. It
prints like journalctl too: a field as text when it is printable, else as its bytes, and other
characters than the control characters as they are (U+2028 among them); and a --grep that
matched nothing exits with status 1 and prints nothing, as grep does. coreutils' timeout(1) in
front of journalctl ends a journalctl that reads too long; without it, the end of the wait of
subprocess.run leaves journalctl reading on the host.
``subprocess`` is blocked, so nothing runs on this machine, whatever the code under test does.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.host import HostCommandError, HostResult
from src.main import app

logs = importlib.import_module("src.logs.router")

REPO = Path(__file__).resolve().parents[2]
API = "/api/v1/logs"

LEVELS = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]
BOOT = "6f0c3c2b1a9e4d7f8e5b4a3c2d1e0f9a"
OLD_BOOT = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"

# The sources as GET /sources lists them, in their order, and the journal matches of each.
SOURCES = [
    {"name": "journal", "label": "System Journal", "description": "Everything in the host's systemd journal"},
    {"name": "dmesg", "label": "Kernel", "description": "The kernel's messages since the host started"},
    {"name": "docker", "label": "Docker", "description": "The Docker daemon"},
    # nmbd runs only where NetBIOS is on; UNOS turns it off (ADR-0275), and the journal keeps
    # what nmbd logged before.
    {"name": "samba", "label": "Samba", "description": "The SMB file server: smbd, and nmbd where NetBIOS is on"},
    {"name": "auth", "label": "Authentication",
     "description": "Sign-ins, sudo and the other messages of the auth and authpriv facilities"},
    {"name": "cron", "label": "Cron Jobs", "description": "The host's cron daemon"},
]
MATCHES = {
    "journal": [],
    "dmesg": ["--dmesg"],
    "docker": ["--unit=docker.service"],
    "samba": ["--unit=smbd.service", "--unit=nmbd.service"],
    "auth": ["SYSLOG_FACILITY=4", "SYSLOG_FACILITY=10"],
    "cron": ["--unit=cron.service"],
}
FIELDS = "MESSAGE,PRIORITY,SYSLOG_FACILITY,SYSLOG_IDENTIFIER,_COMM,_SYSTEMD_UNIT"
CANNOT_READ = "Could not read the host's journal: "
# timeout(1) on the host ends journalctl after 30 seconds (SIGTERM), and 5 seconds later (SIGKILL).
HOST_TIMEOUT = ["timeout", "--kill-after=5", "30"]
DID_NOT_ANSWER = CANNOT_READ + "journalctl did not answer within 30 seconds"


def _web_ui_values(name: str) -> list[str]:
    """The values of the options list ``name`` of webui/src/lib/api/logs.ts, which the tab sends."""
    module = (REPO / "webui" / "src" / "lib" / "api" / "logs.ts").read_text(encoding="utf-8")
    options = module[module.index(f"export const {name}"):]
    return re.findall(r'value: "([^"]*)"', options[: options.index("];")])


# The time ranges of the tab ("" is all time: no since).
TIME_RANGES = [value for value in _web_ui_values("TIME_RANGES") if value]


def journalctl(source: str = "journal", *, lines: int = 100, filters: tuple[str, ...] = ()) -> list[str]:
    """The command a request runs on the host: journalctl under timeout(1), the newest ``lines``
    entries first, then the filters and the source's matches."""
    return [
        *HOST_TIMEOUT,
        "journalctl", "--no-pager", "--quiet", "--all", "--output=json", f"--output-fields={FIELDS}",
        "--reverse", f"--lines={lines}", *filters, *MATCHES[source],
    ]


def _usec(seconds: float) -> str:
    return str(int(seconds * 1_000_000))


def _text(value) -> str:
    if isinstance(value, list):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def _control(char: str) -> bool:
    """systemd's unichar_is_control(): C0 but tab and newline, DEL and C1."""
    code = ord(char)
    return (code < 32 and char not in "\t\n") or 0x7F <= code <= 0x9F


def _printed(value):
    """A field as journalctl's JSON prints it: text when it is printable, else its bytes."""
    if isinstance(value, str) and any(_control(char) for char in value):
        return list(value.encode("utf-8"))
    return value


class FakeJournal:
    """The host behind ``src.host.run_on_host``: a systemd journal that journalctl prints."""

    def __init__(self) -> None:
        self.now = time.time()
        self.entries: list[dict] = []  # oldest first, as the journal keeps them
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self.on_event_loop: list[bool] = []
        self.results: list[HostResult] = []
        # What journalctl answers instead of the journal's entries (an error, other output).
        self.answer: HostResult | None = None
        # Seconds journalctl reads before it answers, such as a search through a large journal
        # that finds nothing; None: it answers at once.
        self.reads_for: float | None = None
        # The journalctl commands that still read on the host after the supervisor stopped waiting.
        self.still_reading: list[list[str]] = []

    def add(self, message, *, minutes_ago, priority=6, identifier=None, comm=None, unit=None,
            transport="syslog", facility=None, boot=BOOT, **fields) -> dict:
        """Append an entry; entries are added oldest first."""
        entry = {
            "__CURSOR": f"s=4c1d;i={len(self.entries) + 1:x};b={boot}",
            "__REALTIME_TIMESTAMP": _usec(self.now - minutes_ago * 60),
            "__MONOTONIC_TIMESTAMP": _usec(1_000 + len(self.entries)),
            "_BOOT_ID": boot,
            "_TRANSPORT": transport,
            "_HOSTNAME": "nas",
            "MESSAGE": message,
        }
        for key, value in (("PRIORITY", priority), ("SYSLOG_IDENTIFIER", identifier), ("_COMM", comm),
                           ("_SYSTEMD_UNIT", unit), ("SYSLOG_FACILITY", facility)):
            if value is not None:
                entry[key] = str(value)
        entry.update(fields)
        self.entries.append(entry)
        return entry

    def run(self, cmd, *, timeout=60, check=False, input=None, errors=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        self.timeouts.append(timeout)
        try:
            asyncio.get_running_loop()
            self.on_event_loop.append(True)
        except RuntimeError:
            self.on_event_loop.append(False)
        result = self.answer if self.answer is not None else self._on_host(cmd, timeout)
        self.results.append(result)
        if check and not result.ok:
            raise HostCommandError(cmd, result.returncode, result.stderr)
        return result

    def _on_host(self, cmd: list[str], wait: float) -> HostResult:
        """journalctl, maybe under timeout(1), which ends it after DURATION seconds: status 124."""
        limit = None
        if cmd[0] == "timeout":
            cmd = cmd[1:]
            while cmd[0].startswith("-"):
                name, _, value = cmd.pop(0).partition("=")
                assert name == "--kill-after" and float(value) > 0, f"the fake timeout does not know {name}"
            limit = float(cmd.pop(0))
        if self.reads_for is not None:
            ends_after = self.reads_for if limit is None else min(self.reads_for, limit)
            if ends_after > wait:
                # subprocess.run kills nsenter, which forked for --pid and does not pass the
                # signal on: journalctl reads on, on the host.
                self.still_reading.append(cmd)
                raise subprocess.TimeoutExpired(cmd, wait)
            if limit is not None and self.reads_for > limit:
                return HostResult(124, "", "")
        return self._journalctl(cmd)

    def _since(self, value: str) -> int:
        number, unit, ago = value.split(" ")
        seconds = {"minute": 60, "minutes": 60, "hour": 3600, "hours": 3600, "day": 86400, "days": 86400}
        assert ago == "ago" and unit in seconds, f"the fake journalctl does not know --since={value}"
        return int((time.time() - int(number) * seconds[unit]) * 1_000_000)

    def _journalctl(self, cmd: list[str]) -> HostResult:
        if cmd[0] != "journalctl":
            raise AssertionError(f"the fake host does not know {cmd!r}")
        output = fields = lines = priority = since = pattern = case_sensitive = None
        reverse = dmesg = False
        units: list[str] = []
        matches: dict[str, set[str]] = {}
        for arg in cmd[1:]:
            name, _, value = arg.partition("=")
            if arg in ("--no-pager", "--quiet", "--all"):
                continue
            if arg == "--reverse":
                reverse = True
            elif arg == "--dmesg":
                dmesg = True
            elif name == "--output":
                output = value
            elif name == "--output-fields":
                fields = set(value.split(","))
            elif name == "--lines":
                lines = int(value)
            elif name == "--priority":
                priority = LEVELS.index(value)
            elif name == "--since":
                since = self._since(value)
            elif name == "--grep":
                pattern = value
            elif name == "--case-sensitive":
                case_sensitive = {"true": True, "false": False}[value]
            elif name == "--unit":
                units.append(value)
            elif re.fullmatch(r"[A-Z_][A-Z0-9_]*", name) and value:
                matches.setdefault(name, set()).add(value)
            else:
                raise AssertionError(f"the fake journalctl does not know {arg!r}")
        assert output == "json", "the router reads journalctl's JSON"
        if pattern is not None:
            # journalctl matches case-insensitively when the pattern has no capital letter, unless told.
            caseless = (pattern == pattern.lower()) if case_sensitive is None else not case_sensitive
            compiled = re.compile(pattern, re.IGNORECASE if caseless else 0)

        def wanted(entry: dict) -> bool:
            if dmesg and (entry["_TRANSPORT"] != "kernel" or entry["_BOOT_ID"] != BOOT):
                return False
            if units and entry.get("_SYSTEMD_UNIT") not in units and entry.get("UNIT") not in units:
                return False
            if any(entry.get(field) not in values for field, values in matches.items()):
                return False
            if priority is not None and ("PRIORITY" not in entry or int(entry["PRIORITY"]) > priority):
                return False
            if since is not None and int(entry["__REALTIME_TIMESTAMP"]) < since:
                return False
            return pattern is None or compiled.search(_text(entry["MESSAGE"])) is not None

        selected = [entry for entry in self.entries if wanted(entry)]
        if reverse:
            selected = selected[::-1][:lines] if lines is not None else selected[::-1]
        elif lines is not None:
            selected = selected[-lines:]
        if pattern is not None and not selected:
            # journalctl --grep copies grep: when it showed nothing, it exits with status 1, silently.
            return HostResult(1, "", "")
        shown = (fields or set()) | {"__CURSOR", "__REALTIME_TIMESTAMP", "__MONOTONIC_TIMESTAMP", "_BOOT_ID"}
        printed = [{key: _printed(value) for key, value in entry.items() if fields is None or key in shown}
                   for entry in selected]
        # Control characters escaped, everything else as it is: journalctl writes UTF-8, not ASCII.
        return HostResult(0, "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in printed), "")


def _forbidden(*args, **kwargs):
    raise AssertionError(f"a test tried to run a command outside host.run_on_host: {args!r}")


# The journal of the tests, oldest first: (message, minutes ago, fields).
JOURNAL = [
    ("Linux version 6.1.0-28-arm64 (debian-kernel@lists.debian.org)", 3 * 24 * 60,
     dict(identifier="kernel", transport="kernel", facility=0, boot=OLD_BOOT)),
    ("Booting Linux on physical CPU 0x0000000000 [0x412fd050]", 180,
     dict(identifier="kernel", transport="kernel", facility=0)),
    ("usb 1-1: device descriptor read/64, error -71", 170,
     dict(priority=3, identifier="kernel", transport="kernel", facility=0)),
    ("Started docker.service - Docker Application Container Engine.", 160,
     dict(identifier="systemd", comm="systemd", unit="init.scope", facility=3, UNIT="docker.service")),
    ("API listen on /run/docker.sock", 159,
     dict(identifier="dockerd", comm="dockerd", unit="docker.service", facility=3)),
    ("smbd version 4.17.12-Debian started.", 150,
     dict(priority=5, identifier="smbd", comm="smbd", unit="smbd.service", facility=3)),
    ("Accepted publickey for admin from 192.0.2.10 port 50122 ssh2: ED25519 SHA256:q2Xx", 120,
     dict(identifier="sshd", comm="sshd", unit="ssh.service", facility=4)),
    ("admin : TTY=pts/0 ; PWD=/home/admin ; USER=root ; COMMAND=/usr/bin/apt update", 90,
     dict(priority=5, identifier="sudo", comm="sudo", unit="session-3.scope", facility=10)),
    ("(root) CMD (command -v debian-sa1 > /dev/null && debian-sa1 1 1)", 40,
     dict(identifier="CRON", comm="cron", unit="cron.service", facility=9)),
    ("Samba name server NAS is now a local master browser for workgroup WORKGROUP", 30,
     dict(priority=5, identifier="nmbd", comm="nmbd", unit="nmbd.service", facility=3)),
    ("Container failed to exit within 10s of signal 15 - using the force", 20,
     dict(priority=4, identifier="dockerd", comm="dockerd", unit="docker.service", facility=3)),
    ("EXT4-fs warning (device sda1): ext4_dx_add_entry:2379: Directory index full!", 10,
     dict(priority=4, identifier="kernel", transport="kernel", facility=0)),
    ("Failed password for invalid user admin from 198.51.100.7 port 4422 ssh2", 5,
     dict(identifier="sshd", comm="sshd", unit="ssh.service", facility=4)),
    ("pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 tty=ssh rhost=198.51.100.7", 4.9,
     dict(priority=5, identifier="sshd", comm="sshd", unit="ssh.service", facility=10)),
    ("Out of memory: Killed process 4242 (python3) total-vm:812345kB", 1,
     dict(priority=3, identifier="kernel", transport="kernel", facility=0)),
]
MESSAGES = [message for message, _, _ in JOURNAL]
NEWEST_FIRST = MESSAGES[::-1]
OOM, PAM, FAILED_PASSWORD, EXT4, NO_EXIT, NMBD, CRON, SUDO, PUBLICKEY, SMBD, DOCKER_API, STARTED_DOCKER, \
    USB, BOOTING, OLD_KERNEL = NEWEST_FIRST

# Each source's entries, newest first.
EXPECTED = {
    "journal": NEWEST_FIRST,
    "dmesg": [OOM, EXT4, USB, BOOTING],
    "docker": [NO_EXIT, DOCKER_API, STARTED_DOCKER],
    "samba": [NMBD, SMBD],
    "auth": [PAM, FAILED_PASSWORD, SUDO, PUBLICKEY],
    "cron": [CRON],
}


@pytest.fixture
def journal(monkeypatch):
    """Host commands reach FakeJournal through nsenter's path; nothing runs on this machine."""
    fake = FakeJournal()
    for message, minutes_ago, fields in JOURNAL:
        fake.add(message, minutes_ago=minutes_ago, **fields)
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _forbidden)
    monkeypatch.setattr(os, "system", _forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _forbidden)
    return fake


def _client(user):
    app.dependency_overrides[verify_token] = lambda: user
    return TestClient(app)


@pytest.fixture
def client(journal):
    """TestClient on the real app with an administrator signed in."""
    try:
        yield _client(SimpleNamespace(id=1, username="admin", is_admin=True, is_active=True))
    finally:
        app.dependency_overrides.pop(verify_token, None)


def entries(client, **params) -> list[dict]:
    response = client.get(f"{API}/entries", params=params)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["total"] == len(answer["entries"])
    return answer["entries"]


def messages(client, **params) -> list[str]:
    return [entry["message"] for entry in entries(client, **params)]


# ---------------------------------------------------------------------------
# Step 1: pick a source and a severity, the entries are listed newest first
# ---------------------------------------------------------------------------


class TestSources:
    def test_the_sources_are_parts_of_the_hosts_journal_and_listing_them_runs_nothing(self, client, journal):
        response = client.get(f"{API}/sources")

        assert response.status_code == 200, response.text
        assert response.json() == {"sources": SOURCES}
        assert journal.calls == []

    @pytest.mark.parametrize("source", list(MATCHES))
    def test_each_source_lists_its_entries_of_the_hosts_journal_newest_first(self, client, journal, source):
        listed = entries(client, source=source)

        assert [entry["message"] for entry in listed] == EXPECTED[source]
        assert {entry["source"] for entry in listed} == {source}
        assert journal.calls == [journalctl(source)]

    def test_the_kernel_source_holds_the_messages_of_this_boot_only(self, client, journal):
        assert OLD_KERNEL in messages(client, source="journal")
        assert OLD_KERNEL not in messages(client, source="dmesg")

    def test_the_default_source_is_the_whole_journal(self, client, journal):
        assert messages(client) == NEWEST_FIRST
        assert journal.calls == [journalctl("journal")]

    def test_the_limit_takes_the_newest_entries(self, client, journal):
        assert messages(client, source="journal", limit=3) == [OOM, PAM, FAILED_PASSWORD]
        assert journal.calls == [journalctl("journal", lines=3)]


class TestSeverity:
    """A severity lists the entries at that level or a more severe one, in every source."""

    def test_warning_in_the_kernel_lists_warnings_and_worse_newest_first(self, client, journal):
        listed = entries(client, source="dmesg", priority="warning")

        assert [(entry["message"], entry["priority"]) for entry in listed] == [
            (OOM, "err"), (EXT4, "warning"), (USB, "err")
        ]
        assert journal.calls == [journalctl("dmesg", filters=("--priority=warning",))]

    @pytest.mark.parametrize("source", list(MATCHES))
    @pytest.mark.parametrize("priority", LEVELS)
    def test_every_source_lists_that_level_and_the_more_severe_ones(self, client, journal, source, priority):
        listed = entries(client, source=source, priority=priority)

        assert all(LEVELS.index(entry["priority"]) <= LEVELS.index(priority) for entry in listed)
        everything = {entry["message"]: entry["priority"] for entry in entries(client, source=source)}
        assert [entry["message"] for entry in listed] == [
            message for message in EXPECTED[source] if LEVELS.index(everything[message]) <= LEVELS.index(priority)
        ]

    def test_the_tabs_levels_are_the_apis(self):
        assert _web_ui_values("PRIORITY_LEVELS") == LEVELS

    def test_an_entrys_level_is_the_one_its_program_logged_with(self, client, journal):
        # sshd logs a failed password at info: no level is guessed from words like "failed" or "accepted".
        levels = {entry["message"]: entry["priority"] for entry in entries(client, source="auth")}

        assert levels == {PAM: "notice", FAILED_PASSWORD: "info", SUDO: "notice", PUBLICKEY: "info"}


class TestTimeRange:
    def test_the_time_range_lists_the_entries_since_then(self, client, journal):
        assert messages(client, source="journal", since="1 hour ago") == [
            OOM, PAM, FAILED_PASSWORD, EXT4, NO_EXIT, NMBD, CRON
        ]
        assert journal.calls == [journalctl("journal", filters=("--since=1 hour ago",))]

    def test_the_time_range_applies_to_the_kernel_too(self, client, journal):
        assert messages(client, source="dmesg", since="15 minutes ago") == [OOM, EXT4]

    def test_the_tab_offers_its_time_ranges(self):
        assert TIME_RANGES == ["5 minutes ago", "15 minutes ago", "1 hour ago", "6 hours ago", "24 hours ago",
                               "7 days ago"]

    @pytest.mark.parametrize("since", TIME_RANGES)
    def test_the_tabs_time_ranges_are_taken(self, client, journal, since):
        entries(client, since=since)

        assert journal.calls == [journalctl("journal", filters=(f"--since={since}",))]


class TestEntries:
    def test_an_entry_has_its_time_in_utc_level_program_and_message(self, client, journal):
        journal.entries = []
        journal.now = datetime(2026, 11, 7, 9, 59, 30, 123456, tzinfo=timezone.utc).timestamp()
        journal.add("Started Session 3 of User admin.", minutes_ago=0, priority=6, identifier="systemd",
                    comm="systemd", unit="init.scope", facility=3)

        assert entries(client) == [{
            "timestamp": "2026-11-07T09:59:30+00:00",
            "facility": "3",
            "priority": "info",
            "message": "Started Session 3 of User admin.",
            "source": "journal",
            "unit": "systemd",
        }]

    def test_an_entrys_program_is_its_syslog_identifier_else_its_command_else_its_unit(self, client, journal):
        journal.entries = []
        journal.add("by identifier", minutes_ago=4, identifier="sudo", comm="sudo", unit="session-3.scope")
        journal.add("by command", minutes_ago=3, comm="python3", unit="unos.service")
        journal.add("by unit", minutes_ago=2, unit="smartd.service")
        journal.add("by nothing", minutes_ago=1)

        assert [(entry["message"], entry["unit"]) for entry in entries(client)] == [
            ("by nothing", None), ("by unit", "smartd.service"), ("by command", "python3"), ("by identifier", "sudo")
        ]

    def test_a_message_journalctl_prints_as_bytes_is_decoded(self, client, journal):
        journal.entries = []
        journal.add([99, 97, 102, 195, 169, 32, 255], minutes_ago=1)  # "café " and a byte that is not UTF-8

        assert messages(client) == ["café �"]

    def test_markup_in_a_message_stays_text(self, client, journal):
        journal.entries = []
        journal.add('Failed password for <img src=x onerror="alert(1)"> from 192.0.2.7', minutes_ago=1)

        assert messages(client) == ['Failed password for <img src=x onerror="alert(1)"> from 192.0.2.7']

    def test_lines_that_are_not_entries_are_skipped(self, client, journal):
        journal.answer = HostResult(0, "-- No entries --\n", "")

        assert entries(client) == []

    def test_an_entry_with_a_line_or_paragraph_separator_in_its_text_is_one_entry(self, client, journal):
        # journalctl escapes only the control characters in its JSON: U+2028 and U+2029, which
        # anyone can put into a user name or a request path, come out as they are, and
        # str.splitlines() would cut an entry's line there.
        journal.entries = []
        journal.add("Invalid user \u2028evil from 198.51.100.7 port 4422", minutes_ago=3, identifier="sshd")
        journal.add("Accepted password for alice from 192.0.2.10", minutes_ago=2, identifier="sshd")
        journal.add("share \u2029music refused", minutes_ago=1, identifier="smbd\u2028")

        listed = entries(client)

        assert "\u2028" in journal.results[-1].stdout and "\u2029" in journal.results[-1].stdout
        assert [(entry["message"], entry["unit"]) for entry in listed] == [
            ("share \u2029music refused", "smbd\u2028"),
            ("Accepted password for alice from 192.0.2.10", "sshd"),
            ("Invalid user \u2028evil from 198.51.100.7 port 4422", "sshd"),
        ]

    def test_a_message_with_control_characters_stays_text(self, client, journal):
        # journalctl prints such a field as its bytes; the entry has the text, the escapes included.
        journal.entries = []
        journal.add("\x1b[1;31mI/O error\x1b[0m on sda", minutes_ago=1, identifier="kernel")

        assert messages(client) == ["\x1b[1;31mI/O error\x1b[0m on sda"]
        assert '"MESSAGE": [27, 91, ' in journal.results[-1].stdout

    def test_the_journal_is_read_off_the_event_loop_and_ends_on_the_host_in_time(self, client, journal):
        entries(client, source="dmesg")

        assert journal.on_event_loop == [False]
        assert journal.calls == [journalctl("dmesg")]
        assert logs.JOURNAL_TIMEOUT == 30 and logs.KILL_AFTER == 5
        # The supervisor waits longer than timeout(1) may take to end journalctl: the host answers.
        [wait] = journal.timeouts
        assert logs.JOURNAL_TIMEOUT + logs.KILL_AFTER < wait <= 60

    def test_at_most_two_journal_reads_run_at_once_in_threads_of_their_own(self, journal, monkeypatch):
        # Not on the default executor, whose threads sign-ins (bcrypt) and other pages need, and
        # never more than two journalctl at a time on the host.
        lock = threading.Lock()
        two_running = threading.Event()
        running, most, threads = 0, 0, set()

        def reading(cmd, **kwargs):
            nonlocal running, most
            with lock:
                running += 1
                most = max(most, running)
                threads.add(threading.current_thread().name)
                if running == 2:
                    two_running.set()
            two_running.wait(timeout=2)  # the first two overlap whenever two threads run them
            time.sleep(0.05)
            with lock:
                running -= 1
            return journal.run(cmd, **kwargs)

        monkeypatch.setattr(host_module, "run_on_host", reading)

        async def five_reads():
            return await asyncio.gather(*(logs.read_journal("journal", limit=1) for _ in range(5)))

        answers = asyncio.run(five_reads())

        assert [[entry.message for entry in answer.entries] for answer in answers] == [[OOM]] * 5
        assert most == 2
        assert threads and all(name.startswith("unos-journal") for name in threads), threads


# ---------------------------------------------------------------------------
# Step 2: search, done by journalctl over the whole range
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_finds_the_newest_matches_in_any_case(self, client, journal):
        assert messages(client, search="FAILED") == [FAILED_PASSWORD, NO_EXIT]
        assert journal.calls == [journalctl("journal", filters=("--grep=FAILED", "--case-sensitive=false"))]

    def test_search_finds_matches_older_than_the_newest_entries(self, client, journal):
        # Not only among the newest `limit` entries: journalctl looks for the newest matches.
        assert messages(client, search="descriptor", limit=2) == [USB]

    @pytest.mark.parametrize(
        "search,pattern,found",
        [
            ("(python3)", r"\(python3\)", [OOM]),
            ("error -71", r"error\ \-71", [USB]),
            ("read.64", r"read\.64", []),
            ("[0x412fd050]", r"\[0x412fd050\]", [BOOTING]),
            ("a$|b*", r"a\$\|b\*", []),
        ],
    )
    def test_search_takes_the_text_literally(self, client, journal, search, pattern, found):
        assert messages(client, search=search) == found
        assert journal.calls == [journalctl("journal", filters=(f"--grep={pattern}", "--case-sensitive=false"))]

    def test_search_combines_with_the_source_the_severity_and_the_time_range(self, client, journal):
        found = messages(client, source="auth", priority="notice", since="1 hour ago", search="198.51.100.7")

        assert found == [PAM]
        assert journal.calls == [journalctl("auth", filters=(
            "--priority=notice", "--since=1 hour ago", r"--grep=198\.51\.100\.7", "--case-sensitive=false"
        ))]

    @pytest.mark.parametrize("params", [{"search": "no-such-word"}, {"source": "cron", "search": "sshd"},
                                        {"source": "dmesg", "priority": "emerg", "search": "memory"}])
    def test_a_search_that_finds_nothing_lists_no_entries(self, client, journal, params):
        # journalctl --grep exits with status 1 and says nothing when no entry matched, as grep does.
        assert messages(client, **params) == []
        assert journal.results == [HostResult(1, "", "")]


# ---------------------------------------------------------------------------
# Errors, values and roles
# ---------------------------------------------------------------------------


class TestErrors:
    def test_a_journal_that_cannot_be_read_answers_the_hosts_message(self, client, journal):
        journal.answer = HostResult(1, "", "Failed to get journal access: Permission denied\n")

        response = client.get(f"{API}/entries", params={"source": "docker"})

        assert response.status_code == 500
        assert response.json()["detail"] == CANNOT_READ + "Failed to get journal access: Permission denied"

    @pytest.mark.parametrize(
        "params,answer,detail",
        [
            # Status 1 with journalctl's message is its error, also in a search.
            ({"search": "error"}, HostResult(1, "", "Failed to open journal: Too many open files\n"),
             "Failed to open journal: Too many open files"),
            # Only a search's status 1 means "no match".
            ({}, HostResult(1, "", ""), "journalctl exited with status 1"),
            ({"search": "error"}, HostResult(2, "", ""), "journalctl exited with status 2"),
        ],
    )
    def test_a_failure_is_an_error_also_in_a_search(self, client, journal, params, answer, detail):
        journal.answer = answer

        response = client.get(f"{API}/entries", params=params)

        assert response.status_code == 500
        assert response.json()["detail"] == CANNOT_READ + detail

    def test_a_journalctl_that_reads_too_long_is_ended_on_the_host(self, client, journal):
        # A search for a word that a large journal does not have reads all of it and prints nothing.
        journal.reads_for = 600

        response = client.get(f"{API}/entries", params={"search": "no-such-word"})

        assert response.status_code == 500
        assert response.json()["detail"] == DID_NOT_ANSWER
        assert journal.results == [HostResult(124, "", "")]  # timeout(1) ended it after 30 seconds
        assert journal.still_reading == []

    def test_a_host_command_that_does_not_answer_in_time_is_an_error(self, client, journal, monkeypatch):
        def hanging(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

        monkeypatch.setattr(host_module, "run_on_host", hanging)

        response = client.get(f"{API}/entries", params={"search": "error"})

        assert response.status_code == 500
        assert response.json()["detail"] == DID_NOT_ANSWER

    def test_a_host_command_that_cannot_start_is_an_error(self, client, journal, monkeypatch):
        def no_nsenter(cmd, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "nsenter")

        monkeypatch.setattr(host_module, "run_on_host", no_nsenter)

        response = client.get(f"{API}/entries")

        assert response.status_code == 500
        assert response.json()["detail"].startswith(CANNOT_READ)
        assert "nsenter" in response.json()["detail"]


class TestValues:
    """Values are checked before anything reaches the host (ADR-0037)."""

    @pytest.mark.parametrize(
        "params,status,detail",
        [
            ({"source": "syslog"}, 400, "Unknown log source: syslog"),
            ({"source": "supervisor"}, 400, "Unknown log source: supervisor"),
            ({"source": "caddy"}, 400, "Unknown log source: caddy"),
            ({"priority": "verbose"}, 422, None),
            ({"priority": "4"}, 422, None),
            ({"since": "yesterday"}, 400, None),
            ({"since": "--until=now"}, 400, None),
            ({"since": "1 week ago"}, 400, None),
            ({"search": "tab\there"}, 400, "The search cannot contain control characters"),
            ({"search": "x" * 257}, 422, None),
            ({"limit": 0}, 422, None),
            ({"limit": 1001}, 422, None),
        ],
    )
    def test_a_value_the_api_does_not_take_is_refused_before_the_host(self, client, journal, params, status, detail):
        response = client.get(f"{API}/entries", params=params)

        assert response.status_code == status, response.text
        if detail is not None:
            assert response.json()["detail"] == detail
        if params.get("since"):
            assert response.json()["detail"].startswith(f"Unknown time range: {params['since']}")
        assert journal.calls == []

    def test_the_longest_search_is_taken(self, client, journal):
        assert messages(client, search="x" * 256) == []


class TestRoles:
    def test_a_regular_user_cannot_read_the_logs(self, journal):
        try:
            client = _client(SimpleNamespace(id=2, username="alice", is_admin=False, is_active=True))
            for path in ("/sources", "/entries"):
                assert client.get(f"{API}{path}").status_code == 403, path
        finally:
            app.dependency_overrides.pop(verify_token, None)
        assert journal.calls == []


# ---------------------------------------------------------------------------
# The host tools and the E2E fakes
# ---------------------------------------------------------------------------


def test_the_host_tools_name_journalctl():
    assert host_module.HOST_TOOLS["journalctl"] == "systemd"


class TestLegacyRoutes:
    """GET /logs/dmesg and /logs/journal read like /entries until system.no-legacy-log-endpoints."""

    def test_they_read_the_journal_with_their_values(self, client, journal):
        assert messages_of(client, "/dmesg", limit=2) == [OOM, EXT4]
        assert messages_of(client, "/journal", limit=5, priority="err") == [OOM, USB]
        assert journal.calls == [journalctl("dmesg", lines=2),
                                 journalctl("journal", lines=5, filters=("--priority=err",))]

    def test_the_journal_route_takes_no_unit(self):
        # unit is removed (ADR-0257): the route does not offer a filter that it would ignore.
        parameters = app.openapi()["paths"][f"{API}/journal"]["get"]["parameters"]

        assert sorted(parameter["name"] for parameter in parameters) == ["limit", "priority"]


def messages_of(client, path: str, **params) -> list[str]:
    response = client.get(f"{API}{path}", params=params)
    assert response.status_code == 200, response.text
    return [entry["message"] for entry in response.json()["entries"]]


def test_the_e2e_fakes_answer_with_the_servers_texts():
    """webui/tests/logs.spec.ts answers the tab's requests itself (route fakes): with the server's
    sources and its answer for a journalctl that did not answer in time, word for word (ADR-0189)."""
    spec = (REPO / "webui" / "tests" / "logs.spec.ts").read_text(encoding="utf-8")
    for source in SOURCES:
        for text in (source["name"], source["label"], source["description"]):
            assert text in spec, text
    assert f"{logs.CANNOT_READ}: journalctl did not answer within {logs.JOURNAL_TIMEOUT} seconds" in spec
