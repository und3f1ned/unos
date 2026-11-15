"""FakeSambaHost (ADR-0189): an in-memory host behind ``src.host.run_on_host``.

Install it with ``monkeypatch.setattr(src.host, "run_on_host", fake.run)`` (or
``install_fake_samba_host``, which also blocks real subprocesses). It follows
the FakeHost pattern of test_pool_persistence.py: every command is recorded in
``calls`` (argv, stdin), the ones that change the host also in ``mutations``,
and a command it does not know fails the test.

It emulates the account commands of shares/accounts.py (getent, groupadd, useradd,
userdel, usermod -l, smbpasswd, pdbedit -L, -x and the account policy of -P, smbstatus,
smbcontrol <pid> shutdown, sync), the
smb.conf commands of an apply (mkdir /run/samba, testparm, smbcontrol
reload-config and close-share, systemctl reload smbd), ``cat -- /etc/fstab``
and the tool check of ``host.missing_host_binaries``. ``findmnt`` and
``btrfs`` are not emulated: a test that creates a share folder patches
``pools._mounted_pool_target``.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Iterable

from src import host as host_module
from src.host import HostCommandError, HostResult
from src.shares.accounts import HostAccount

DATA_UUID = "3a1c9f6e-1234-4c3b-9b0a-8f3c2a1b4c5d"
DEFAULT_FSTAB = (
    "# /etc/fstab: static file system information.\n"
    "# unos-pool:data (managed by UNOS)\n"
    f"UUID={DATA_UUID} /mnt/pools/data btrfs "
    "defaults,noatime,nofail,x-systemd.device-timeout=15s,x-systemd.before=docker.service 0 0\n"
)

# The parameters that UNOS renders, in [global] and in share sections (the testparm
# allowlist; ADR-0281, ADR-0088, ADR-0275, ADR-0342, ADR-0380).
SMB_CONF_KEYS = frozenset({
    "workgroup", "server string", "server role", "security", "passdb backend", "map to guest",
    "restrict anonymous", "guest account", "invalid users", "hosts allow", "server min protocol",
    "ntlm auth", "server signing", "server smb encrypt", "smb3 unix extensions",
    "access based share enum", "load printers", "printing", "printcap name", "disable spoolss",
    "usershare max shares", "unix password sync", "pam password change", "obey pam restrictions",
    "check password script",
    "cache directory", "log file", "max log size", "log level", "logging", "debug timestamp", "dns proxy",
    "disable netbios", "smb ports", "multicast dns register",
    "path", "comment", "browseable", "read only", "guest ok", "valid users", "write list",
    "available", "veto files", "force group", "create mask", "force create mode",
    "directory mask", "force directory mode",
    "smb encrypt", "vfs objects", "fruit:time machine", "fruit:aapl", "fruit:nfs_aces",
    "shadow:mountpoint", "shadow:snapdir", "shadow:snapprefix", "shadow:delimiter", "shadow:format",
})

# The script of host.missing_host_binaries (the tool check).
_TOOL_CHECK = 'for c do command -v -- "$c" >/dev/null 2>&1 || printf "%s\\n" "$c"; done'


def _forbidden_subprocess(*args, **kwargs):
    raise AssertionError(f"test tried to run a real command: {args!r}")


class FakeSambaHost:
    """In-memory host accounts, groups, Samba passdb and smbd sessions."""

    # Commands that only read (mutating_calls() leaves them out).
    READS = ("getent", "testparm", "smbstatus", "cat")

    def __init__(self) -> None:
        self.passwd: dict[str, HostAccount] = {}
        for name, uid, gid, gecos, home, shell in (
            ("root", 0, 0, "root", "/root", "/bin/bash"),
            ("www-data", 33, 33, "www-data", "/var/www", "/usr/sbin/nologin"),
            ("sshd", 989, 65534, "", "/run/sshd", "/usr/sbin/nologin"),
            ("nobody", 65534, 65534, "nobody", "/nonexistent", "/usr/sbin/nologin"),
        ):
            self.passwd[name] = HostAccount(name, uid, gid, gecos, home, shell)
        self.groups: dict[str, tuple[int, list[str]]] = {
            "root": (0, []),
            "sudo": (27, []),
            "users": (100, []),
            "sambashare": (987, []),
            "docker": (989, []),
            "nogroup": (65534, []),
        }
        # lower-cased name -> {"name": stored name, "password": str, "uid": int}
        self.passdb: dict[str, dict] = {}
        # Samba's account policies (pdbedit -P), with Samba's defaults: a fresh host keeps no
        # password history and locks no account (the lockout's times are 30 minutes each).
        self.policies: dict[str, int] = {"password history": 0, "lockout duration": 30, "reset count minutes": 30,
                                         "bad lockout attempt": 0}
        self.sessions: list[dict] = []
        self.fstab = DEFAULT_FSTAB
        self.busy: dict[str, int] = {}
        self.missing: set[str] = set()
        self.fail: dict[tuple[str, ...], tuple[int, str]] = {}
        self.testparm_result: tuple[int, str] = (0, "")
        self.check_testparm_keys = True
        self.calls: list[tuple[list[str], str | None]] = []
        self.mutations: list[list[str]] = []
        self.reloads = 0
        self.closed_shares: list[str] = []
        self._next_pid = 40000
        self._lock = threading.Lock()
        self._pauses: dict[tuple[str, ...], tuple[threading.Event, threading.Event]] = {}

    # -- building the host --------------------------------------------------

    def add_account(self, name, uid, *, gid=70000, gecos="UNOS SMB user", home="/nonexistent",
                    shell="/usr/sbin/nologin") -> HostAccount:
        """A host account; by default one with the UNOS marker."""
        account = HostAccount(name, uid, gid, gecos, home, shell)
        self.passwd[name] = account
        return account

    def add_group(self, name, gid, members=()) -> None:
        self.groups[name] = (gid, list(members))

    def add_smb_group(self) -> None:
        self.add_group("unos-smb", 70000)

    def add_passdb(self, name, password="x", *, uid=None) -> None:
        """A passdb entry stored as ``name`` (Samba keys it by the lower-cased name)."""
        if uid is None:
            account = self.passwd.get(name)
            uid = account.uid if account else 0
        self.passdb[name.lower()] = {"name": name, "password": password, "uid": uid}

    def pause_at(self, *prefix: str) -> tuple[threading.Event, threading.Event]:
        """The next command starting with ``prefix`` sets ``reached`` and waits for ``release``
        (it runs in a worker thread, so the event loop goes on)."""
        reached, release = threading.Event(), threading.Event()
        self._pauses[tuple(prefix)] = (reached, release)
        return reached, release

    def add_session(self, username: str, shares: Iterable[str] = ()) -> int:
        """An smbd process serving a session of ``username`` with tree connects to ``shares``."""
        with self._lock:
            pid = self._next_pid
            self._next_pid += 1
        self.sessions.append({"pid": pid, "username": username, "shares": list(shares)})
        return pid

    # -- views ----------------------------------------------------------------

    def session_pids(self, username: str | None = None) -> list[int]:
        return [s["pid"] for s in self.sessions if username is None or s["username"] == username]

    def argvs(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls]

    def commands(self, *prefix: str) -> list[list[str]]:
        """The calls whose argv starts with ``prefix``."""
        return [argv for argv, _ in self.calls if tuple(argv[: len(prefix)]) == prefix]

    def mutating_calls(self) -> list[list[str]]:
        """Every call except getent, ``pdbedit -L``, ``pdbedit -P <policy>`` without ``-C``, testparm,
        smbstatus, cat and the tool check."""
        result = []
        for argv, _ in self.calls:
            if (
                argv[0] in self.READS
                or argv[:2] == ["pdbedit", "-L"]
                or (argv[:2] == ["pdbedit", "-P"] and len(argv) == 3)
                or self._is_tool_check(argv)
            ):
                continue
            result.append(argv)
        return result

    def assert_no_secret(self, secret: str) -> None:
        """``secret`` is in no argv element (stdin is where passwords go)."""
        for argv, _ in self.calls:
            for part in argv:
                assert secret not in part, f"secret in argv: {argv[0]}"

    def stdin_of(self, *prefix: str) -> list[str | None]:
        return [stdin for argv, stdin in self.calls if tuple(argv[: len(prefix)]) == prefix]

    # -- dispatch -------------------------------------------------------------

    @staticmethod
    def _result(returncode=0, stdout="", stderr=""):
        return HostResult(returncode, stdout, stderr)

    @staticmethod
    def _is_tool_check(argv) -> bool:
        return argv[:2] == ["/bin/sh", "-c"] and len(argv) >= 4 and argv[2] == _TOOL_CHECK and argv[3] == "sh"

    def run(self, cmd, *, timeout=60, check=False, input=None, errors=None):
        argv = list(cmd)
        assert argv and all(isinstance(part, str) for part in argv), argv
        with self._lock:
            self.calls.append((argv, input))
            pause = next((key for key in self._pauses if tuple(argv[: len(key)]) == key), None)
            reached, release = self._pauses.pop(pause) if pause else (None, None)
        if reached is not None:
            reached.set()
            assert release.wait(10), f"{argv[0]} was paused and never released"
        result = None
        for prefix, (code, stderr) in self.fail.items():
            if tuple(argv[: len(prefix)]) == prefix:
                result = self._result(code, "", stderr)
                break
        if result is None:
            if argv[0] in self.missing:
                result = self._result(127, "", f"nsenter: failed to execute {argv[0]}: No such file or directory")
            else:
                result = self._dispatch(argv, input)
        if check and not result.ok:
            raise HostCommandError(argv, result.returncode, result.stderr)
        return result

    def _mutation(self, argv) -> None:
        with self._lock:
            self.mutations.append(argv)

    def _dispatch(self, argv, input):
        prog = argv[0]
        if self._is_tool_check(argv):
            names = argv[4:]
            return self._result(stdout="".join(f"{n}\n" for n in names if n in self.missing))
        if prog == "getent":
            return self._getent(argv)
        if prog == "groupadd":
            return self._groupadd(argv)
        if prog == "useradd":
            return self._useradd(argv)
        if prog == "userdel":
            return self._userdel(argv)
        if prog == "usermod":
            return self._usermod(argv)
        if prog == "smbpasswd":
            return self._smbpasswd(argv, input)
        if prog == "pdbedit":
            return self._pdbedit(argv)
        if prog == "smbstatus":
            assert argv == ["smbstatus", "--json"], argv
            return self._result(stdout=self._smbstatus_json())
        if prog == "smbcontrol":
            return self._smbcontrol(argv)
        if argv in (["sync", "/etc"], ["sync"]):
            return self._result()
        if argv == ["cat", "--", "/etc/fstab"]:
            return self._result(stdout=self.fstab)
        if argv == ["mkdir", "-p", "-m", "0755", "/run/samba"]:
            self._mutation(argv)
            return self._result()
        if prog == "testparm":
            return self._testparm(argv)
        if argv == ["systemctl", "reload", "smbd"]:
            self._mutation(argv)
            self.reloads += 1
            return self._result()
        raise AssertionError(f"unexpected host command: {argv}")

    # -- accounts -------------------------------------------------------------

    @staticmethod
    def _passwd_line(a: HostAccount) -> str:
        return f"{a.name}:x:{a.uid}:{a.gid}:{a.gecos}:{a.home}:{a.shell}\n"

    def _getent(self, argv):
        assert len(argv) in (2, 3) and argv[1] in ("passwd", "group"), argv
        if argv[1] == "passwd":
            entries = list(self.passwd.values())
            if len(argv) == 3:
                key = argv[2]
                entries = [a for a in entries if a.name == key or (key.isdigit() and a.uid == int(key))][:1]
                if not entries:
                    return self._result(2)
            return self._result(stdout="".join(self._passwd_line(a) for a in entries))
        groups = list(self.groups.items())
        if len(argv) == 3:
            key = argv[2]
            groups = [(n, g) for n, g in groups if n == key or (key.isdigit() and g[0] == int(key))][:1]
            if not groups:
                return self._result(2)
        return self._result(stdout="".join(f"{n}:x:{gid}:{','.join(m)}\n" for n, (gid, m) in groups))

    def _groupadd(self, argv):
        assert len(argv) == 4 and argv[1] == "-g", argv
        gid, name = int(argv[2]), argv[3]
        self._mutation(argv)
        if name in self.groups:
            return self._result(9, stderr=f"groupadd: group '{name}' already exists")
        if any(g == gid for g, _ in self.groups.values()):
            return self._result(4, stderr=f"groupadd: GID '{gid}' already exists")
        self.groups[name] = (gid, [])
        return self._result()

    def _useradd(self, argv):
        args = argv[1:]
        for flag in ("-r", "-l", "-N", "-M"):
            assert flag in args, f"useradd without {flag}: {argv}"
        for flag in ("-m", "-p", "--password", "--create-home"):
            assert flag not in args, f"useradd with {flag}: {argv}"
        expected = ["-r", "-l", "-N", "-M", "-u", None, "-g", None, "-d", None, "-s", None, "-c", None, None]
        assert len(args) == len(expected), f"useradd with other arguments than create_host_account uses: {argv}"
        for want, got in zip(expected, args):
            if want is not None:
                assert got == want, f"useradd with other arguments than create_host_account uses: {argv}"
        uid, gid, home, shell, gecos, name = int(args[5]), int(args[7]), args[9], args[11], args[13], args[14]
        assert not name.startswith("-"), argv
        self._mutation(argv)
        if name in self.passwd:
            return self._result(9, stderr=f"useradd: user '{name}' already exists")
        if any(a.uid == uid for a in self.passwd.values()):
            return self._result(4, stderr=f"useradd: UID {uid} is not unique")
        if not any(g == gid for g, _ in self.groups.values()):
            return self._result(6, stderr=f"useradd: group '{gid}' does not exist")
        self.passwd[name] = HostAccount(name, uid, gid, gecos, home, shell)
        return self._result()

    def _userdel(self, argv):
        assert not {"-r", "-f", "--remove", "--force"} & set(argv[1:]), f"userdel with -r or -f: {argv}"
        assert len(argv) == 2, argv
        name = argv[1]
        self._mutation(argv)
        if name not in self.passwd:
            return self._result(6, stderr=f"userdel: user '{name}' does not exist")
        if self.busy.get(name, 0) > 0:
            self.busy[name] -= 1
            return self._result(8, stderr=f"userdel: user {name} is currently used by process 4242")
        del self.passwd[name]  # the entry only: never a group
        return self._result()

    def _usermod(self, argv):
        """``usermod -l NEW OLD`` (a rename, ADR-0368): the account keeps its uid, groups and GECOS; busy
        (8) while ``busy`` counts down, as usermod refuses a rename while a process of the user runs."""
        assert argv[:2] == ["usermod", "-l"] and len(argv) == 4, f"usermod with other arguments than -l: {argv}"
        new, old = argv[2], argv[3]
        assert not new.startswith("-") and not old.startswith("-"), argv
        self._mutation(argv)
        if old not in self.passwd:
            return self._result(6, stderr=f"usermod: user '{old}' does not exist")
        if self.busy.get(old, 0) > 0:
            self.busy[old] -= 1
            return self._result(8, stderr=f"usermod: user {old} is currently used by process 4242")
        if new in self.passwd:
            return self._result(9, stderr=f"usermod: user '{new}' already exists")
        account = self.passwd.pop(old)
        self.passwd[new] = HostAccount(new, account.uid, account.gid, account.gecos, account.home, account.shell)
        return self._result()

    def _smbpasswd(self, argv, input):
        assert argv[:3] == ["smbpasswd", "-s", "-a"] and len(argv) == 4, argv
        name = argv[3]
        self._mutation(argv)
        account = self.passwd.get(name)
        if account is None:
            return self._result(1, stderr=f"Failed to find entry for user {name}.\nFailed to add entry for user {name}.")
        lines = (input or "").split("\n")
        if len(lines) != 3 or lines[2] != "" or lines[0] != lines[1]:
            return self._result(1, stderr="Mismatch - password unchanged.\nUnable to get new password.")
        self.passdb[name.lower()] = {"name": name, "password": lines[0], "uid": account.uid}
        return self._result(stdout=f"Added user {name}.\n")

    # What pdbedit says about an account policy (source3/utils/pdbedit.c).
    _POLICY_DESCRIPTIONS = {
        "password history": "Length of Password History Entries (0 = off)",
        "lockout duration": "Lockout duration in minutes (default: 30, -1 => forever)",
        "reset count minutes": "Reset time after lockout in minutes (default: 30)",
        "bad lockout attempt": "Lockout users after bad logon attempts (default: 0 => off)",
    }

    def _policy(self, argv):
        """``pdbedit -P <policy>`` prints the value; with ``-C <value>`` it sets it, writing even an
        unchanged value."""
        name = argv[2]
        if name not in self.policies:
            return self._result(1, stderr="No account policy by that name!\n")
        lines = [f'account policy "{name}" description: {self._POLICY_DESCRIPTIONS[name]}']
        if len(argv) == 3:
            lines.append(f'account policy "{name}" value is: {self.policies[name]}')
            return self._result(stdout="\n".join(lines) + "\n")
        assert len(argv) == 5 and argv[3] == "-C" and argv[4].isdigit(), argv
        self._mutation(argv)
        lines.append(f'account policy "{name}" value was: {self.policies[name]}')
        self.policies[name] = int(argv[4])
        lines.append(f'account policy "{name}" value is now: {self.policies[name]}')
        return self._result(stdout="\n".join(lines) + "\n")

    def _pdbedit(self, argv):
        if argv == ["pdbedit", "-L"]:
            return self._result(stdout="".join(
                f"{e['name']}:{e['uid']}:UNOS SMB user\n" for e in self.passdb.values()
            ))
        if argv[:2] == ["pdbedit", "-P"] and len(argv) >= 3:
            return self._policy(argv)
        assert argv[:3] == ["pdbedit", "-x", "-u"] and len(argv) == 4, argv
        self._mutation(argv)
        if self.passdb.pop(argv[3].lower(), None) is None:
            return self._result(255, stderr=f"Username not found!\nFailed to delete entry for user {argv[3]}.")
        return self._result(stdout="")

    # -- smbd -----------------------------------------------------------------

    def _smbstatus_json(self) -> str:
        sessions, tcons = {}, {}
        for index, session in enumerate(self.sessions):
            server_id = {"pid": str(session["pid"]), "task_id": "0", "vnn": "4294967295",
                         "unique_id": str(11259836127046405537 + index)}
            session_id = str(3254418735 + index)
            account = self.passwd.get(session["username"])
            sessions[session_id] = {
                "session_id": session_id,
                "server_id": server_id,
                "uid": account.uid if account else 65534,
                "gid": account.gid if account else 65534,
                "username": session["username"],
                "groupname": "unos-smb",
                "remote_machine": "10.1.1.20",
                "hostname": "ipv4:10.1.1.20:50122",
                "session_dialect": "SMB3_11",
                "encryption": {"cipher": "-", "degree": "none"},
                "signing": {"cipher": "AES-128-GMAC", "degree": "full"},
                "channels": {},
            }
            for share in session["shares"]:
                tcon_id = str(2983 + len(tcons))
                tcons[tcon_id] = {"service": share, "server_id": server_id, "tcon_id": tcon_id,
                                  "session_id": session_id, "machine": "10.1.1.20"}
        return json.dumps({
            "timestamp": "2026-11-06T16:20:31.512345+0100",
            "version": "4.22.6-Debian-4.22.6+dfsg-0+deb13u1",
            "smb_conf": "/etc/samba/smb.conf",
            "sessions": sessions,
            "tcons": tcons,
            "open_files": {},
        })

    def _smbcontrol(self, argv):
        assert "close-denied-share" not in argv, f"close-denied-share is never used: {argv}"
        self._mutation(argv)
        if argv == ["smbcontrol", "all", "reload-config"]:
            self.reloads += 1
            return self._result()
        if argv[:3] == ["smbcontrol", "smbd", "close-share"] and len(argv) == 4:
            self.closed_shares.append(argv[3])
            for session in self.sessions:
                session["shares"] = [s for s in session["shares"] if s.lower() != argv[3].lower()]
            return self._result()
        if len(argv) == 3 and argv[2] == "shutdown" and argv[1].isdigit():
            pid = int(argv[1])
            if pid not in self.session_pids():
                return self._result(1, stderr=f"Can't find pid {pid}")
            self.sessions = [s for s in self.sessions if s["pid"] != pid]
            return self._result()
        raise AssertionError(f"unexpected smbcontrol command: {argv}")

    def _testparm(self, argv):
        assert argv[:3] == ["testparm", "-s", "--suppress-prompt"] and len(argv) == 4, argv
        code, output = self.testparm_result
        if code == 0 and self.check_testparm_keys:
            with open(argv[3], encoding="utf-8", errors="surrogateescape") as f:
                for line in f.read().split("\n"):
                    stripped = line.strip()
                    if not stripped or stripped.startswith(("#", ";", "[")):
                        continue
                    key = stripped.split("=", 1)[0].strip().lower()
                    assert key in SMB_CONF_KEYS, f"smb.conf parameter that UNOS does not render: {line!r}"
        return self._result(code, stdout="Loaded services file OK.\n" if code == 0 else "", stderr=output)


def install_fake_samba_host(monkeypatch) -> FakeSambaHost:
    """Route every host command to a new FakeSambaHost and block real subprocesses."""
    fake = FakeSambaHost()
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    monkeypatch.setattr(subprocess, "run", _forbidden_subprocess)
    monkeypatch.setattr(host_module, "run_in_container", _forbidden_subprocess)
    return fake
