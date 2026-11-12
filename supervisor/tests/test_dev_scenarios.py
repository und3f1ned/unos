"""The scenario checks of the dev device (scripts/dev/scenario.sh and scenario.py, ADR-0304),
tested without the device.

FakeNas stands in for the dev device: the API that scenario.py calls through api.py's relay
(request, upload) and the host that it reads through ssh.sh (the methods of scenario.Device),
over one model in memory of a pool with its folders, trash and volumes, the shares, the UNOS
users, the SMB host accounts and Samba's passdb, the disks and the backups. Its Samba serves
sessions as the device's does for the checks: access per share, a closed tree connect when a
share's access shrinks (ADR-0103), the end of an account's sessions at a password change or a
deletion (ADR-0104), a failed sign-in that Samba counts in passdb.tdb but for one of the two
passwords that the history holds (ADR-0278), the lock of an account after 5 of them, which
refuses the right password as well and writes nothing (ADR-0334), and a password change over SAMR,
which Samba refuses without a write (ADR-0342).

Covered (the steps of the ledger entry device.scenario-checks-in-repo):
* every area (files, volumes, smart, backups, smb, smbconf, shares, scrub, offsite, restore) passes against
  the fake, prints a PASS line with its SD MiB for each step and a summary, and leaves the device as it
  found it;
* the volumes check makes a snapshot of its volume and restores it: the copy restore_<time> is
  listed with the volume's restored copies, its Delete is refused while a share uses it and
  removes it from the host afterwards, and the volume can be deleted then (ADR-0358); its fake
  refuses the Delete of a volume that holds a snapshot or a copy, as btrfs does;
* the restore check (ADR-0341) restores only the backup it made, twice: its fake restore keeps a
  restored SMB account only with the live password and uid (ADR-0141), keeps the live next uid
  (ADR-0109), removes stale host accounts and renders smb.conf; its helper on the device lists
  the members of a plain and of an encrypted backup as src/backup/router.py writes them;
* the smbconf check (ADR-0309) shows what an smb.conf edited by hand does: the Shares page names
  the file before an apply, the apply copies it byte for byte to a new
  smb.conf.unos-replaced-<time> and keeps the earlier copies (ADR-0360), a share
  that is not served gets access only with a new folder, and a restart of the supervisor writes
  no file in /etc/samba; its hand edit runs without the read-only namespace, and its helpers on
  the device touch only a check's own section and share folder;
* the shares check shows the device checks of shares.acl-aware-parent-check (step 3),
  shares.repair-permissions, shares.self-activation (the user's own calls carry its own token,
  which the clean-up deletes) and shares.read-only-guest-share, says so in place of the step of
  mandatory signing where the device has other guest shares and in place of the ACL step without
  setfacl; its entries helper makes the check's entries with their owners and modes and touches
  only a check's own folders;
* a failure at any API call of a step, before or after the call changed something (a lost
  answer), an interruption, an output that can no longer be written or a refusal of the device
  still removes what the check made, and the check fails; only a backup whose answer was lost is
  named and left; a failed clean-up, also one that meets an answer of an unexpected shape, is
  reported and fails the check; signals wait while the clean-up runs;
* the check refuses to start beside leftovers of an earlier one, and scenario.py refuses to run
  without the lock of scenario.sh;
* the defects the checks exist for fail them: a share access that shrinks without closing the
  open tree connects, a password change that leaves the sessions open, a share folder or an
  upload with the wrong mode, a volume that the File Manager deletes, a password in a log, an
  apply that drops a section added by hand without a copy, access granted to a share that is
  not served, a status that misses an smb.conf that is not UNOS's, a restart that writes
  smb.conf; a new share folder below a folder of another account, a sticky folder that excuses a
  share folder of another account, a Repair permissions that follows a link, opens a private
  entry, drops an ACL entry or repairs a share that is not served, SMB that takes a password of
  7 characters or keeps the account the user turned off, a guest share without the warning,
  an anonymous write, guest lines that stay after the last guest share, a password change over
  SAMR that Samba takes, or refuses while it writes passdb.tdb or stores the new password;
* no temporary password reaches the output, a command line or the environment: scenario.Device
  sends them on standard input, and the SMB helper keeps them in a file of mode 600 in RAM that
  it deletes, or gives them to smbpasswd -r on its standard input;
* every call the checks make is a route of the supervisor in this tree, with a body its model
  accepts (the contract with the API);
* the scripts that run on the device parse; the SMB helper's session and share-list protocol
  works against a stand-in smbclient, with signing required except for the null session, and its
  password change against a stand-in smbpasswd; the
  subvolume helper touches only a check's own folder;
* scenario.sh holds the device lock and reports the SD writes of each check, also of one that a
  signal ended;
* the feature-pass skill names the check of every host-level entry of the ledger.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import textwrap
import types
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from .test_dev_scripts import DEV, REPO_ROOT, STUB_FLOCK, STUB_MOUNT, STUB_UNSHARE, Device as StubDevice, bashes

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def load_scenario() -> types.ModuleType:
    """scripts/dev/scenario.py as a module (it imports api.py from its folder)."""
    if str(DEV) not in sys.path:
        sys.path.insert(0, str(DEV))
    path = DEV / "scenario.py"
    module = types.ModuleType("scripts_dev_scenario")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


scenario = load_scenario()
Answer = scenario.relay.Answer

ROOT = "/mnt/pools/data"
SMB_GROUP = "unos-smb"
DISK = "sda"
LEFTOVER = re.compile(r"unoscheck[0-9a-f]{6}")
HEADER = "# Generated by UNOS from its database.\n"
# The texts of GET /shares/smb-status that the smbconf check reads, as UNOS words them
# (test_share_api.py asserts them against the real code).
CONF_REPLACED = ("smb.conf was replaced or removed after UNOS wrote it; it is written again at the next change or "
                 "restart, and SMB cannot be turned on until then")
COPIED = "smb.conf had shares that UNOS did not create ({}); they are kept in {}: create them again on the Shares page"
EARLIER_COPIES = "; earlier copies (UNOS keeps the newest 10): {}"
# The warnings of a share change as UNOS words them (GUEST_WARNING and PERMISSIONS_WARNING of
# src/shares/service.py; test_the_shares_check_expects_unos_s_texts compares).
GUEST_WARNING = (scenario.GUEST_WARNS + ", and its name is visible to anyone on your network. Guests can never write. "
                 "While a guest share exists, connections to this server are not required to be signed. Windows 11 "
                 "refuses guest access by default, and guest connections are neither signed nor encrypted.")
PERMISSIONS_WARNING = ("The folder's permissions are not set up for SMB users (group unos-smb, mode 2770): use Repair "
                       "permissions")
# What Samba 4.22's smbpasswd -r prints when the server refuses the change (remote_password_change of
# source3/libsmb/passchange.c): the status's description of MS-ERREF (get_friendly_nt_error_msg,
# libcli/util/ntstatus_err_table.txt), here the one of NT_STATUS_PASSWORD_RESTRICTION, which the check
# password script of UNOS's [global] gives (ADR-0342).
SAMR_REFUSED = "machine 127.0.0.1 rejected to change the password with error: "
SAMR_REFUSAL = (SAMR_REFUSED + "When trying to update a password, this status indicates that some password update rule "
                "has been violated. For example, the password might not meet length criteria.")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def conf_sections(text: str) -> list[tuple[str, str]]:
    """(name, the section's lines) of smb.conf's sections."""
    found, name, lines = [], None, []
    for line in text.splitlines():
        if line.startswith("["):
            if name is not None:
                found.append((name, "\n".join(lines)))
            name, lines = line.strip("[]"), []
        elif name is not None:
            lines.append(line)
    if name is not None:
        found.append((name, "\n".join(lines)))
    return found


# --- the fake device --------------------------------------------------------------------------


class Node:
    def __init__(self, kind: str, mode: int, owner: str = "root", group: str = "root", content: str = "",
                 acl: list[str] | None = None) -> None:
        self.kind = kind  # dir, file, subvolume (a volume, or one made by the files check) or link
        self.mode = mode
        self.owner = owner
        self.group = group
        self.content = content
        self.acl = acl  # the named entries of a POSIX ACL ("user:nobody:r--"), whose mask is the group bits

    def facts(self) -> tuple:
        return (self.kind, self.mode, self.owner, self.group, self.content, self.acl)

    def acl_lines(self) -> list[str]:
        """What getfacl -c -E prints: the owner, the named entries, the group, the mask, other."""
        def bits(n: int) -> str:
            return "".join(c if n & b else "-" for c, b in (("r", 4), ("w", 2), ("x", 1)))
        mode = self.mode
        if not self.acl:
            return [f"user::{bits(mode >> 6)}", f"group::{bits(mode >> 3)}", f"other::{bits(mode)}"]
        return ([f"user::{bits(mode >> 6)}"] + self.acl + ["group::r--", f"mask::{bits(mode >> 3)}", f"other::{bits(mode)}"])


# The accounts of the fake host: name -> uid.
UIDS = {"root": 0, "nobody": 65534}


class Refused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Interrupt(BaseException):
    """Raised by FakeNas in place of KeyboardInterrupt; the tests check the same handling."""


class FakeSmbSession:
    """One smbclient session to a share, as REMOTE_SMB's session mode gives it to scenario.py."""

    def __init__(self, nas: FakeNas, user: str, password: str, share: str, guest: bool = False) -> None:
        self.nas = nas
        self.user = user or "nobody"  # a guest works as the guest account
        self.share = share
        self.local: dict[str, str] = {"data.txt": scenario.DATA, "data2.txt": scenario.DATA2}
        self.problem = None
        if guest:  # a null session: only a guest share, read-only for guests (ADR-0096), and only
            # while [global] lets anonymous sessions in (restrict anonymous = 1, ADR-0339)
            admitted = (share in nas.guests_rendered() and "guest-refused" not in nas.bugs
                        and "restrict anonymous = 2" not in nas.installed)
            self.level = ("write" if "anonymous-writes" in nas.bugs else "read") if admitted else None
        else:
            status = nas.sign_in(user, password)
            if status is not None:
                self.problem = f"session setup failed: {status}"
            self.level = nas.level(share, user)  # what the tree connect was granted
        if self.problem is None and self.level is None:
            self.problem = "tree connect failed: NT_STATUS_ACCESS_DENIED"
        self.tree_closed = False
        self.killed = False
        self.ended = self.problem is not None
        nas.sessions.append(self)

    def __enter__(self) -> FakeSmbSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.ended = True
        if self in self.nas.sessions:
            self.nas.sessions.remove(self)

    def run(self, *commands: str):
        if self.ended:
            return scenario.SmbResult(False, self.problem or "", True)
        out = []
        for command in commands:
            out.append(self.command(command))
            if self.killed:
                self.ended = True
                return scenario.SmbResult(False, "\n".join(out), True)
        text = "\n".join(out)
        return scenario.SmbResult("NT_STATUS_" not in text, text, False)

    def command(self, line: str) -> str:
        nas = self.nas
        if line.startswith("!cat "):
            return self.local.get(line[5:], "cat: no such file")
        if self.killed:
            return "NT_STATUS_CONNECTION_DISCONNECTED"
        if self.tree_closed:
            return "NT_STATUS_NETWORK_NAME_DELETED"
        level = self.level
        folder = nas.shares[self.share]["path"]
        words = line.split()
        if words[0] == "ls":
            return "\n".join(sorted(p.rsplit("/", 1)[1] for p in nas.children(folder)))
        if words[0] == "get":
            node = nas.fs.get(f"{folder}/{words[1]}")
            if node is None:
                return "NT_STATUS_OBJECT_NAME_NOT_FOUND opening remote file"
            self.local[words[2]] = node.content
            return f"getting file \\{words[1]}"
        if level != "write":
            return f"NT_STATUS_ACCESS_DENIED opening remote file \\{words[-1]}"
        if words[0] == "put":
            path = f"{folder}/{words[2]}"
            if os.path.dirname(path) not in nas.fs:
                return "NT_STATUS_OBJECT_PATH_NOT_FOUND"
            old = nas.fs.get(path)
            nas.fs[path] = Node("file", 0o660, old.owner if old else self.user, SMB_GROUP, self.local[words[1]])
            return f"putting file {words[1]} as \\{words[2]}"
        if words[0] == "mkdir":
            nas.fs[f"{folder}/{words[1]}"] = Node("dir", 0o2770, self.user, SMB_GROUP)
            return ""
        raise AssertionError(f"the fake smbclient does not know {line!r}")


def smb_password_problem(password: str) -> str | None:
    """smb_password_problem of src/shares/constants.py without the list of common passwords."""
    if len(password) < 8:
        return scenario.SMB_TOO_SHORT
    if len(password.encode()) > 72:
        return "SMB passwords must not be longer than 72 bytes (UTF-8)"
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        return scenario.SMB_CONTROL
    return None


class FakeUserApi:
    """api.DeviceApi.as_user on FakeNas: a regular user's calls with its own token, which the relay
    gets at the first call, a login with the user's password (else ApiError, as the relay's), and
    keeps in the device's RAM under its token cache until forget_token."""

    def __init__(self, nas: FakeNas, username: str, password: str, cache: str) -> None:
        self.nas = nas
        self.username = username
        self.password = password
        self.cache = cache
        self.secrets = list(nas.secrets)

    def request(self, method, path, json_body=None, params=None, auth=True, upload=None) -> Answer:
        nas = self.nas
        known = [u for u in nas.users.values() if u["username"] == self.username]
        if not known or (self.cache not in nas.tokens and known[0].get("password") != self.password):
            raise scenario.relay.ApiError(f"login as {self.username} failed: HTTP 401")
        nas.tokens.add(self.cache)
        nas.identity = self.username
        try:
            return nas.request(method, path, json_body=json_body, params=params, auth=auth, upload=upload)
        finally:
            nas.identity = None

    def forget_token(self) -> None:
        self.nas.tokens.discard(self.cache)
        self.nas.forgotten.append(self.cache)


class FakeNas:
    """The dev device for scenario.py: request() and upload() as api.DeviceApi has them, and the
    methods of scenario.Device. fail_at and interrupt_at name an API call (0-based) that answers
    500 or raises Interrupt; refuse_device names a device method that fails, and refuse_after a
    device command ("entries sticky") that fails after it made its thing; bugs names defects to
    inject."""

    def __init__(self) -> None:
        self.fs: dict[str, Node] = {
            "/mnt/pools": Node("dir", 0o755),
            ROOT: Node("dir", 0o755),
            f"{ROOT}/shared": Node("dir", 0o2770, "root", SMB_GROUP),
            f"{ROOT}/shared/old.txt": Node("file", 0o660, "root", SMB_GROUP, "old"),
            f"{ROOT}/media": Node("subvolume", 0o755),
            f"{ROOT}/.unos-trash": Node("dir", 0o700),
        }
        self.trash: dict[str, str] = {}  # the item's folder in a trash -> the path it was deleted from
        self.trash_entries = 100
        self.quota_of: dict[str, int] = {}
        self.users = {1: {"username": "admin", "is_admin": True}, 2: {"username": "test", "is_admin": False}}
        self.next_user = 3
        self.accounts: dict[str, int] = {}  # marked host accounts: name -> uid
        self.passdb: dict[str, dict] = {}
        # The SMB rows that need their password again (allowed, Samba has none), and the next uid
        # (smb_state.next_uid, ADR-0109).
        self.needed: set[str] = set()
        self.next_smb_uid = 70003
        self.shares = {"DATA": {"path": f"{ROOT}/shared", "comment": "", "access": [], "version": 1}}
        self.backups = {"unos_backup_20261107_000000.tar.gz": None}  # name -> passphrase
        # What the check's backups hold: the database (database()) and smb.conf at their time; the
        # backups restored.
        self.databases: dict[str, dict] = {}
        self.restored: list[str] = []
        self.restores = 0  # the restores of snapshots, which name their copies (restore_<time>)
        # The copies of the database that the restores keep (the newest 5), and an API token.
        self.copies: list[str] = []
        self.api_tokens = [{"id": 1, "user_id": 1, "name": "home assistant", "token_prefix": "unos_ab12", "is_active": True,
                            "permissions": "read", "created_at": "2026-11-01T09:00:00", "last_used_at": None,
                            "expires_at": None}]
        self.quotas = False
        self.sessions: list[FakeSmbSession] = []
        self.passdb_mtime = 1
        # Samba's account policies as UNOS's reconcile sets them (ADR-0278, ADR-0334), by the keys of
        # scenario.SAMBA_POLICIES.
        self.policies = {"history": "3", "duration": "5", "reset": "5", "lockout": "5"}
        self.log: list[str] = []
        self.sector_count = 1000
        self.calls: list[tuple] = []
        self.fail_at: int | None = None
        self.fail_after: int | None = None  # this call is made, then answers 500, as if its answer was lost
        self.interrupt_at: int | None = None
        self.unreadable: set[str] = set()  # sources that the scan cannot read
        self.injected = False  # whether fail_at or fail_after answered a call
        self.partitions: list[str] = []  # the partitions of the disk
        self.guest_shares: list[str] = []
        self.refuse_device: str | None = None
        self.refuse_after: str | None = None
        self.bugs: set[str] = set()
        self.secrets: list[str] = []  # api.DeviceApi's: dev.env's secrets
        # /etc/samba: smb.conf as installed (UNOS's rendering, or as edited by hand), the other
        # files, their times of change; the copies of the hand-edit helper in RAM; the problem of
        # a copy, which the supervisor keeps in memory until it restarts (ADR-0360).
        self.installed = self.smb_conf()
        self.samba = {"smb.conf.unos-import-backup-20261106_120000_000000": "[global]\n   workgroup = WORKGROUP\n",
                      "gdbcommands": "bt\n"}
        self.mtimes: dict[str, int] = {"smb.conf": 1}
        self.saved: dict[str, str] = {}
        self.replaced_problem: str | None = None
        self.restarts = 0
        # The startup reconcile's apply has run (last_apply().ok); after a restart it runs once a
        # change of SMB state waits for smb_lock, which it holds from before the first request.
        self.last_apply_ok: bool | None = True
        self.reconcile_pending = False
        # The pool's scrub as btrfs reports it: its last record, and of a running scrub the
        # requests left until btrfs records it (ADR-0313) and until it ends; the Scheduler.
        self.scrub = {"status": "finished", "last_run": "Mon Nov  9 09:00:00 2026", "errors": 0, "duration": 19}
        self.scrub_unrecorded = 0
        self.scrub_left = 0
        self.scrubs = 0
        self.pool_used = 1  # bytes of data in the pool data
        self.tasks: dict[str, dict] = {}
        self.runs: list[dict] = []
        self.task_keys: dict[str, str] = {}  # the backup tasks' passphrases (scheduled_backup_keys.json)
        # The off-device copy (ADR-0337): its settings, password and state; the repository is a folder
        # of fs (config, keys/key, snapshots/<id>); what a copy or restore that runs makes is shown by
        # GET /offsite/status after offsite_left more requests.
        self.offsite: dict | None = None
        self.offsite_password: str | None = None
        self.offsite_status: dict = {"last_copy": None, "last_restore": None}
        self.offsite_running: dict | None = None
        self.offsite_left = 0
        self.offsite_pending: tuple[str, dict] | None = None
        self.offsite_count = 0
        # The shares check: the last Repair permissions of each share, kept in the supervisor's
        # memory; whose token a call carries (FakeUserApi), the users' tokens that the relay keeps
        # and those deleted; the calls answered 422 (their bodies the API's models refuse); and a
        # device without setfacl.
        self.repairs: dict[str, dict] = {}
        self.identity: str | None = None
        self.tokens: set[str] = set()
        self.forgotten: list[str] = []
        self.invalid: set[int] = set()
        self.callers: list[str | None] = []  # whose token each call of calls carried; None: the administrator's
        self.no_acl = False

    # --- what the tests compare

    def snapshot(self) -> dict:
        return {
            "fs": {path: node.facts() for path, node in self.fs.items()},
            "trash": dict(self.trash),
            "users": copy.deepcopy(self.users),
            "accounts": dict(self.accounts),
            "passdb": sorted(self.passdb),
            "the SMB rows that need a password": sorted(self.needed),
            "shares": copy.deepcopy(self.shares),
            "backups": dict(self.backups),
            "the copies of the database": list(self.copies),
            "the API tokens": copy.deepcopy(self.api_tokens),
            "smb.conf": self.installed,
            "/etc/samba": dict(self.samba),
            "the hand edit's copies": dict(self.saved),
            "the copy's problem": self.replaced_problem,
            "a scrub runs": self.scrub["status"] == "running",
            "the Scheduler's tasks": copy.deepcopy(self.tasks),
            "the backup tasks' keys": dict(self.task_keys),
            "the off-device copy": (copy.deepcopy(self.offsite), self.offsite_password,
                                    copy.deepcopy(self.offsite_status), self.offsite_running),
            "the users' tokens in RAM": sorted(self.tokens),
        }

    # --- the model

    def children(self, folder: str) -> list[str]:
        return [p for p in self.fs if os.path.dirname(p) == folder and not p.rsplit("/", 1)[1].startswith(".")]

    def subtree(self, path: str) -> list[str]:
        return [p for p in self.fs if p == path or p.startswith(path + "/")]

    def move(self, src: str, dst: str) -> None:
        for p in sorted(self.subtree(src)):
            self.fs[dst + p[len(src):]] = self.fs.pop(p)

    def remove(self, path: str) -> None:
        for p in self.subtree(path):
            del self.fs[p]

    def volume_of(self, path: str) -> str | None:
        """The volume that holds PATH, or None for the pool's own folders."""
        parts = path.split("/")
        top = "/".join(parts[:5])
        return top if self.fs.get(top) and self.fs[top].kind == "subvolume" and path != top else None

    def level(self, share: str, user: str) -> str | None:
        info = self.shares.get(share)
        if info is None:
            return None
        for entry in info["access"]:
            if entry["username"] == user and user in self.passdb:
                return entry["level"]
        return None

    def smb_state(self, uid: int) -> dict:
        user = self.users[uid]
        if user["is_admin"]:
            return {"state": "admin", "allowed": False}
        if user["username"] in self.passdb:
            return {"state": "active", "allowed": True, "uid": self.accounts[user["username"]]}
        if user.get("smb_allowed") or user["username"] in self.needed:
            return {"state": "password_needed", "allowed": True, "uid": self.accounts.get(user["username"])}
        return {"state": "off", "allowed": False}

    def user_info(self, uid: int) -> dict:
        user = self.users[uid]
        return {"id": uid, "username": user["username"], "is_admin": user["is_admin"], "smb": self.smb_state(uid),
                "warnings": []}

    def path_problem(self, path: str) -> str | None:
        """Why a share at PATH is not served (share_path_problem), or None."""
        node = self.fs.get(path)
        if node is not None and node.kind == "link":
            return f"Share path must not contain symbolic links ({path} is one)"
        return None

    def unsafe_parent(self, path: str) -> tuple[str, str, str] | None:
        """The first folder from the pool root down to PATH's parent that other accounts can change
        (_unsafe_parent of src/shares/service.py, ADR-0153, ADR-0193): (folder, detail, commands),
        or None. A sticky folder excuses its other writers unless PATH, in it, is another account's."""
        parts = path[len(ROOT) + 1:].split("/")
        chain = [ROOT] + [f"{ROOT}/{'/'.join(parts[:i])}" for i in range(1, len(parts))]
        for folder in chain:
            node = self.fs.get(folder)
            if node is None:
                return None
            entry = self.fs.get(path) if folder == chain[-1] else None
            foreign = entry is not None and entry.owner != "root"
            sticky = bool(node.mode & 0o1000)
            writable = bool(node.mode & 0o002 or (node.mode & 0o020 and node.group != "root"))
            writable = writable and (not sticky or (foreign and "sticky-excuses-foreign" not in self.bugs))
            owner_ok = node.owner == "root" or "foreign-parent-allowed" in self.bugs
            if owner_ok and not writable:
                continue
            mode = node.mode & 0o7777
            detail = f"mode {mode:04o}" if owner_ok else f"owner uid {UIDS[node.owner]}, mode {mode:04o}"
            if writable and sticky:
                detail += f", the share folder belongs to uid {UIDS[entry.owner]}"
            chmod = f"'chmod 755 {folder}'"
            return folder, detail, chmod if owner_ok else f"'chown root:root {folder}'" + (f" and {chmod}" if writable else "")
        return None

    def unsafe_text(self, path: str, kind: str, share: str = "") -> str | None:
        """The refusal, warning or status problem of UnsafeFolder for a share at PATH, or None."""
        found = self.unsafe_parent(path)
        if found is None:
            return None
        folder, detail, commands = found
        changed = f"{folder} can be changed by other accounts ({detail})"
        if kind == "refusal":
            return (f"Share folders must be inside folders that only root can change: {changed}. Run {commands} on "
                    "the NAS, then try again.")
        if kind == "warning":
            return (f"Share folders must be inside folders that only root can change: {changed}, so they can replace "
                    f"this share's folder. Run {commands} on the NAS.")
        return f"Share {share}: {changed}, so they can replace the share's folder. Run {commands} on the NAS."

    def share_info(self, name: str) -> dict:
        info = self.shares[name]
        problem = self.path_problem(info["path"])
        access = [{**a, "smb_state": "active" if a["username"] in self.passdb else "off",
                   "effective": a["username"] in self.passdb and problem is None} for a in info["access"]]
        guest = bool(info.get("guest_ok"))
        return {"name": name, "path": info["path"], "comment": info["comment"], "browseable": True, "guest_ok": guest,
                "access": access, "version": info["version"], "served": problem is None, "path_problem": problem,
                "available": problem is None and (guest or any(a["effective"] for a in access)), "pool": "data",
                "pool_mounted": True, "permissions_ok": True, "import_notes": [], "warnings": []}

    def guests_rendered(self) -> list[str]:
        """The served shares with guest access, which give [global] its guest lines (ADR-0339)."""
        return sorted(name for name, info in self.shares.items()
                      if info.get("guest_ok") and self.path_problem(info["path"]) is None)

    def smb_conf(self) -> str:
        """UNOS's rendering: a section per served share with its active principals (root when none
        has access), each ending with UNOS's fixed lines."""
        fixed = "".join(f"   {key} = {value}\n" for key, value in scenario.UNOS_SHARE_LINES)

        def principals(info: dict) -> str:
            active = [a for a in info["access"] if a["username"] in self.passdb]
            writers = ", ".join(sorted(a["username"] for a in active if a["level"] == "write"))
            return (f"   valid users = {', '.join(sorted(a['username'] for a in active)) or 'root'}\n"
                    + (f"   write list = {writers}\n" if writers else "   write list =\n"))

        sections = "".join(f"\n[{name}]\n   path = {info['path']}\n" + principals(info) + fixed
                           for name, info in sorted(self.shares.items()) if self.path_problem(info["path"]) is None)
        # The defect guest-globals-stay keeps the guest lines once a guest share was rendered, and
        # no-guest-globals never writes them.
        guest = ((bool(self.guests_rendered()) and "no-guest-globals" not in self.bugs)
                 or ("guest-globals-stay" in self.bugs and getattr(self, "guest_once", False)))
        return (HEADER + "\n[global]\n" + "".join(f"   {key} = {values[guest]}\n" for key, values in scenario.GUEST_GLOBALS.items())
                + "   log file = /run/samba/log.samba\n   max log size = 100\n" + sections)

    def replaced_copies(self) -> list[str]:
        """The names of the copies smb.conf.unos-replaced-<time> in /etc/samba, oldest first."""
        return sorted(name for name in self.samba if scenario.REPLACED_COPY_NAME.fullmatch(name))

    def apply(self) -> None:
        """An apply after a share change (ADR-0089): share sections that UNOS did not render go to
        a new smb.conf.unos-replaced-<time> first, unless the newest copy holds these bytes, and the
        oldest copies beyond the newest 10 are deleted (ADR-0360); an unchanged rendering writes
        nothing. The defect deletes-earlier-copies keeps only the new copy."""
        self.guest_once = getattr(self, "guest_once", False) or bool(self.guests_rendered())
        old, new = self.installed, self.smb_conf()
        if old == new:
            return
        fixed = [f"   {key} = {value}" for key, value in scenario.UNOS_SHARE_LINES]
        foreign = [name for name, body in conf_sections(old)
                   if name.lower() != "global" and not all(line in body.splitlines() for line in fixed)]
        if foreign and "no-replaced-copy" not in self.bugs:
            copies = self.replaced_copies()
            if copies and self.samba[copies[-1]] == old:
                name, others = copies[-1], copies[:-1]
            else:
                self.copy_count = getattr(self, "copy_count", 0) + 1
                name = f"smb.conf.unos-replaced-20261112_1200{self.copy_count:02d}_000000"
                self.samba[name] = old
                self.mtimes[name] = 1
                others = copies[-(scenario.REPLACED_COPIES_KEPT - 1):] if "deletes-earlier-copies" not in self.bugs else []
                for gone in set(copies) - set(others):
                    del self.samba[gone]
                    self.mtimes.pop(gone, None)
            self.replaced_problem = COPIED.format(", ".join(foreign), name) + (
                EARLIER_COPIES.format(", ".join(reversed(others))) if others else "")
        self.installed = new
        self.mtimes["smb.conf"] += 1

    def status_problems(self) -> list[str]:
        problems = [f"Share {name} is not served: {problem}" for name, info in sorted(self.shares.items())
                    if (problem := self.path_problem(info["path"]))]
        problems += [text for name, info in sorted(self.shares.items()) if self.path_problem(info["path"]) is None
                     and (text := self.unsafe_text(info["path"], "problem", name))]
        if self.last_apply_ok and not self.installed.startswith(HEADER) and "status-misses-replaced-file" not in self.bugs:
            problems.append(CONF_REPLACED)
        if self.replaced_problem:
            problems.append(self.replaced_problem)
        return problems

    def end_sessions(self, user: str) -> None:
        if "sessions-stay" in self.bugs:
            return
        for session in self.sessions:
            if session.user == user:
                session.killed = True

    def close_share(self, share: str) -> None:
        if "no-close-share" in self.bugs:
            return
        for session in self.sessions:
            if session.share == share:
                session.tree_closed = True

    def sign_in(self, user: str, password: str) -> str | None:
        """Samba's check of a sign-in (source3/auth/check_samsec.c): None when it is taken, else
        its NT status. A locked account is refused before its password is checked, which writes
        nothing; a wrong password counts while a lockout is set, but for the two previous
        passwords (ADR-0278), and the count is written to passdb.tdb; the failure that reaches the
        threshold locks the account (ADR-0334). The right password sets a count back to 0, which
        is written too. Without a lockout a failure writes nothing (ADR-0310)."""
        account = self.passdb.get(user)
        if account is None:  # map to guest = Bad User while a guest share exists (ADR-0339)
            return None if self.guests_rendered() else "NT_STATUS_LOGON_FAILURE"
        if account["locked"]:
            if "locked-writes" in self.bugs:
                self.passdb_mtime += 1
            return "NT_STATUS_ACCOUNT_LOCKED_OUT"
        if account["password"] == password:
            if account["bad"]:
                account["bad"] = 0
                self.passdb_mtime += 1
            return None
        threshold = int(self.policy_values()["lockout"])
        counted = threshold > 0 and password not in account["history"]
        if counted:
            account["bad"] += 2 if "counts-twice" in self.bugs else 1
            account["locked"] = account["bad"] >= threshold and "lockout-ignored" not in self.bugs
        if (counted and "count-unwritten" not in self.bugs) or "wrong-password-writes" in self.bugs:
            self.passdb_mtime += 1
        return "NT_STATUS_LOGON_FAILURE"

    # --- the API

    def upload(self, path: str, params: dict, filename: str, content: bytes) -> Answer:
        return self.request("POST", path, params=params, upload=(filename, content.decode()))

    def as_user(self, username: str, password: str, token_cache: str) -> FakeUserApi:
        return FakeUserApi(self, username, password, token_cache)

    def request(self, method, path, json_body=None, params=None, auth=True, upload=None) -> Answer:
        index = len(self.calls)
        self.calls.append((method, path, copy.deepcopy(json_body), dict(params or {})))
        self.callers.append(self.identity)
        if index == self.interrupt_at:
            raise Interrupt()
        if index == self.fail_at:
            self.injected = True
            return Answer(500, json.dumps({"detail": "injected failure"}))
        self.log.append(f"{method} {path}")
        try:
            body = self.route(method, path, json_body, params or {}, upload)
        except Refused as e:
            return Answer(e.status, json.dumps({"detail": e.detail}))
        if index == self.fail_after:
            self.injected = True
            return Answer(500, json.dumps({"detail": "injected failure after the change"}))
        status = 200
        if isinstance(body, tuple):  # (status, body): a route that answers 202
            status, body = body
        return Answer(status, body if isinstance(body, str) else json.dumps(body))

    def route(self, method, path, body, params, upload):
        parts = path.strip("/").split("/")
        if self.identity is not None and parts[0] != "auth":  # a regular user's token (ADR-0030)
            raise Refused(403, "Admin access required")
        handler = getattr(self, "api_" + parts[0].replace("-", "_"))
        return handler(method, parts[1:], body, params, upload)

    def api_storage(self, method, parts, body, params, upload):
        if parts[:1] == ["pools"] and parts[2:] == ["scrub"]:
            return self.api_scrub(method, parts[1])
        if parts == ["disks"] and self.partitions:
            return [{"name": DISK, "path": f"/dev/{DISK}", "size": 640, "rotational": True, "type": "disk",
                     "partitions": [{"name": p, "path": f"/dev/{p}", "size": 1} for p in self.partitions]}]
        if parts == ["pools"]:
            return [{"name": "data", "mountpoint": ROOT, "mounted": True, "persistent": True, "read_only": False,
                     "devices": ["/dev/sda"], "total_size": max(1, self.pool_used), "used_size": self.pool_used}]
        if parts == ["disks"]:
            return [{"name": DISK, "path": f"/dev/{DISK}", "model": "WDC", "serial": "W1", "size": 640, "rotational": True,
                     "type": "disk", "partitions": [], "is_system": False}]
        if parts[0] == "disks" and parts[2:] == ["smart"]:
            if parts[1] != DISK:
                raise Refused(400, f"Not a disk of this system: {parts[1]}")
            return {"device": f"/dev/{DISK}", "healthy": True, "temperature": 33}
        if parts[:2] == ["pools", "data"] and parts[2] == "volumes":
            if method == "GET":
                return [{"name": p.rsplit("/", 1)[1], "pool": "data", "path": p, "quota": None}
                        for p in self.children(ROOT) if self.fs[p].kind == "subvolume"]
            if method == "POST":
                path = f"{ROOT}/{body['name']}"
                if body.get("quota") and not self.quotas:
                    raise Refused(409, "Quotas are not enabled on pool data: enable them first (btrfs quota enable)")
                if path in self.fs:
                    raise Refused(409, f"Volume {body['name']} already exists")
                self.fs[path] = Node("subvolume", 0o755)
                if body.get("quota"):
                    self.quota_of[path] = body["quota"]
                return {"name": body["name"], "pool": "data", "path": path, "quota": body.get("quota")}
            path = f"{ROOT}/{parts[3]}"
            if path not in self.fs:
                raise Refused(404, "Volume not found")
            users = [n for n, s in self.shares.items() if s["path"] == path or s["path"].startswith(path + "/")]
            if users and "volume-delete-ignores-shares" not in self.bugs:
                raise Refused(409, f"Volume '{parts[3]}' is used by SMB shares ({', '.join(users)}). "
                                   "Stop or remove them and try again.")
            if any(self.fs[p].kind == "subvolume" for p in self.subtree(path) if p != path):
                # btrfs refuses to delete a subvolume that holds snapshots or copies.
                raise Refused(409, f"Volume '{parts[3]}' contains snapshots or other subvolumes (ERROR: Could not "
                                   "destroy subvolume/snapshot: Directory not empty). Delete them first.")
            self.remove(path)
            self.quota_of.pop(path, None)
            self.trash = {t: o for t, o in self.trash.items() if not t.startswith(path + "/")}
            return {"status": "deleted"}
        raise AssertionError(f"the fake does not know {method} /storage/{'/'.join(parts)}")

    def api_snapshots(self, method, parts, body, params, upload):
        """The Snapshots page's API on the pool's volumes: read-only snapshots in <volume>/.snapshots,
        restores into writable copies restore_<time> right in the volume (ADR-0358)."""
        folder = body["subvolume"] if method == "POST" and parts == [] else params.get("subvolume")
        if folder is not None and (folder not in self.fs or self.fs[folder].kind != "subvolume"):
            raise Refused(404, f"Subvolume not found: {folder}")
        if parts == [] and method == "POST":
            name = body.get("name") or "snap_20261112_100000"
            path = f"{folder}/.snapshots/{name}"
            if path in self.fs:
                raise Refused(400, f"Snapshot already exists: {name}")
            self.fs.setdefault(f"{folder}/.snapshots", Node("dir", 0o755))
            self.fs[path] = Node("subvolume", 0o755)
            return {"id": name, "name": name, "subvolume": folder, "path": path, "created": "2026-11-12T10:00:00+00:00"}
        if parts == [] and method == "GET":
            return [{"id": p.rsplit("/", 1)[1], "name": p.rsplit("/", 1)[1], "subvolume": folder, "path": p,
                     "created": "2026-11-12T10:00:00+00:00"}
                    for p in sorted(self.fs) if os.path.dirname(p) == f"{folder}/.snapshots"]
        if parts == ["restore"]:
            source = body["snapshot_path"]
            if source not in self.fs:
                raise Refused(404, f"Snapshot not found: {source}")
            self.restores += 1
            target = f"{source.rsplit('/.snapshots/', 1)[0]}/restore_20261112_1000{self.restores:02d}"
            self.fs[target] = Node("subvolume", 0o755)
            return {"status": "restored", "source": source, "target": target}
        if parts == ["copies"]:
            if "copies-not-listed" in self.bugs:
                return []
            return [{"name": p.rsplit("/", 1)[1], "pool": "data", "volume": folder.rsplit("/", 1)[1], "subvolume": folder,
                     "path": p, "created": f"2026-11-12T10:00:{p[-2:]}+00:00"}
                    for p in sorted(self.fs, reverse=True)
                    if os.path.dirname(p) == folder and re.fullmatch(r"restore_\d{8}_\d{6}", p.rsplit("/", 1)[1])
                    and self.fs[p].kind == "subvolume"]
        if parts[:1] == ["copies"] and method == "DELETE":
            path = f"{folder}/{parts[1]}"
            shown = f"{folder.rsplit('/', 1)[1]}/{parts[1]}"
            if path not in self.fs:
                raise Refused(404, f"Restored copy '{shown}' not found in pool 'data'")
            users = [n for n, s in self.shares.items() if s["path"] == path or s["path"].startswith(path + "/")]
            if users and "copy-delete-ignores-shares" not in self.bugs:
                raise Refused(409, f"Restored copy '{shown}' is used by SMB shares ({', '.join(users)}). "
                                   "Stop or remove them and try again.")
            self.remove(path)
            return {"status": "deleted", "name": parts[1]}
        if method == "DELETE" and len(parts) == 1:
            path = f"{folder}/.snapshots/{parts[0]}"
            if path not in self.fs:
                raise Refused(404, f"Snapshot not found: {parts[0]}")
            self.remove(path)
            return {"status": "deleted", "id": parts[0]}
        raise AssertionError(f"the fake does not know {method} /snapshots/{'/'.join(parts)}")

    def scrub_answer(self) -> dict:
        s = self.scrub
        if s["status"] == "running" and self.scrub_unrecorded:
            # Not recorded yet: btrfs prints the time left of a scrub that runs (ADR-0313).
            return {"pool": "data", "status": "idle" if "unrecorded-is-idle" in self.bugs else "running",
                    "duration": None, "bytes_scrubbed": 0, "total_bytes": scenario.GIB, "rate": 0, "errors": 0, "last_run": None}
        scrubbed = (3 - self.scrub_left) * scenario.GIB // 4 if s["status"] == "running" else None
        return {"pool": "data", "status": s["status"], "duration": s.get("duration"), "bytes_scrubbed": scrubbed,
                "total_bytes": scenario.GIB, "rate": scenario.GIB // 19, "errors": s.get("errors", 0), "last_run": s.get("last_run")}

    def start_scrub(self) -> None:
        if self.scrub["status"] == "running":
            raise Refused(500, "Scrub start failed: ERROR: Scrub is already running.")
        self.scrubs += 1
        self.scrub = {"status": "running", "last_run": f"Mon Nov  9 10:{self.scrubs:02d}:00 2026", "errors": 0}
        self.scrub_unrecorded, self.scrub_left = 2, 3

    def api_scrub(self, method, name):
        if name != "data":
            folder = f"/mnt/pools/{name}"
            if method == "POST" and folder in self.fs and "no-mount-check" in self.bugs:
                raise Refused(500, f"Scrub start failed: ERROR: not a btrfs filesystem: {folder}")
            if method == "POST" and folder in self.fs:
                raise Refused(409, f"Pool {name} is not mounted at {folder}")
            raise Refused(404, f"Pool {name} not found")
        if method == "POST":
            self.start_scrub()
            answer = self.scrub_answer()
            return {**answer, "status": "idle" if "start-answers-idle" in self.bugs else "running"}
        if method == "DELETE":
            if self.scrub["status"] == "running" and "cancel-keeps-running" not in self.bugs:
                if self.scrub_unrecorded:
                    # Cancelled before btrfs recorded it: no record, and the answer says aborted
                    # (ADR-0313).
                    self.scrub = {"status": "idle", "last_run": None, "errors": 0}
                    self.scrub_unrecorded = 0
                    return {**self.scrub_answer(), "status": "aborted"}
                self.scrub = {**self.scrub, "status": "aborted", "duration": 2, "errors": 0}
            return self.scrub_answer()
        answer = self.scrub_answer()
        if self.scrub["status"] == "running":  # each request is a moment later
            if self.scrub_unrecorded:
                self.scrub_unrecorded -= 1
            else:
                self.scrub_left -= 1
                if self.scrub_left <= 0:
                    self.scrub = {**self.scrub, "status": "finished", "duration": 19,
                                  "errors": 2 if "scrub-errors" in self.bugs else 0}
        return answer

    def api_scheduler(self, method, parts, body, params, upload):
        if parts == ["tasks"] and method == "GET":
            return list(copy.deepcopy(self.tasks).values())
        if parts == ["tasks"] and method == "POST":
            task_id = f"task_{len(self.tasks) + len(self.runs) + 1}"
            task = {k: v for k, v in body.items() if k != "passphrase"}  # write-only (ADR-0325)
            if body.get("task_type") == "backup":
                if not body.get("passphrase"):
                    raise Refused(400, "A backup task needs a passphrase: its backups are encrypted with it")
                self.task_keys[task_id] = body["passphrase"]
            self.tasks[task_id] = {**task, "id": task_id}
            return {**self.tasks[task_id], "has_backup_key": task_id in self.task_keys if task.get("task_type") == "backup"
                    else None}
        if parts[:1] == ["tasks"] and parts[2:] == ["run"]:
            task = self.tasks[parts[1]]
            run = {"id": f"run_{len(self.runs) + 1}", "task_id": task["id"], "task_name": task["name"],
                   "started_at": "2026-11-09T10:00:00+00:00", "finished_at": "2026-11-09T10:00:01+00:00",
                   "status": "success", "output": "Scrub started", "error": None, "exit_code": None}
            if task["task_type"] == "backup":  # encrypted with the task's key (ADR-0325)
                name = f"auto_backup_20261111_0300{len(self.backups):02d}.tar.gz.enc"
                self.backups[name] = self.task_keys[task["id"]]
                run["output"] = f"Backup {name} created."
                self.runs.append(run)
                return {"status": "started", "task_id": task["id"]}
            try:
                if task["target_pool"] != "data":  # the one start, with its mount check (ADR-0311)
                    folder = f"/mnt/pools/{task['target_pool']}"
                    if "no-mount-check" in self.bugs:
                        raise Refused(500, f"ERROR: not a btrfs filesystem: {folder}")
                    raise Refused(409, f"Pool {task['target_pool']} is not mounted at {folder}")
                self.start_scrub()
            except Refused as e:
                run.update(status="failed", output=None, error=e.detail)
            self.runs.append(run)
            return {"status": "started", "task_id": task["id"]}
        if parts[:1] == ["runs"]:
            return [r for r in self.runs if r["task_id"] == parts[1]]
        if parts[:1] == ["tasks"] and method == "DELETE":
            if parts[1] not in self.tasks:
                raise Refused(404, "Task not found")
            del self.tasks[parts[1]]
            self.task_keys.pop(parts[1], None)
            if "runs-go-with-the-task" in self.bugs:
                self.runs = [r for r in self.runs if r["task_id"] != parts[1]]
            return {"status": "deleted"}
        raise Refused(404, "Not Found")

    def api_health(self, method, parts, body, params, upload):
        if parts == ["disks"]:
            return {"disks": [self.smart_info()]}
        if parts[0] == "disk":
            if parts[1] != DISK:
                raise Refused(400, f"Not a disk of this system: {parts[1]}")
            if parts[2:] == ["test"]:
                return {"status": "started", "message": f"Short test started on /dev/{DISK}"}
            return self.smart_info()
        raise AssertionError(f"the fake does not know {method} /health/{'/'.join(parts)}")

    def smart_info(self) -> dict:
        return {"device": f"/dev/{DISK}", "model": "WDC", "serial": "W1", "firmware": "1", "capacity": "640 GB",
                "temperature": 33, "power_on_hours": 51832, "power_cycle_count": 9, "health_status": "PASSED",
                "attributes": [{"id": 5}, {"id": 9}], "self_test_status": None, "last_test_result": None}

    def api_files(self, method, parts, body, params, upload):
        what = parts[0] if parts else ""
        if what == "list":
            return {"path": params["path"], "items": [{"name": p.rsplit("/", 1)[1], "path": p, "type": self.fs[p].kind}
                                                      for p in sorted(self.children(params["path"]))]}
        if what == "download":
            node = self.fs.get(params["path"])
            if node is None or node.kind != "file":
                raise Refused(404, "File not found")
            return node.content
        if what == "upload":
            folder = params["path"]
            name, content = upload
            share_folder = self.fs[folder].mode & 0o2000 and self.fs[folder].group == SMB_GROUP
            mode = 0o640 if "upload-mode" in self.bugs else (0o660 if share_folder else 0o644)
            self.fs[f"{folder}/{name}"] = Node("file", mode, "root", SMB_GROUP if share_folder else "root", content)
            return {"name": name}
        if what == "mkdir":
            folder = body["path"]
            share_folder = self.fs[folder].mode & 0o2000 and self.fs[folder].group == SMB_GROUP
            self.fs[f"{folder}/{body['name']}"] = Node("dir", 0o2770 if share_folder else 0o755, "root",
                                                       SMB_GROUP if share_folder else "root")
            return {"name": body["name"]}
        if what == "rename":
            self.refuse_volumes([body["path"]])
            new = f"{os.path.dirname(body['path'])}/{body['new_name']}"
            if new in self.fs:
                raise Refused(400, f"Already exists: {body['new_name']}")
            self.move(body["path"], new)
            return {"name": body["new_name"]}
        if what in ("copy", "move"):
            if what == "move":
                self.refuse_volumes(body)
            for source in body:
                target = f"{params['destination']}/{source.rsplit('/', 1)[1]}"
                for p in sorted(self.subtree(source)):
                    node = copy.copy(self.fs[p])
                    if node.kind == "subvolume":
                        node.kind = "dir"
                    if what == "move":
                        del self.fs[p]
                    if not p[len(source):].startswith("/.unos-trash"):
                        self.fs[target + p[len(source):]] = node
            return {"copied" if what == "copy" else "moved": len(body)}
        if what == "delete":
            if "volume-deletable" not in self.bugs:
                self.refuse_volumes(body)
            for path in body:
                if any(self.fs[p].kind == "subvolume" for p in self.subtree(path) if p != path):
                    raise Refused(409, f"{path} holds a subvolume or a mount: it is not deleted")
            for path in body:
                if params.get("permanent") in (True, "true"):
                    self.remove(path)
                    continue
                trash = (self.volume_of(path) or ROOT) + "/.unos-trash"
                self.fs.setdefault(trash, Node("dir", 0o700))
                self.trash_entries += 1
                entry = f"{trash}/{self.trash_entries}"
                self.fs[entry] = Node("dir", 0o700)
                self.move(path, f"{entry}/{path.rsplit('/', 1)[1]}")
                self.trash[entry] = path
            return {"deleted": len(body)}
        if what == "trash":
            if method == "GET":
                return {"items": [{"path": t, "name": o.rsplit("/", 1)[1], "type": "file", "size": 1,
                                   "original_path": o, "deleted": "2026-11-08T12:00:00+00:00",
                                   "expires": "2026-12-08T12:00:00+00:00"} for t, o in sorted(self.trash.items())],
                        "retention_days": 30, "problems": []}
            if parts[1:] == ["restore"]:
                for entry in body:
                    if self.trash[entry] in self.fs:
                        raise Refused(400, f"Already exists: {self.trash[entry].rsplit('/', 1)[1]}")
                for entry in body:
                    original = self.trash.pop(entry)
                    self.move(f"{entry}/{original.rsplit('/', 1)[1]}", original)
                    self.remove(entry)
                return {"restored": len(body)}
            for entry in body:  # DELETE /files/trash
                self.remove(entry)
                del self.trash[entry]
            return {"deleted": len(body)}
        raise AssertionError(f"the fake does not know {method} /files/{'/'.join(parts)}")

    def refuse_volumes(self, paths) -> None:
        for path in paths:
            if self.fs.get(path) and self.fs[path].kind == "subvolume":
                raise Refused(409, f"{path} is a volume: delete it on the Storage page")

    def reconcile(self) -> None:
        """The startup reconcile after a restart: the marked host accounts and Samba accounts that
        no user's row has (stale) go, and an apply that writes only on a change."""
        if self.reconcile_pending:
            self.reconcile_pending = False
            names = {user["username"] for user in self.users.values()}
            for name in [n for n in self.accounts if n not in names]:
                del self.accounts[name]
                self.passdb.pop(name, None)
            if "restart-writes" in self.bugs:
                self.mtimes["smb.conf"] += 1
            self.apply()
            self.last_apply_ok = True

    def database(self) -> dict:
        """What a backup holds of the database: the users (with their passwords, which stand in for
        the hashes), the shares, the API tokens, the SMB rows (the users' names with a host account,
        and its uid), those that need a password, the next uid; and smb.conf, which no backup should
        hold."""
        names = {user["username"] for user in self.users.values()}
        return {"users": copy.deepcopy(self.users), "shares": copy.deepcopy(self.shares),
                "tokens": copy.deepcopy(self.api_tokens),
                "rows": {n: uid for n, uid in self.accounts.items() if n in names}, "needed": set(self.needed),
                "next_smb_uid": self.next_smb_uid, "smb.conf": self.installed}

    def restore(self, name: str) -> dict:
        """A restore of the database of NAME (src/backup/router.py): a restored SMB row stays set
        only when the live user of its name has an active account with its uid and its password
        (ADR-0141); the next uid never goes below the live one (ADR-0109); then the post-restore
        reconcile (ADR-0138): host accounts that no row has and the Samba passwords of rows that are
        not active go, with their sessions, and smb.conf is rendered from the restored database."""
        answer, reconcile = self.restore_database(name)
        reconcile()
        return answer

    def restore_database(self, name: str) -> tuple[dict, Callable[[], None]]:
        """The part of a restore of NAME up to the swap of the database, with its answer, and the
        post-restore reconcile, which runs after it under the same smb_lock (_restore_and_reconcile)."""
        db = copy.deepcopy(self.databases[name])
        self.restored.append(name)
        copied = f"unos.db.pre-restore-20261111_1300{len(self.restored):02d}_000000"
        self.copies = sorted(self.copies + [copied])[-5:]  # PRE_RESTORE_COPIES
        live = {user["username"]: user for user in self.users.values()}
        asked = []
        for user in db["users"].values():
            username = user["username"]
            if username not in db["rows"] or username in db["needed"]:
                continue
            now = live.get(username)
            proven = (now is not None and username in self.passdb and self.accounts.get(username) == db["rows"][username]
                      and now.get("password") == user.get("password"))
            if "restore-proves-nothing" in self.bugs or ("restore-keeps-every-password" not in self.bugs and not proven):
                db["needed"].add(username)
                asked.append(f"SMB must be turned on again for {username}")
        self.users, self.shares, self.needed = db["users"], db["shares"], db["needed"]
        self.api_tokens = [] if "restore-drops-tokens" in self.bugs else db["tokens"]
        self.next_smb_uid = db["next_smb_uid"] if "restore-next-uid" in self.bugs else max(self.next_smb_uid,
                                                                                            db["next_smb_uid"])

        def reconcile() -> None:
            if "restore-keeps-stale-account" not in self.bugs:
                for username in [n for n, uid in self.accounts.items() if db["rows"].get(n) != uid]:
                    del self.accounts[username]
                    self.passdb.pop(username, None)
                    self.end_sessions(username)
            for username in [n for n in self.passdb if n in self.needed]:
                del self.passdb[username]
                self.end_sessions(username)
            if "restore-installs-the-archived-conf" in self.bugs:
                if self.installed != db["smb.conf"]:
                    self.installed = db["smb.conf"]
                    self.mtimes["smb.conf"] += 1
            elif "restore-apply-fails" not in self.bugs:
                self.apply()
            if "restore-rewrites-smb-conf" in self.bugs:
                self.installed += "# rewritten by the restore\n"
                self.mtimes["smb.conf"] += 1

        items = ["database"] if "restore-apply-fails" in self.bugs else ["database", "smb_config"]
        return {"status": "success", "restored": items, "warnings": asked,
                "message": f"Backup restored: {', '.join(items)}.", "manifest": {"version": "1.0"},
                "previous_database": f"/data/{copied}"}, reconcile

    def api_shares(self, method, parts, body, params, upload):
        if method != "GET":  # a change of SMB state waits for smb_lock
            self.reconcile()
        if parts == ["smb-status"]:
            return {"samba_tools_missing": [], "group_ok": True, "problems": self.status_problems(),
                    "guest_shares": sorted(set(self.guest_shares)
                                           | {n for n, info in self.shares.items() if info.get("guest_ok")}),
                    "import_report": [],
                    "unmanaged_samba_users": [], "active_smb_users": len(self.passdb)}
        if parts == ["smb"] and method == "GET":
            return [self.share_info(name) for name in sorted(self.shares)]
        if parts == ["smb"]:
            name, path = body["name"], body["path"]
            if name in self.shares:
                raise Refused(409, f"Share {name} already exists")
            if path == ROOT:
                raise Refused(400, "A pool's top folder cannot be shared")
            for other in self.shares.values():
                if path.startswith(other["path"] + "/") or other["path"].startswith(path + "/"):
                    raise Refused(409, "The folder overlaps another share")
            warnings = []
            if path not in self.fs:
                refusal = self.unsafe_text(path, "refusal")
                if refusal:  # no folder is made below a folder that others can change (ADR-0153)
                    raise Refused(409, refusal)
                self.fs[path] = Node("dir", 0o750 if "share-folder-mode" in self.bugs else 0o2770, "root", SMB_GROUP)
            else:
                node = self.fs[path]
                if (node.mode & 0o7777, node.group) != (0o2770, SMB_GROUP):
                    warnings.append(PERMISSIONS_WARNING)
                if (warning := self.unsafe_text(path, "warning")):
                    warnings.append(warning)
            self.shares[name] = {"path": path, "comment": body.get("comment", ""), "access": body.get("access", []),
                                 "version": 1, "guest_ok": bool(body.get("guest_ok"))}
            self.apply()
            return {**self.share_info(name), "warnings": warnings}
        name = parts[1]
        if name not in self.shares:
            raise Refused(404, f"Share {name} not found")
        if parts[2:] == ["repair-permissions"]:
            return self.api_repair(method, name)
        if method == "GET":
            return self.share_info(name)
        if method == "PATCH":
            info = self.shares[name]
            if "expected_version" in body and body["expected_version"] != info["version"]:
                raise Refused(409, "The share was changed meanwhile")
            # A share that is not served grants nothing until a new folder is served (ADR-0145).
            grants = bool(body["access"]) if "access" in body else bool(info["access"])
            problem = self.path_problem(body.get("path", info["path"]))
            if grants and problem and "access-without-folder" not in self.bugs:
                raise Refused(400, problem)
            if "path" in body:
                if body["path"] not in self.fs:
                    self.fs[body["path"]] = Node("dir", 0o2770, "root", SMB_GROUP)
                info["path"] = body["path"]
            if "access" in body:
                old = {a["username"]: a["level"] for a in info["access"]}
                new = {a["username"]: a["level"] for a in body["access"]}
                shrunk = any(u not in new or (lvl == "write" and new[u] == "read") for u, lvl in old.items())
                info["access"] = body["access"]
                if shrunk:
                    self.close_share(name)
            if "comment" in body:
                info["comment"] = body["comment"]
            warnings = []
            if "guest_ok" in body:
                if body["guest_ok"] and not info.get("guest_ok") and "no-guest-warning" not in self.bugs:
                    warnings.append(GUEST_WARNING)
                info["guest_ok"] = body["guest_ok"]
            info["version"] += 1
            self.apply()
            return {**self.share_info(name), "warnings": warnings}
        del self.shares[name]
        self.close_share(name)
        self.apply()
        return {"status": "deleted", "warnings": []}

    def api_repair(self, method, name):
        """Repair permissions of a share (src/shares/repair_router.py, ADR-0155): its last result since
        the start, or a new run, refused with 409 for a share that is not served or below a folder that
        others can change."""
        if method == "GET":
            if name not in self.repairs:
                raise Refused(404, "No repair has run for this share since UNOS started")
            return copy.deepcopy(self.repairs[name])
        path = self.shares[name]["path"]
        problem = self.path_problem(path)
        if problem and "repair-unserved" not in self.bugs:
            raise Refused(409, f"Share {name} is not served: {problem}; change its folder first")
        if (refusal := self.unsafe_text(path, "refusal")):
            raise Refused(409, refusal)
        self.repairs[name] = {"share": name, "state": "done", "started_at": "2026-11-11T10:00:00Z",
                              "finished_at": "2026-11-11T10:00:01Z", **self.repair_walk(path),
                              "skipped_paths": [], "errors": 0, "error_samples": [], "message": "Permissions repaired."}
        return copy.deepcopy(self.repairs[name])

    def repair_walk(self, path: str) -> dict:
        """repair_tree (ADR-0156) on the fake: folders 2770, files 660 or 770, the group unos-smb;
        links, private entries and other filesystems skipped, a private folder not entered; an ACL's
        mask follows the group bits."""
        tally = {"visited": 0, "changed": 0,
                 "skipped": dict.fromkeys(("other_filesystem", "snapshots", "symlink", "special", "hardlink",
                                           "private", "too_deep", "raced"), 0)}

        def fix(at: str, mode: int) -> None:
            node = self.fs[at]
            if (node.group, node.mode) != (SMB_GROUP, mode):
                node.group, node.mode = SMB_GROUP, mode
                tally["changed"] += 1
                if "repair-drops-acl" in self.bugs:
                    node.acl = None

        def visit(folder: str) -> None:
            for at in sorted(self.children(folder)):
                tally["visited"] += 1
                node = self.fs[at]
                if node.kind == "link":
                    tally["skipped"]["symlink"] += 1
                    if "repair-follows-links" in self.bugs:
                        fix(os.path.normpath(os.path.join(folder, node.content)), 0o660)
                elif node.kind == "subvolume":
                    tally["skipped"]["other_filesystem"] += 1
                elif node.mode & 0o077 == 0 and "repair-opens-private" not in self.bugs:
                    tally["skipped"]["private"] += 1
                elif node.kind == "dir":
                    fix(at, 0o2770)
                    visit(at)
                else:
                    fix(at, 0o770 if node.mode & 0o100 else 0o660)

        tally["visited"] += 1
        fix(path, 0o2770)
        visit(path)
        return tally

    def activate(self, name: str, password: str) -> None:
        """SMB on with PASSWORD: the row's uid, or a fresh one; Samba's password. The defect
        reenable-new-uid gives a fresh uid also to a row that has one."""
        if name not in self.accounts or "reenable-new-uid" in self.bugs:
            self.accounts[name] = self.next_smb_uid
            self.next_smb_uid += 1
        self.passdb[name] = {"password": password, "history": [], "bad": 0, "locked": False}
        self.needed.discard(name)

    def api_auth(self, method, parts, body, params, upload):
        if self.identity is not None:
            return self.api_own(method, "/".join(parts), body)
        assert parts[:1] != ["me"] and parts != ["change-password"], f"/auth/{'/'.join(parts)} with the administrator's token"
        if parts == ["tokens"]:  # list_api_tokens: the caller's own, the relay's administrator's
            return [copy.deepcopy(t) for t in self.api_tokens if t["user_id"] == 1]
        if parts == ["users"] and method == "GET":
            return [self.user_info(uid) for uid in sorted(self.users)]
        if parts == ["users"]:
            uid = self.next_user
            self.next_user += 1
            self.users[uid] = {"username": body["username"], "is_admin": False, "password": body["password"],
                               "smb_allowed": bool(body.get("smb_enabled"))}
            if body.get("smb_enabled"):
                self.activate(body["username"], body["password"])
            if "log-password" in self.bugs:
                self.log.append(f"created {body['username']} with {body['password']}")
            return self.user_info(uid)
        uid = int(parts[1])
        if uid not in self.users:
            raise Refused(404, "User not found")
        name = self.users[uid]["username"]
        if parts[2:] == ["smb", "enable"]:  # an administrator allows SMB, or turns it on with a password (ADR-0118)
            if body["username"] != name:
                raise Refused(409, f"User {uid} is now {name}")
            self.users[uid]["smb_allowed"] = True
            if body.get("password") is not None:
                self.users[uid]["password"] = body["password"]
                self.activate(name, body["password"])
                self.apply()
            return self.user_info(uid)
        if method == "PUT":
            self.users[uid]["password"] = body["password"]
            account = self.passdb.get(name)
            if account is not None:
                account["history"] = ([account["password"]] + account["history"])[:2]
                account["password"] = body["password"]
                self.end_sessions(name)
            return self.user_info(uid)
        if params.get("username") != name:
            raise Refused(409, f"User {uid} is now {name}")
        del self.users[uid]
        self.accounts.pop(name, None)
        self.passdb.pop(name, None)
        self.needed.discard(name)
        for share in self.shares.values():
            share["access"] = [a for a in share["access"] if a["username"] != name]
        self.end_sessions(name)
        return {"status": "ok", "message": "User deleted", "warnings": []}

    def api_own(self, method, what, body):
        """A regular user's own routes (src/auth/router.py): the Account page's SMB card and the
        password change, which keeps the token's session (ADR-0329)."""
        uid = next(u for u, info in self.users.items() if info["username"] == self.identity)
        user = self.users[uid]
        name = user["username"]

        def status() -> dict:
            shares = []
            for share, info in sorted(self.shares.items()):
                levels = {a["level"] for a in info["access"] if a["user_id"] == uid and a["username"] == name}
                if levels or info.get("guest_ok"):
                    shares.append({"name": share, "comment": info["comment"], "guest_ok": bool(info.get("guest_ok")),
                                   "level": "write" if "write" in levels else "read",
                                   "usable": name in self.passdb or bool(info.get("guest_ok"))})
            return {"account": self.smb_state(uid), "totp_required": False, "shares": shares}

        if what == "me" and method == "GET":
            return {"id": uid, "username": name, "is_admin": user["is_admin"], "totp_enabled": False}
        if what == "me/smb" and method == "GET":
            return status()
        if method != "POST":
            raise AssertionError(f"the fake does not know {method} /auth/{what}")
        if what in ("change-password", "me/smb/enable") and body["current_password"] != user["password"]:
            raise Refused(400, "Current password is incorrect")
        if what == "change-password":
            new = body["new_password"]
            if len(new.encode()) > scenario.UNOS_PASSWORD_BYTES:  # NewPassword's validator: the model refuses it
                self.invalid.add(len(self.calls) - 1)
                raise Refused(422, [{"type": "value_error", "loc": ["body", "new_password"],
                                     "msg": "Value error, Password must not be longer than 72 bytes (UTF-8 encoded)"}])
            account = self.passdb.get(name)
            if account is not None:  # an active account changes Samba's password first
                if (problem := smb_password_problem(new)):
                    raise Refused(400, problem)
                account["history"] = ([account["password"]] + account["history"])[:2]
                account["password"] = new
                self.end_sessions(name)
            user["password"] = new
            return {"status": "ok", "message": "Password changed successfully", "warnings": []}
        if what == "me/smb/enable":
            problem = smb_password_problem(body["current_password"])
            if problem and "smb-takes-short-password" not in self.bugs:
                raise Refused(400, problem)
            if not (user.get("smb_allowed") or name in self.needed):
                raise Refused(403, "An administrator must allow SMB access for your account first")
            self.activate(name, body["current_password"])
            return {**status(), "warnings": []}
        if what == "me/smb/disable":  # the allowance and the host account stay (ADR-0118)
            if "self-off-keeps-samba" not in self.bugs:
                self.passdb.pop(name, None)
            self.end_sessions(name)
            return {**status(), "warnings": []}
        raise AssertionError(f"the fake does not know {method} /auth/{what}")

    def api_backup(self, method, parts, body, params, upload):
        if parts == ["list"]:
            return {"backups": [{"filename": n, "encrypted": p is not None, "size": 1} for n, p in sorted(self.backups.items())]}
        if parts == ["create"]:
            name = f"unos_backup_20261108_1200{len(self.backups):02d}.tar.gz" + (".enc" if body.get("encrypt") else "")
            self.backups[name] = body.get("passphrase") if body.get("encrypt") else None
            self.databases[name] = self.database()
            return {"status": "success", "encrypted": bool(body.get("encrypt")), "backup": {"filename": name}}
        if parts[0] == "restore":
            if parts[1] not in self.backups:
                raise Refused(404, "Backup not found")
            passphrase = (body or {}).get("passphrase")
            if self.backups[parts[1]] is not None and not passphrase:
                raise Refused(400, "Passphrase required for encrypted backup")
            if passphrase != self.backups[parts[1]]:
                raise Refused(400, "Wrong passphrase, or the backup is damaged")
            assert parts[1] in self.databases, "a restore of a backup that no check made"
            return self.restore(parts[1])
        if parts[0] not in self.backups:
            raise Refused(404, "Backup not found")
        del self.backups[parts[0]]
        self.databases.pop(parts[0], None)
        return {"status": "deleted", "filename": parts[0]}

    def offsite_answer(self) -> dict:
        settings = self.offsite
        # The fake's one pool holds the target and the folders it copies.
        same_disk = settings is not None and settings["path"].startswith(ROOT + "/")
        warnings = [{"kind": "same_disk", "message": "This copy is on a disk it copies"}] if same_disk else []
        return {"configured": settings is not None, "settings": copy.deepcopy(settings),
                "has_password": self.offsite_password is not None, "has_s3_secret_access_key": False,
                "pools": [{"name": "data", "path": ROOT}], "pools_base": "/mnt/pools", "warnings": warnings}

    def offsite_busy(self) -> None:
        if self.offsite_running is not None:
            raise Refused(409, f"The {self.offsite_running['kind']} started at 2026-11-11 03:00:00 UTC is still running")

    def offsite_snapshots(self) -> list[dict]:
        folder = f"{self.offsite['path']}/snapshots"
        found = [json.loads(self.fs[p].content) for p in self.fs if os.path.dirname(p) == folder]
        return sorted(found, key=lambda snap: snap["time"], reverse=True)

    def offsite_snapshot(self, kind: str, paths: list[str], files: dict[str, str]) -> dict:
        self.offsite_count += 1
        snap_id = hashlib.sha256(f"snapshot {self.offsite_count}".encode()).hexdigest()
        snapshot = {"id": snap_id, "short_id": snap_id[:8], "time": f"2026-11-11T03:00:{self.offsite_count:02d}.123456789+00:00",
                    "kind": kind, "tags": [f"unos-{'configuration' if kind == 'configuration' else 'data'}"],
                    "paths": paths, "hostname": "unos", "size": sum(len(c) for c in files.values()),
                    "files": len(files), "content": files}
        self.fs[f"{self.offsite['path']}/snapshots/{snap_id}"] = Node("file", 0o400, content=json.dumps(snapshot))
        return snapshot

    def offsite_start(self, kind: str, result_key: str, result: dict, folder: str | None = None) -> None:
        """A copy or restore that GET /offsite/status shows running for one request, then RESULT."""
        self.offsite_running = {"kind": kind, "started": "2026-11-11T03:00:00+00:00", "step": None,
                                "snapshot_id": None, "folder": folder}
        self.offsite_left = 1
        self.offsite_pending = (result_key, result)

    def api_offsite(self, method, parts, body, params, upload):
        what = "/".join(parts)
        if what == "settings" and method == "GET":
            return self.offsite_answer()
        if what == "settings" and method == "PUT":
            self.offsite_busy()
            target, sources = body["path"], body["sources"]
            if not target.startswith("/mnt/") or os.path.dirname(target) not in self.fs:
                raise Refused(400, f"{os.path.dirname(target)} does not exist")
            for source in sources:
                if source not in self.fs:
                    raise Refused(400, f"{source} does not exist")
            password = body.get("password") or (self.offsite_password if self.offsite and self.offsite["path"] == target
                                                 else None)
            if not password:
                raise Refused(400, "Enter the password of the copy repository")
            if f"{target}/config" in self.fs:
                if json.loads(self.fs[f"{target}/keys/key"].content)["password"] != password:
                    raise Refused(400, "The password does not open the copy repository: Fatal: wrong password")
            else:
                self.fs.setdefault(target, Node("dir", 0o700))
                for sub in ("data", "index", "keys", "locks", "snapshots"):
                    self.fs[f"{target}/{sub}"] = Node("dir", 0o700)
                self.fs[f"{target}/config"] = Node("file", 0o400, content="{}")
                self.fs[f"{target}/keys/key"] = Node("file", 0o400, content=json.dumps({"password": password}))
            if "log-offsite-password" in self.bugs:
                self.log.append(f"restic init with RESTIC_PASSWORD={password}")
            self.offsite = {"kind": "disk", "path": target, "sources": list(sources), "keep_daily": 7, "keep_weekly": 4,
                            "keep_monthly": 12, "check_percent": 2}
            self.offsite_password = password
            return self.offsite_answer()
        if what == "settings" and method == "DELETE":
            self.offsite_busy()
            self.offsite, self.offsite_password = None, None
            self.offsite_status = {"last_copy": None, "last_restore": None}
            return {"status": "deleted"}
        if what == "status":
            answer = {"running": copy.deepcopy(self.offsite_running), **copy.deepcopy(self.offsite_status)}
            if self.offsite_running is not None:
                self.offsite_left -= 1
                if self.offsite_left <= 0:
                    key, result = self.offsite_pending
                    self.offsite_status[key] = result
                    self.offsite_running = self.offsite_pending = None
            return answer
        if self.offsite is None:
            raise Refused(409, "No off-device copy is set up: set one up on System > Backup & Restore")
        if what == "run":
            self.offsite_busy()
            config = self.offsite_snapshot("configuration", ["/unos-configuration.tar.gz"], {"/unos-configuration.tar.gz": "archive"})
            files = {p: self.fs[p].content for p in sorted(self.fs) for src in self.offsite["sources"]
                     if p.startswith(src + "/") and self.fs[p].kind == "file"}
            data = self.offsite_snapshot("data", list(self.offsite["sources"]), files)
            failed = "offsite-check-fails" in self.bugs
            self.offsite_start("copy", "last_copy", {
                "started": "2026-11-11T03:00:00+00:00", "ended": "2026-11-11T03:00:09+00:00", "trigger": "manual",
                "success": not failed, "step": "check" if failed else None,
                "error": "restic check ended with exit status 1: Fatal: repository contains errors" if failed else None,
                "summary": {"files_new": len(files), "files_changed": 0, "files_unmodified": 0, "bytes_added": 99,
                            "configuration_snapshot": config["id"], "data_snapshot": data["id"]},
                "check": "failed" if failed else "passed", "restore_test": None if failed else "passed"})
            return 202, {"status": "started"}
        if what == "snapshots":
            return {"snapshots": [{k: v for k, v in snap.items() if k != "content"} for snap in self.offsite_snapshots()]}
        found = [snap for snap in self.offsite_snapshots() if snap["id"].startswith(body["snapshot_id"])]
        if not found:
            raise Refused(404, f"The copy repository has no snapshot {body['snapshot_id']}")
        snapshot = found[0]
        if what == "restore-configuration":
            self.offsite_busy()
            stamp = re.sub(r"[-:T]", "", snapshot["time"][:19])
            name = f"offsite_{stamp[:8]}_{stamp[8:]}.tar.gz"
            self.backups[name] = None
            return {"status": "success", "backup": {"filename": name, "size": 1, "created": "2026-11-11T04:00:00",
                                                    "version": "1.0", "encrypted": False}}
        if what == "restore-files":
            self.offsite_busy()
            if body["pool"] != "data":
                raise Refused(400, f"There is no pool {body['pool']} at /mnt/pools/{body['pool']}")
            self.offsite_count += 1
            folder = f"{ROOT}/restored-20261111-04{self.offsite_count:04d}"
            self.fs[folder] = Node("dir", 0o755)
            sub = (body.get("path") or "").rstrip("/")
            for path, content in snapshot["content"].items():
                relative = path[len(sub) + 1:] if sub else path.lstrip("/")
                if sub and not path.startswith(sub + "/"):
                    continue
                parts_of = relative.split("/")
                for i in range(1, len(parts_of)):
                    self.fs.setdefault(f"{folder}/{'/'.join(parts_of[:i])}", Node("dir", 0o755))
                changed = content + "changed" if "offsite-restore-differs" in self.bugs else content
                self.fs[f"{folder}/{relative}"] = Node("file", 0o644, content=changed)
            self.offsite_start("restore-files", "last_restore", {
                "snapshot_id": snapshot["id"], "path": body.get("path"), "folder": folder,
                "started": "2026-11-11T04:00:00+00:00", "ended": "2026-11-11T04:00:02+00:00", "success": True,
                "error": None}, folder=folder)
            return 202, {"status": "started", "folder": folder}
        raise AssertionError(f"the fake does not know {method} /offsite/{what}")

    # --- the host (scenario.Device's methods)

    def device_call(self, name: str) -> None:
        if self.refuse_device == name:
            raise scenario.Failed(f"the device answered status 1 for {name}")

    def device_made(self, command: str) -> None:
        """A command that fails halfway: it made its thing, then answered status 1."""
        if self.refuse_after == command:
            raise scenario.Failed(f"the device answered status 1 after {command}")

    def sectors(self):
        self.sector_count += 8
        return self.sector_count

    def stat(self, paths):
        self.device_call("stat")
        out = {}
        for path in paths:
            node = self.fs.get(path)
            out[path] = "missing" if node is None else f"{node.mode:o} {node.owner}:{node.group}"
        return out

    def read(self, path):
        self.device_call("read")
        if path == "/etc/samba/smb.conf":
            return self.installed
        node = self.fs.get(path)
        return node.content if node is not None and node.kind == "file" else None

    def facts(self):
        self.device_call("facts")
        accounts = [f"{n}:x:{uid}:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin" for n, uid in sorted(self.accounts.items())]
        digest = hashlib.sha256("\n".join(accounts).encode()).hexdigest()
        return {
            "now": 1_800_000_000,
            "tools": ["getent", "pdbedit", "smbclient", "smbpasswd", "stdbuf", "testparm"] + ([] if self.no_acl else ["getfacl", "setfacl"]),
            "sha256": {"/etc/passwd": digest, "/etc/group": "g", "/etc/shadow": digest, "/etc/gshadow": "gs",
                       "/etc/samba/smb.conf": hashlib.sha256(self.installed.encode()).hexdigest()},
            "accounts": accounts,
            "group": "unos-smb:x:70000:",
            "testparm": True,
            "passdb": f"2026-11-08 12:00:00.{self.passdb_mtime:09d} +0000",  # stat -c %y
            "sizes": {"/var/log/samba/log.smbd": "120", "/var/log/samba/log.nmbd": "missing", "/run/samba/log.samba": "800"},
        }

    def passdb_entries(self):
        self.device_call("passdb_entries")
        return sorted(self.passdb), self.policy_values()

    def policy_values(self) -> dict[str, str]:
        """Samba's account policies, with those of a device that UNOS's reconcile did not set."""
        values = dict(self.policies)
        for bug, key, value in (("no-history", "history", "0"), ("no-lockout", "lockout", "0"),
                                ("long-lockout", "duration", "30")):
            if bug in self.bugs:
                values[key] = value
        return values

    def subvolume(self, action, path):
        self.device_call("subvolume")
        if action == "create":
            self.fs[path] = Node("subvolume", 0o755)
        elif action == "delete":
            self.fs.pop(path, None)
        return "subvolume" if self.fs.get(path) and self.fs[path].kind == "subvolume" else "none"

    def system_disk(self):
        return "mmcblk1"

    def now(self):
        self.device_call("now")
        return 1_800_000_000

    def quota_limit(self, path):
        self.device_call("quota_limit")
        return str(self.quota_of[path]) if path in self.quota_of else "none"

    def scan(self, needles, since):
        self.device_call("scan")
        found = sum(1 for line in self.log for n in needles if n in line)
        counts = {"the supervisor's log": found, "the journal": 0, "Samba's log": 0, "the audit log": 0}
        return {k: (-1 if k in self.unreadable else v) for k, v in counts.items()}

    def smb_list(self, user, password):
        self.device_call("smb_list")
        status = self.sign_in(user, password)
        if status is not None:
            return scenario.SmbListing(1, f"session setup failed: {status}")
        disks = [f"Disk|{name}|" for name in sorted(self.shares) if self.level(name, user)]
        return scenario.SmbListing(0, "\n".join(disks + ["IPC|IPC$|IPC Service"]))

    def smb_anonymous(self):
        """With a guest share, a null session signs in (map to guest = Bad User), but restrict
        anonymous = 1 lets srvsvc refuse it the share list: as on the device, no share is named."""
        self.device_call("smb_anonymous")
        if not self.guests_rendered():
            return scenario.SmbListing(1, "session setup failed: NT_STATUS_ACCESS_DENIED")
        return scenario.SmbListing(0, "")

    def smb_change_password(self, user, old, new):
        """smbpasswd -r 127.0.0.1: a session signed in with OLD, then Samba's SAMR change to NEW,
        which its check password script /bin/false refuses before Samba stores anything (ADR-0342);
        smbpasswd names the refusal by MS-ERREF's description of NT_STATUS_PASSWORD_RESTRICTION, as
        Samba 4.22 prints it (SAMR_REFUSAL). The defect samr-change-accepted takes the change, as
        Samba does without that line: the new password, the old one in the history, passdb.tdb
        written. Two defects answer the refusal all the same: samr-refusal-writes writes
        passdb.tdb, and samr-refusal-changes-password stores the new password, without a change of
        passdb.tdb's time, so that only the sign-in after the refusal shows it."""
        self.device_call("smb_change_password")
        status = self.sign_in(user, old)
        if status is not None:
            return scenario.SmbPasswordChange(1, f"Could not connect to machine 127.0.0.1: {status}\n")
        account = self.passdb[user]
        if "samr-change-accepted" in self.bugs:
            account["history"] = ([account["password"]] + account["history"])[:2]
            account["password"] = new
            self.passdb_mtime += 1
            return scenario.SmbPasswordChange(0, f"Password changed for user {user} on 127.0.0.1.\n")
        if "samr-refusal-writes" in self.bugs:
            self.passdb_mtime += 1
        if "samr-refusal-changes-password" in self.bugs:
            account["password"] = new
        return scenario.SmbPasswordChange(1, SAMR_REFUSAL)

    def smb_session(self, user, password, share):
        self.device_call("smb_session")
        return FakeSmbSession(self, user, password, share)

    def smb_guest_session(self, share):
        self.device_call("smb_guest_session")
        return FakeSmbSession(self, "", "", share, guest=True)

    def entries(self, action, path, acl=False):
        """REMOTE_ENTRIES: the sticky folders, or REPAIR_ENTRIES in a share folder of the check and
        the file outside it that link.txt points to; the other account is nobody:nogroup."""
        self.device_call("entries")
        if action == "sticky":
            assert re.fullmatch(rf"{ROOT}/unoscheck[0-9a-f]{{6}}-sticky", path), path
            if path in self.fs:
                raise scenario.Failed(f"the device answered status 1: mkdir: {path}: File exists")
            self.fs[path] = Node("dir", 0o1777)
            self.fs[f"{path}/theirs"] = Node("dir", 0o1777, "nobody", "nogroup")
        else:
            assert action == "repair" and re.fullmatch(rf"{ROOT}/unoscheck[0-9a-f]{{6}}", path), (action, path)
            outside = f"{path}-outside"
            if outside in self.fs or any(f"{path}/{name}" in self.fs for name in scenario.REPAIR_ENTRIES):
                raise scenario.Failed(f"the device answered status 1: {outside} exists")
            self.fs[outside] = Node("dir", 0o755)
            self.fs[f"{outside}/target.txt"] = Node("file", 0o644, content=scenario.DATA)
            for name, (made, _) in scenario.REPAIR_ENTRIES.items():
                mode, owner = made.format(user="nobody", group="nogroup").split()
                user, group = owner.split(":")
                kind = "link" if name == "link.txt" else ("file" if "." in name else "dir")
                content = f"../{outside.rsplit('/', 1)[1]}/target.txt" if kind == "link" else (
                    scenario.DATA if kind == "file" else "")
                self.fs[f"{path}/{name}"] = Node(kind, int(mode, 8), user, group, content,
                                                 ["user:nobody:r--"] if acl and name == "acl.txt" else None)
        self.device_made(f"entries {action}")
        return {"user": "nobody", "group": "nogroup", "uid": "65534"}

    def acl(self, path):
        self.device_call("acl")
        return self.fs[path].acl_lines()

    def samba_files(self):
        self.device_call("samba_files")
        files = {"smb.conf": self.installed, **self.samba}
        return {name: {"sha256": digest(text), "mtime": str(self.mtimes.get(name, 1)),
                       "mode": "644 root:root" if name == "smb.conf" else "600 root:root"}
                for name, text in sorted(files.items())}

    def hand_edit(self, action, section, expect=""):
        """REMOTE_HAND_EDIT: edit smb.conf as by hand, keeping the original in RAM, or put it back
        when it is not UNOS's and delete the copies that hold the check's edited file."""
        self.device_call("hand_edit")
        assert re.fullmatch(r"unoscheck[0-9a-f]{6}hand", section), section
        if action == "edit":
            if digest(self.installed) != expect:
                raise scenario.Failed("the device answered status 1: smb.conf changed since the check read it")
            if len(self.replaced_copies()) >= scenario.REPLACED_COPIES_KEPT or section in self.saved:
                raise scenario.Failed("the device answered status 1: an apply would delete the oldest copy")
            self.saved[section] = self.installed
            body = self.installed.split("\n")
            while body and body[0].startswith("#"):
                body.pop(0)
            self.installed = ("# Edited by hand for a scenario check of UNOS\n" + "\n".join(body)
                              + f"\n[{section}]\n   path = /srv/{section}\n   available = no\n")
            self.mtimes["smb.conf"] += 1
        else:
            saved = self.saved.pop(section, None)
            if saved is not None and not self.installed.startswith(HEADER):
                self.installed = saved
                self.mtimes["smb.conf"] += 1
            for name in self.replaced_copies():
                if expect and digest(self.samba[name]) == expect:
                    del self.samba[name]
                    self.mtimes.pop(name, None)
        return digest(self.installed), {name: digest(self.samba[name]) for name in self.replaced_copies()}

    def unserve(self, action, path):
        """REMOTE_UNSERVE: a share folder of a check replaced by a symbolic link, or put back."""
        self.device_call("unserve")
        assert re.fullmatch(rf"{ROOT}/unoscheck[0-9a-f]{{6}}", path), path
        moved = path + "-moved"
        node = self.fs.get(path)
        if action == "link":
            if node is None or node.kind == "link" or moved in self.fs:
                raise scenario.Failed(f"the device answered status 1: {path} is not a plain folder")
            self.move(path, moved)
            self.fs[path] = Node("link", 0o777, content=moved.rsplit("/", 1)[1])
            self.device_made("unserve link")
        else:
            if node is not None and node.kind == "link":
                del self.fs[path]
            if moved in self.fs and path not in self.fs:
                self.move(moved, path)
        node = self.fs.get(path)
        return "missing" if node is None else ("link" if node.kind == "link" else "folder")

    def pool_folders(self, action="list", name=""):
        """REMOTE_POOL_FOLDER: a folder of a check below /mnt/pools, made or removed."""
        self.device_call("pool_folders")
        path = f"/mnt/pools/{name}"
        if action == "make":
            assert re.fullmatch(r"unoscheck[0-9a-f]{6}", name), name
            if path in self.fs:
                raise scenario.Failed(f"the device answered status 1: mkdir: {path}: File exists")
            self.fs[path] = Node("dir", 0o755)
        elif action == "remove":
            assert re.fullmatch(r"unoscheck[0-9a-f]{6}", name), name
            self.fs.pop(path, None)
        return sorted(p.rsplit("/", 1)[1] for p in self.fs if os.path.dirname(p) == "/mnt/pools")

    def archive_members(self, name, passphrase=""):
        """REMOTE_ARCHIVE: the members of a backup, as src/backup/router.py and the Scheduler write
        them; an encrypted one opens only with its passphrase."""
        self.device_call("archive_members")
        if name not in self.backups:
            raise scenario.Failed(f"the device answered status 1: FileNotFoundError: /data/backups/{name}")
        if self.backups[name] is not None and passphrase != self.backups[name]:
            raise scenario.Failed("the device answered status 1: cryptography.exceptions.InvalidTag")
        scheduled = name.startswith("auto_backup_")
        members = ["manifest.json", "unos.db"] + (["scheduled_tasks.json"] if scheduled else [])
        if ("scheduled-backup-holds-smb-conf" if scheduled else "backup-holds-smb-conf") in self.bugs:
            members.append("smb.conf")
        return members

    def db_copies(self, action="list", names=()):
        """REMOTE_DB_COPIES: the copies of the database from before a restore, after removing NAMES."""
        self.device_call("db_copies")
        if action == "remove":
            assert all(re.fullmatch(r"unos\.db\.pre-restore-\d{8}_\d{6}_\d{6}", n) for n in names), names
            self.copies = [n for n in self.copies if n not in names]
        return sorted(self.copies)

    def restart_supervisor(self):
        """docker restart unos-supervisor: the copy's problem goes with the process, and the API
        answers before the startup reconcile has applied (reconcile)."""
        self.device_call("restart_supervisor")
        self.restarts += 1
        self.replaced_problem = None
        self.last_apply_ok = None
        self.reconcile_pending = True
        return 8


@pytest.fixture
def nas():
    return FakeNas()


@pytest.fixture(autouse=True)
def _scrubs_without_pauses(monkeypatch):
    """The scrub check asks btrfs every SCRUB_POLL seconds, for up to SCRUB_WAIT seconds: the fake's
    scrubs are recorded and end after a few requests."""
    monkeypatch.setattr(scenario, "SCRUB_POLL", 0)
    monkeypatch.setattr(scenario, "SCRUB_WAIT", 2)


def run_check(area: str, nas: FakeNas, **kw) -> tuple[int, str]:
    out = io.StringIO()
    check = scenario.Check(area, nas, nas, out=out, run_id="unoscheck1a2b3c", **kw)
    status = check.execute(scenario.CHECKS[area])
    return status, out.getvalue()


def step_lines(output: str) -> list[str]:
    """The lines of the steps, without those of the clean-up."""
    return [line for line in output.splitlines() if re.match(r"(PASS|FAIL|SKIP)  \d+\. ", line)]


# --- each area, against the fake ------------------------------------------------------------


@pytest.mark.parametrize("area", scenario.AREAS)
def test_each_check_passes_reports_every_step_with_its_sd_writes_and_leaves_the_device_as_it_was(nas, area):
    before = nas.snapshot()
    status, output = run_check(area, nas)
    assert status == 0, output
    steps = step_lines(output)
    assert len(steps) >= 4 and all(line.startswith("PASS  ") for line in steps), output
    # Every step line ends with the MiB written to the SD card meanwhile (ADR-0214).
    assert all(re.search(r"  \[SD \d+\.\d{3} MiB\]$", line) for line in steps), output
    assert output.splitlines()[0] == f"== scenario {area} (run unoscheck1a2b3c)"
    assert re.search(rf"^scenario {area}: OK, \d+ steps passed$", output, re.M), output
    assert nas.snapshot() == before
    assert not nas.sessions


def test_the_files_check_covers_the_file_manager_the_trash_and_the_volumes(nas):
    status, output = run_check("files", nas)
    assert status == 0, output
    done = {(method, path.split("?")[0]) for method, path, _, _ in nas.calls}
    for call in [("POST", "/files/mkdir"), ("POST", "/files/upload"), ("GET", "/files/download"),
                 ("POST", "/files/rename"), ("POST", "/files/copy"), ("POST", "/files/move"),
                 ("DELETE", "/files/delete"), ("GET", "/files/trash"), ("POST", "/files/trash/restore"),
                 ("DELETE", "/files/trash"), ("POST", "/storage/pools/data/volumes")]:
        assert call in done, call
    text = " ".join(step_lines(output))
    for words in ("755 root:root", "644 root:root", "trash", "Restore", "409", "subvolume", "volume's own trash"):
        assert words in text, words


def test_the_smb_check_covers_the_flows_of_slice_2(nas):
    status, output = run_check("smb", nas)
    assert status == 0, output
    text = "\n".join(step_lines(output))
    for words in ("marked host account", "2770 root:unos-smb", "660 root:unos-smb", "signed session", "read",
                  "during the session", "NT_STATUS_LOGON_FAILURE", "passdb.tdb", "anonymous",
                  "5 wrong passwords in a row lock the account", "NT_STATUS_ACCOUNT_LOCKED_OUT", "mandatory",
                  "/run/samba/log.samba", "no password", "smb.conf", "host account"):
        assert words in text, words
    # The temporary account had read-write access, then read, then none, then read-write again.
    levels = [tuple(a["level"] for a in body["access"]) for method, path, body, _ in nas.calls
              if method == "PATCH" and body and "access" in body]
    assert levels == [("read",), (), ("write",)]


def test_the_smbconf_check_covers_the_steps_that_were_checked_by_hand(nas):
    """ADR-0309: the steps of shares.smb-conf-and-status (4, 5 and the restart of 3) and of
    shares.edit-and-delete that the smb check leaves out, in the order they were checked by hand
    on 2026-11-09."""
    status, output = run_check("smbconf", nas)
    assert status == 0, output
    text = "\n".join(step_lines(output))
    for words in ("edited by hand", "before an apply", "byte for byte", "smb.conf.unos-replaced", "409",
                  "symbolic link", "not served", "Share path must not contain symbolic links", "refused with 400",
                  "new folder", "2770 root:unos-smb", "keeps the folder", "restart of the supervisor",
                  "healthy after about 8 s"):
        assert words in text, words
    assert nas.restarts == 1
    patches = [(path, body) for method, path, body, _ in nas.calls if method == "PATCH"]
    share = "/shares/smb/unoscheck1a2b3c"
    new_folder = f"{ROOT}/unoscheck1a2b3c-new"
    access = [{"user_id": 2, "username": "test", "level": "read"}]  # the device's regular user
    assert patches == [(share, {"expected_version": 1, "comment": "unos scenario check"}),
                       (share, {"expected_version": 1, "comment": "stale"}),
                       (share, {"expected_version": 2, "access": access}),
                       (share, {"expected_version": 2, "path": new_folder, "access": access}),
                       # after the restart: waits for the startup reconcile, 404, no write
                       ("/shares/smb/unoscheck1a2b3cnone", {})]
    assert ("POST", "/auth/users") not in [(m, p) for m, p, _, _ in nas.calls]


def test_without_a_regular_user_the_smbconf_check_makes_one_without_smb_and_deletes_it(nas):
    del nas.users[2]
    before = nas.snapshot()
    status, output = run_check("smbconf", nas)
    assert status == 0, output
    assert "a regular user for an access entry: unoscheck1a2b3c, made without SMB, as the device has none" in output
    made = [body for method, path, body, _ in nas.calls if (method, path) == ("POST", "/auth/users")]
    assert len(made) == 1 and made[0]["smb_enabled"] is False
    assert "done  clean up: delete the user unoscheck1a2b3c" in output
    assert nas.snapshot() == before and not nas.accounts and not nas.passdb


def test_the_smbconf_check_puts_a_hand_edited_smb_conf_back_after_a_failure(nas):
    """A failure between the hand edit and the first apply leaves smb.conf as the hand edited it:
    the clean-up puts the original back from RAM, and the device is as it was."""
    before = nas.snapshot()
    real = nas.api_shares

    def refuse_the_create(method, parts, body, params, upload):
        if method == "POST":
            raise Refused(503, "Samba could not be reloaded")
        return real(method, parts, body, params, upload)

    nas.api_shares = refuse_the_create
    status, output = run_check("smbconf", nas)
    assert status == 1
    assert re.search(r"^FAIL  \d+\. a new share unoscheck1a2b3c applies smb.conf: .*HTTP 503", output, re.M), output
    assert "FAIL  clean up" not in output and nas.restarts == 0
    assert nas.snapshot() == before, output


def test_after_a_lost_answer_the_smbconf_check_restarts_the_supervisor_for_the_copy_s_problem(nas):
    """The copy's problem stays until the supervisor restarts (ADR-0360): when the steps end
    after an apply copied the hand-edited file, the clean-up deletes the copy and restarts the
    supervisor, so that the Shares page reports no problem of the check."""
    before = nas.snapshot()
    real = nas.api_shares

    def lost(method, parts, body, params, upload):
        answer = real(method, parts, body, params, upload)
        if method == "POST":
            raise Refused(504, "Gateway Timeout")
        return answer

    nas.api_shares = lost
    status, output = run_check("smbconf", nas)
    assert status == 1
    assert "done  clean up: restart the supervisor if the Shares page still names the check's section" in output
    assert nas.restarts == 1 and nas.snapshot() == before, output


# --- the shares check -------------------------------------------------------------------------


def test_the_shares_check_covers_the_sticky_parent_repair_permissions_the_user_s_own_smb_and_a_guest_share(nas):
    """The device checks of shares.acl-aware-parent-check (step 3), shares.repair-permissions,
    shares.self-activation and shares.read-only-guest-share, in that order."""
    status, output = run_check("shares", nas)
    assert status == 0, output
    text = "\n".join(step_lines(output))
    for words in ("1777 root:root", "nobody:nogroup, uid 65534", "refused with 409",
                  f"{ROOT}/unoscheck1a2b3c-sticky/theirs can be changed by other accounts (owner uid 65534, mode 1777)",
                  "the share folder belongs to uid 65534", "2770 root:unos-smb", "keeps their owners", "symbolic link",
                  "the ACL entry stays", "mask::rw-", "visited 9, changed 5, skipped symlink 1, private 2, errors 0",
                  "Share path must not contain symbolic links", "7 characters", scenario.SMB_TOO_SHORT,
                  scenario.SMB_CONTROL, "over 72 bytes (422)", "off, allowed False", "password_needed, allowed True",
                  "without a two-factor code", "signed session", "turns SMB off again", "NT_STATUS_LOGON_FAILURE",
                  "the answer warns", "anonymous (null) session", "NT_STATUS_ACCESS_DENIED",
                  "a null session's share list (smbclient -L) ends with status 0 and names no share", "server signing is mandatory",
                  "no password of the check", "host account"):
        assert words in text, words
    assert "SKIP" not in output
    # The user's own calls carry its token, and only those; the relay kept it in RAM until the clean-up.
    own = [(m, p) for (m, p, _, _), who in zip(nas.calls, nas.callers) if who is not None]
    assert own and all(p.startswith(("/auth/me", "/auth/change-password")) for _, p in own), own
    assert all(who is None for (m, p, _, _), who in zip(nas.calls, nas.callers) if not p.startswith(("/auth/me", "/auth/change-p")))
    assert ("POST", "/auth/me/smb/enable") in own and ("POST", "/auth/me/smb/disable") in own
    assert nas.forgotten == ["/dev/shm/unos-scenario-unoscheck1a2b3c.token"] and not nas.tokens
    passwords = [body for (m, p, body, _), who in zip(nas.calls, nas.callers) if p == "/auth/change-password"]
    assert [len(b["new_password"].encode()) for b in passwords][1] == 73 and "\t" in passwords[0]["new_password"]
    guest = [body for m, p, body, _ in nas.calls if m == "PATCH" and body and "guest_ok" in body]
    assert guest == [{"expected_version": guest[0]["expected_version"], "guest_ok": True}]
    repairs = [p for m, p, _, _ in nas.calls if m == "POST" and p.endswith("/repair-permissions")]
    assert repairs == ["/shares/smb/unoscheck1a2b3ct/repair-permissions", "/shares/smb/unoscheck1a2b3c/repair-permissions",
                       "/shares/smb/unoscheck1a2b3c/repair-permissions"]


def test_with_another_guest_share_the_shares_check_says_so_in_place_of_the_signing_step(nas):
    """With another guest share the guest lines of [global] stay after the check's share is gone
    (ADR-0339): the step that would show signing mandatory again says why it cannot, and the check
    tries no sign-in with a password that Samba has no account for, which map to guest = Bad User
    would take as a guest."""
    nas.shares["DATA"]["guest_ok"] = True
    nas.installed = nas.smb_conf()
    before = nas.snapshot()
    real = nas.smb_list
    tried = []
    nas.smb_list = lambda user, password: (tried.append(user), real(user, password))[1]
    status, output = run_check("shares", nas)
    assert status == 0, output
    assert re.search(r"^PASS  3\. .*; its guest shares: DATA  ", output, re.M), output
    assert re.search(r"^SKIP  \d+\. with the last guest share gone, server signing is mandatory again.*: the device "
                     r"has other guest shares: DATA  \[SD ", output, re.M), output
    assert "a sign-in is not tried, as the device has guest shares" in output
    assert tried == ["unoscheck1a2b3c"]  # only the one in the signed session's step
    assert re.search(r"^scenario shares: OK, \d+ steps passed, 1 skipped$", output, re.M)
    assert nas.snapshot() == before


def test_without_setfacl_the_shares_check_makes_no_acl_entry_and_says_so(nas):
    nas.no_acl = True
    status, output = run_check("shares", nas)
    assert status == 0, output
    assert "it has no setfacl or getfacl" in output
    assert re.search(r"^SKIP  \d+\. the ACL entry stays, .*: the device has no setfacl or getfacl", output, re.M), output
    assert f"{ROOT}/unoscheck1a2b3c/acl.txt" not in nas.fs


def test_the_shares_check_fails_when_the_last_guest_share_leaves_the_guest_lines(nas):
    nas.bugs.add("guest-globals-stay")
    status, output = run_check("shares", nas)
    assert status == 1
    failed = [line for line in step_lines(output) if line.startswith("FAIL  ")]
    assert len(failed) == 2, output
    assert "with the last guest share gone, server signing is mandatory again" in failed[0]
    assert "map to guest = Bad User, not Never" in failed[0]
    assert "the device is as the check found it: smb.conf: its content changed" in failed[1]


def test_the_shares_check_expects_unos_s_texts():
    """The texts that the shares check compares are UNOS's own."""
    from src.auth import schemas
    from src.shares import constants, repair, repair_router, service, smbconf

    assert service.GUEST_WARNING == GUEST_WARNING and GUEST_WARNING.startswith(scenario.GUEST_WARNS)
    assert service.PERMISSIONS_WARNING == PERMISSIONS_WARNING
    assert constants.smb_password_problem("a" * 7) == scenario.SMB_TOO_SHORT
    assert constants.smb_password_problem("unos\tscenario\tcheck") == scenario.SMB_CONTROL
    assert schemas.MAX_PASSWORD_BYTES == scenario.UNOS_PASSWORD_BYTES
    for guest in (False, True):
        lines = dict(smbconf.global_settings(guest))
        assert {key: values[guest] for key, values in scenario.GUEST_GLOBALS.items()} == {
            key: lines[key] for key in scenario.GUEST_GLOBALS}
    unsafe = service.UnsafeFolder(path="/p/x", detail="mode 1777, the share folder belongs to uid 65534",
                                  commands="'chmod 755 /p/x'")
    changed = "/p/x can be changed by other accounts (mode 1777, the share folder belongs to uid 65534)"
    assert changed in unsafe.refusal() and changed in unsafe.warning()
    assert unsafe.problem("docs").startswith(f"Share docs: {changed}")
    fake = FakeNas()
    assert fake.unsafe_text(f"{ROOT}/x", "refusal") is None
    fake.fs[f"{ROOT}/s"] = Node("dir", 0o1777)
    fake.fs[f"{ROOT}/s/x"] = Node("dir", 0o755, "nobody", "nogroup")
    real = service.UnsafeFolder(path=f"{ROOT}/s", detail="mode 1777, the share folder belongs to uid 65534",
                                commands=f"'chmod 755 {ROOT}/s'")
    assert [fake.unsafe_text(f"{ROOT}/s/x", kind, "docs") for kind in ("refusal", "warning", "problem")] == [
        real.refusal(), real.warning(), real.problem("docs")]
    fake.fs[f"{ROOT}/s"].owner = "nobody"
    real = service.UnsafeFolder(path=f"{ROOT}/s", detail="owner uid 65534, mode 1777",
                                commands=f"'chown root:root {ROOT}/s'")
    assert fake.unsafe_text(f"{ROOT}/s/new", "refusal") == real.refusal()
    source = (REPO_ROOT / "supervisor" / "src" / "shares" / "repair_router.py").read_text()
    assert 'detail=f"Share {share.name} is not served: {problem}; change its folder first"' in source
    assert repair_router.NO_RESULT == "No repair has run for this share since UNOS started"
    assert repair.EXECUTABLE_FILE_MODE == 0o770 and constants.SHARE_DIR_MODE == 0o2770
    assert constants.SHARE_FILE_MODE == 0o660


# --- failures, interruptions and leftovers ---------------------------------------------------


def calls_of_the_steps(area: str) -> int:
    """How many API calls a passing run of AREA makes before its first clean-up."""
    nas = FakeNas()
    out = io.StringIO()
    marks = []  # the number of calls when each step's line was written, until the first clean-up

    class Recorder(io.StringIO):
        cleaning = False

        def write(self, text):
            if text.startswith(("done  clean up", "FAIL  clean up")):
                Recorder.cleaning = True
            elif re.match(r"(PASS|SKIP)  \d+\. ", text) and not Recorder.cleaning:
                marks.append(len(nas.calls))
            return out.write(text)

    check = scenario.Check(area, nas, nas, out=Recorder(), run_id="unoscheck1a2b3c")
    assert check.execute(scenario.CHECKS[area]) == 0, out.getvalue()
    return marks[-1]


@pytest.mark.parametrize("mode", ["fail_at", "fail_after"])
@pytest.mark.parametrize("area", scenario.AREAS)
def test_a_failure_at_any_call_of_a_step_still_removes_what_the_check_made(area, mode):
    """Each API call of a passing run fails in turn: before it changes anything (fail_at), or after
    (fail_after: a 500 after the commit, an answer lost to a timeout or a dropped connection). The
    step that made it fails with the call named, no later step runs, and the clean-up leaves the
    device as it was. The exception: a backup whose answer was lost has no name the check knows,
    so the clean-up names it and leaves it, as a backup made by hand meanwhile would look the
    same. A failure in a clean-up itself is reported as such, and the final comparison names what
    is left."""
    steps = calls_of_the_steps(area)
    assert steps >= 3
    text = "injected failure after the change" if mode == "fail_after" else "injected failure"
    for index in range(steps + 8):
        nas = FakeNas()
        before = nas.snapshot()
        setattr(nas, mode, index)
        status, output = run_check(area, nas)
        if index >= len(nas.calls):
            break
        if not nas.injected:  # fail_after of a call that the API refused anyway, as the check expects
            assert status == 0 and nas.snapshot() == before, (index, output)
            continue
        assert status == 1, (index, output)
        method, path = nas.calls[index][:2]
        failure = f"{method} {path} answered HTTP 500: {text}"
        assert failure in output, (index, output)
        failed_steps = [line for line in step_lines(output) if line.startswith("FAIL  ")]
        lost_backup = mode == "fail_after" and path == "/backup/create"
        if lost_backup:
            new = sorted(set(nas.backups) - set(before["backups"]))
            assert len(new) == 1 and re.search(rf"^FAIL  clean up: delete the check's backups: new backups whose names "
                                               rf"the check never got, {new[0]}: ", output, re.M), (index, output)
            assert {**nas.snapshot(), "backups": before["backups"]} == before, (index, output)
        elif index < steps:
            assert len(failed_steps) == 1 and failure in failed_steps[0], (index, output)
            assert "FAIL  clean up" not in output, (index, output)
            assert nas.snapshot() == before, (index, output)
        else:
            assert "FAIL  clean up: " in output or nas.snapshot() == before, (index, output)
        assert re.search(rf"^scenario {area}: FAILED, ", output, re.M), output
        assert not nas.sessions


@pytest.mark.parametrize("area", ["files", "smb", "volumes", "smbconf", "shares", "restore"])
def test_an_interruption_still_removes_what_the_check_made(area):
    steps = calls_of_the_steps(area)
    nas = FakeNas()
    before = nas.snapshot()
    nas.interrupt_at = steps // 2
    out = io.StringIO()
    check = scenario.Check(area, nas, nas, out=out, run_id="unoscheck1a2b3c", interrupts=(Interrupt,))
    assert check.execute(scenario.CHECKS[area]) == 130
    assert "interrupted: removing what the check made" in out.getvalue()
    assert nas.snapshot() == before, out.getvalue()


def test_a_refusal_of_the_device_fails_the_step_and_the_check_still_cleans_up(nas):
    before = nas.snapshot()
    nas.refuse_device = "smb_session"
    status, output = run_check("smb", nas)
    assert status == 1
    assert "the device answered status 1 for smb_session" in output
    assert nas.snapshot() == before, output


@pytest.mark.parametrize("area, command", [("shares", "entries sticky"), ("shares", "entries repair"),
                                           ("shares", "unserve link"), ("smbconf", "unserve link")])
def test_a_device_command_that_fails_after_making_its_thing_leaves_nothing(nas, area, command):
    """ADR-0304: each clean-up is registered before the call that makes its thing, so a command
    over ssh that fails halfway, after its mkdir or its move, leaves nothing on the device."""
    before = nas.snapshot()
    nas.refuse_after = command
    status, output = run_check(area, nas)
    assert status == 1
    assert f"the device answered status 1 after {command}" in output
    assert nas.snapshot() == before, output


def test_a_failed_clean_up_is_reported_and_fails_the_check(nas, monkeypatch):
    real = nas.api_shares

    def no_delete(method, parts, body, params, upload):
        if method == "DELETE":
            raise Refused(503, "Samba could not be reloaded")
        return real(method, parts, body, params, upload)

    monkeypatch.setattr(nas, "api_shares", no_delete)
    status, output = run_check("volumes", nas)
    assert status == 1
    assert re.search(r"^FAIL  clean up: delete the share unoscheck1a2b3cs: .*HTTP 503", output, re.M), output
    assert re.search(r"^FAIL  \d+\. the device is as the check found it: the volumes: new unoscheck1a2b3c; "
                     r"the shares: new unoscheck1a2b3cs", output, re.M), output


def test_an_output_that_cannot_be_written_never_stops_the_clean_up(nas):
    """After SIGHUP from a closed terminal every write fails with EIO, and a closed pipe gives
    EPIPE: nobody reads the results any more, so the steps stop, and the check still removes
    everything it made."""
    before = nas.snapshot()

    class Closed(io.StringIO):
        writes = 0

        def write(self, text):
            Closed.writes += 1
            if Closed.writes > 3:
                raise OSError(5, "Input/output error")
            return super().write(text)

    check = scenario.Check("smb", nas, nas, out=Closed(), run_id="unoscheck1a2b3c", interrupts=(Interrupt,))
    assert check.execute(scenario.CHECKS["smb"]) == 1
    assert check.output_lost and check.number == 4 and nas.snapshot() == before
    assert Closed.writes == 4  # the lost line was the last one tried


def test_an_answer_of_an_unexpected_shape_in_the_clean_up_is_a_fail_and_the_rest_runs(nas):
    steps = calls_of_the_steps("files")
    real = nas.api_files

    def shapeless(method, parts, body, params, upload):
        if len(nas.calls) > steps and parts == ["trash"] and method == "GET":
            return {"problems": []}
        return real(method, parts, body, params, upload)

    nas.api_files = shapeless
    status, output = run_check("files", nas)
    assert status == 1
    assert "FAIL  clean up: delete the check's items in the trash for good: an answer of an unexpected shape (KeyError" in output
    assert "done  clean up: delete /mnt/pools/data/unoscheck1a2b3c for good" in output, output
    assert f"{ROOT}/unoscheck1a2b3c" not in nas.fs and f"{ROOT}/unoscheck1a2b3cv" not in nas.fs


def test_signals_wait_while_the_clean_up_runs(nas):
    """With signals on (main), SIGINT, SIGTERM and SIGHUP are blocked during the clean-up: one
    that arrives then waits until the clean-up has run to its end, and then ends the run as
    interrupted (130). The mask is as before afterwards."""
    done = []

    def check(c):
        c.on_cleanup("second", lambda: done.append("second"))
        c.on_cleanup("first", lambda: (os.kill(os.getpid(), signal.SIGINT), done.append("first")))

    def raise_interrupt(signum, frame):
        raise Interrupt()

    old = signal.signal(signal.SIGINT, raise_interrupt)
    try:
        out = io.StringIO()
        status = scenario.Check("files", nas, nas, out=out, signals=True, interrupts=(Interrupt,)).execute(check)
        assert status == 130 and done == ["first", "second"], out.getvalue()
        assert "interrupted during the clean-up, which ran to its end" in out.getvalue()
        assert signal.SIGINT not in signal.pthread_sigmask(signal.SIG_BLOCK, [])
    finally:
        signal.signal(signal.SIGINT, old)


@pytest.mark.parametrize("area, source", [("smb", "the journal"), ("smb", "Samba's log"), ("backups", "the audit log")])
def test_a_log_that_cannot_be_read_fails_the_scan_for_passwords(nas, area, source):
    nas.unreadable.add(source)
    status, output = run_check(area, nas)
    assert status == 1
    assert re.search(rf"^FAIL  \d+\. no pass\w+ of the check is in .*: {re.escape(source)} could not be read", output, re.M), output


def test_scenario_py_runs_only_under_the_lock_of_scenario_sh(stub):
    result = stub.run(sys.executable, DEV / "scenario.py", "files")
    assert result.returncode == 2 and "run it through scripts/dev/scenario.sh" in result.stderr
    ended = subprocess.Popen(["true"])
    ended.wait()
    result = stub.run(sys.executable, DEV / "scenario.py", "files", UNOS_DEV_LOCK_HELD=str(ended.pid))
    assert result.returncode == 2 and "run it through scripts/dev/scenario.sh" in result.stderr
    stub.write_env(mode=0o644)
    result = stub.run(sys.executable, DEV / "scenario.py", "files", UNOS_DEV_LOCK_HELD=str(os.getpid()))
    assert result.returncode == 2 and "chmod 600" in result.stderr
    assert stub.run(sys.executable, DEV / "scenario.py", "files", "--pool", "a b", UNOS_DEV_LOCK_HELD=str(os.getpid())).returncode == 2
    assert not any(stub.log.iterdir())


@pytest.mark.parametrize("action, path, runs", [
    ("create", "/mnt/pools/data/unoscheck1a2b3c/nest/sub", True),
    ("delete", "/mnt/pools/data/unoscheck1a2b3c/nest/sub", True),
    ("show", "/mnt/pools/data/unoscheck1a2b3c", True),
    ("create", "/mnt/pools/data/media/sub", False),
    ("delete", "/mnt/pools/data/unoscheck1a2b3c", False),
    ("delete", "/mnt/pools/data/unoscheckxyz123/sub", False),
    ("create", "/etc/unoscheck1a2b3c/x", False),
    ("show", "/etc", False),
    ("delete", "/mnt/pools/data/unoscheck1a2b3c/../media", False),
    ("create", "/mnt/pools/data/unoscheck1a2b3c//x", False),
])
def test_the_subvolume_helper_touches_only_a_scenario_check_s_folder(tmp_path, action, path, runs):
    """Only a subvolume below a scenario check's own folder is made or deleted; show reads below
    /mnt/pools only."""
    stub = tmp_path / "btrfs"
    stub.write_text('#!/usr/bin/env bash\necho "btrfs $*" >> "$FAKE_LOG"\n[ "$2" != show ] || exit 1\n')
    stub.chmod(0o755)
    real = Path(path)
    (tmp_path / "log").write_text("")
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "FAKE_LOG": str(tmp_path / "log")}
    result = subprocess.run(["bash", "-s", "--", action, path], input=scenario.REMOTE_SUBVOLUME, env=env,
                            capture_output=True, text=True)
    calls = (tmp_path / "log").read_text()
    if runs:
        assert result.returncode == 0 and f"btrfs subvolume show {real}" in calls, result.stderr
        if action == "create":
            assert f"btrfs subvolume create {real}" in calls
    else:
        assert result.returncode == 2 and calls == "" and "not a subvolume of a scenario check" in result.stderr


@pytest.mark.parametrize("area", ["files", "volumes", "smb", "smbconf", "shares", "offsite", "restore"])
def test_a_check_refuses_to_start_beside_leftovers_of_an_earlier_one(nas, area):
    nas.users[9] = {"username": "unoscheck0a1b2c", "is_admin": False}
    nas.fs[f"{ROOT}/unoscheck0a1b2c"] = Node("dir", 0o755)
    nas.fs[f"{ROOT}/unoscheck0a1b2c-sticky"] = Node("dir", 0o1777)
    nas.shares["unoscheck0a1b2c"] = {"path": f"{ROOT}/unoscheck0a1b2c", "comment": "", "access": [], "version": 1}
    before = nas.snapshot()
    status, output = run_check(area, nas)
    assert status == 1
    assert re.search(r"^FAIL  2\. the device holds nothing of an earlier check: .*unoscheck0a1b2c", output, re.M), output
    assert nas.snapshot() == before
    assert not [c for c in nas.calls if c[0] != "GET"]


def test_the_scrub_check_covers_storage_scrub_and_the_start_through_one_helper(nas):
    """ADR-0314: the steps of storage.scrub and storage.scrub-needs-mounted-pool that were checked by
    hand on 2026-11-09, with Start Scrub's answer of ADR-0313."""
    status, output = run_check("scrub", nas)
    assert status == 0, output
    text = "\n".join(step_lines(output))
    for words in ("refused with 409", "a scheduled scrub of it fails with the same text",
                  "'Pool unoscheck1a2b3c is not mounted at /mnt/pools/unoscheck1a2b3c'", "its last scrub finished",
                  "its progress moving", "GiB scrubbed while it ran",
                  "Start Scrub of data answers running, and so does the status right after it (ADR-0313): running, "
                  "then running", "until it ends, finished without errors", "the status says it was cancelled: "
                  "aborted, then aborted", "Run now", "Scrub started, then running", "the scheduled scrub of data ends",
                  "its run stays in the Scheduler's history"):
        assert words in text, words
    assert nas.scrubs == 3 and [r["output"] or r["error"] for r in nas.runs] == [
        "Pool unoscheck1a2b3c is not mounted at /mnt/pools/unoscheck1a2b3c", "Scrub started"]


def test_on_a_pool_with_much_data_the_scrub_check_cancels_its_scrubs(nas):
    nas.pool_used = 100 * scenario.GIB
    status, output = run_check("scrub", nas)
    assert status == 0, output
    assert re.search(r"^SKIP  \d+\. btrfs reports the scrub of data until it ends: 100 GiB used: it is cancelled "
                     r"instead", output, re.M), output
    assert re.search(r"^PASS  \d+\. Cancel Scrub stops the scheduled scrub of data once btrfs has recorded it",
                     output, re.M), output
    assert nas.scrubs == 2 and nas.scrub["status"] == "aborted"


@pytest.mark.parametrize("leftover", ["a task", "a folder"])
def test_the_scrub_check_does_not_start_beside_leftovers_of_an_earlier_one(nas, leftover):
    if leftover == "a task":
        nas.tasks["task_9"] = {"id": "task_9", "name": "unoscheck0a1b2c", "task_type": "scrub", "target_pool": "data"}
    else:
        nas.fs["/mnt/pools/unoscheck0a1b2c"] = Node("dir", 0o755)
    before = nas.snapshot()
    status, output = run_check("scrub", nas)
    assert status == 1
    assert re.search(r"^FAIL  2\. the device holds nothing of an earlier check: .*unoscheck0a1b2c", output, re.M), output
    assert nas.snapshot() == before
    assert not [c for c in nas.calls if c[0] != "GET"]


def test_the_scrub_check_does_not_start_beside_a_scrub_that_runs(nas):
    nas.start_scrub()
    status, output = run_check("scrub", nas)
    assert status == 1
    assert re.search(r"^FAIL  1\. .*: a scrub of data runs: wait for its end, or cancel it", output, re.M), output
    assert nas.scrubs == 1 and not [c for c in nas.calls if c[0] != "GET"]


@pytest.mark.parametrize("action, name, code", [
    ("make", "unoscheck1a2b3c", 0),
    ("make", "data", 2),
    ("make", "unoscheckxyz123", 2),
    ("make", "../unoscheck1a2b3c", 2),
    ("make", "unoscheck1a2b3c/x", 2),
    ("remove", "data", 2),
    ("list", "", 0),
])
def test_the_pool_folder_helper_touches_only_a_scenario_check_s_folder(tmp_path, action, name, code):
    """REMOTE_POOL_FOLDER, which runs without the read-only namespace: it makes and removes only a
    folder named unoscheck and six hex digits right below the pools' base (ADR-0314)."""
    base = tmp_path / "pools"
    (base / "data").mkdir(parents=True)
    env = {"PATH": "/usr/bin:/bin", "UNOS_DEV_POOLS": str(base)}
    result = subprocess.run(["bash", "-s", "--", action, name], input=scenario.REMOTE_POOL_FOLDER, env=env,
                            capture_output=True, text=True)
    assert result.returncode == code, result.stderr
    assert sorted(p.name for p in base.iterdir()) == (["data", name] if action == "make" and code == 0 else ["data"])
    assert not (tmp_path / "unoscheck1a2b3c").exists()
    if code == 0:
        assert result.stdout.split() == sorted(p.name for p in base.iterdir())


def test_the_pool_folder_helper_removes_only_an_empty_folder_that_is_no_mount_point(tmp_path):
    base = tmp_path / "pools"
    for name in ("unoscheck1a2b3c", "unoscheck2b3c4d", "unoscheck3c4d5e"):
        (base / name).mkdir(parents=True)
    (base / "unoscheck2b3c4d" / "kept").write_text("x")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "mountpoint").write_text(f'#!/usr/bin/env bash\n[ "$3" = "{base}/unoscheck3c4d5e" ]\n')
    (stubs / "mountpoint").chmod(0o755)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "UNOS_DEV_POOLS": str(base)}
    codes = [subprocess.run(["bash", "-s", "--", "remove", name], input=scenario.REMOTE_POOL_FOLDER, env=env,
                            capture_output=True, text=True).returncode
             for name in ("unoscheck1a2b3c", "unoscheck2b3c4d", "unoscheck3c4d5e")]
    assert codes == [0, 1, 0]
    assert sorted(p.name for p in base.iterdir()) == ["unoscheck2b3c4d", "unoscheck3c4d5e"]


# --- the defects the checks exist for -------------------------------------------------------


@pytest.mark.parametrize("area, bug, words", [
    ("smb", "no-close-share", "lowering the access to read during the session makes its next write fail"),
    ("smb", "sessions-stay", "a new password set by an administrator ends the user's open session"),
    ("smb", "share-folder-mode", "2770 root:unos-smb"),
    ("smb", "upload-mode", "660 root:unos-smb"),
    ("smb", "no-history", "Samba's password history is '0', not 3"),
    ("smb", "no-lockout", "Samba's bad lockout attempt is '0', not 5"),
    ("smb", "long-lockout", "Samba's lockout duration is '30', not 5"),
    ("smb", "wrong-password-writes", "the replaced password is refused"),
    ("smb", "count-unwritten", "a wrong password is refused"),
    ("smb", "lockout-ignored", "after 5 wrong passwords the right one answered status 0"),
    ("smb", "counts-twice", "wrong password 4 of 5 answered NT_STATUS_ACCOUNT_LOCKED_OUT"),
    ("smb", "locked-writes", "while the account was locked"),
    ("smb", "samr-change-accepted", "smbpasswd -r answered status 0"),
    ("smb", "samr-refusal-writes", "passdb.tdb changed at"),
    ("smb", "samr-refusal-changes-password", "the password answered NT_STATUS_LOGON_FAILURE after the refused change"),
    ("smb", "log-password", "no password of the check"),
    ("files", "volume-deletable", "refused with 409"),
    ("files", "upload-mode", "644 root:root"),
    ("volumes", "volume-delete-ignores-shares", "names the share"),
    ("volumes", "copies-not-listed", "listed with the volume's restored copies"),
    ("volumes", "copy-delete-ignores-shares", "the copy's Delete is refused while the share"),
    ("smbconf", "status-misses-replaced-file", "names smb.conf as not UNOS's"),
    ("smbconf", "no-replaced-copy", "byte for byte"),
    ("smbconf", "access-without-folder", "access without a new folder is refused"),
    ("smbconf", "restart-writes", "a restart of the supervisor writes no file"),
    ("scrub", "no-mount-check", "is refused with 409"),
    ("scrub", "start-answers-idle", "Start Scrub answered idle"),
    ("scrub", "unrecorded-is-idle", "the status right after the start is idle"),
    ("scrub", "scrub-errors", "it found 2 errors"),
    ("scrub", "cancel-keeps-running", "the scrub ended finished"),
    ("scrub", "runs-go-with-the-task", "its run is gone"),
    ("offsite", "offsite-check-fails", "the copy failed at check"),
    ("offsite", "offsite-restore-differs", "the restored files hold other bytes"),
    ("offsite", "log-offsite-password", "no password of the check"),
    ("shares", "foreign-parent-allowed", "a new share folder in theirs, a sticky folder of another account, is refused"),
    ("shares", "sticky-excuses-foreign", "a share of theirs, a folder of another account in the sticky folder of root"),
    ("shares", "repair-follows-links", "it leaves the private file and folder"),
    ("shares", "repair-opens-private", "it leaves the private file and folder"),
    ("shares", "repair-drops-acl", "the ACL entry stays"),
    ("shares", "repair-unserved", "Repair permissions refuses it with 409"),
    ("shares", "smb-takes-short-password", "SMB refuses the user's password of 7 characters"),
    ("shares", "self-off-keeps-samba", "the user turns SMB off again"),
    ("shares", "no-guest-warning", "guest access turned on for the share: the answer warns"),
    ("shares", "no-guest-globals", "guest access turned on for the share: the answer warns"),
    ("shares", "guest-refused", "an anonymous (null) session to the share lists it"),
    ("shares", "anonymous-writes", "an anonymous (null) session to the share lists it"),
    ("shares", "log-password", "no password of the check"),
    ("restore", "backup-holds-smb-conf", "a backup made by hand holds no smb.conf"),
    ("restore", "scheduled-backup-holds-smb-conf", "of a disabled scheduled backup task"),
    ("restore", "restore-proves-nothing", "with nothing changed since"),
    ("restore", "restore-keeps-every-password", "needs its password again"),
    ("restore", "restore-installs-the-archived-conf", "UNOS's rendering of the restored database"),
    ("restore", "restore-keeps-stale-account", "which no restored row has, are removed"),
    ("restore", "restore-next-uid", "which the backup's next uid would have given again"),
    ("restore", "restore-apply-fails", "with nothing changed since"),
    ("restore", "restore-rewrites-smb-conf", "byte for byte"),
    ("restore", "reenable-new-uid", "under its uid"),
])
def test_the_checks_fail_on_the_defects_they_exist_for(nas, area, bug, words):
    before = nas.snapshot()
    nas.bugs.add(bug)
    status, output = run_check(area, nas)
    assert status == 1, output
    failed = [line for line in step_lines(output) if line.startswith("FAIL  ")]
    assert len(failed) == 1 and words in failed[0], output
    if bug not in ("volume-delete-ignores-shares", "volume-deletable"):
        assert nas.snapshot() == before, output


@pytest.mark.parametrize("found", ["ten copies", "an smb.conf that is not UNOS's", "the same, before any apply",
                                   "a section that UNOS did not write", "a problem"])
def test_the_smbconf_check_does_not_start_beside_ten_copies_a_foreign_smb_conf_or_a_problem(nas, found):
    """With 10 copies smb.conf.unos-replaced-<time>, the check's copy would make UNOS delete the
    oldest, which the check did not make (ADR-0360); an apply would also copy a section that UNOS
    did not write, which the check's clean-up deletes with the copy (it keeps a header of UNOS's,
    so the status does not name it, ADR-0249); an smb.conf that is not UNOS's, which the status
    names only once an apply has run since the start, or a problem on the Shares page would make
    the check's findings ambiguous. The check changes nothing then."""
    if found == "ten copies":
        for day in range(1, 11):
            nas.samba[f"smb.conf.unos-replaced-202611{day:02d}_120000_000000"] = "[global]\n\n[old]\n   path = /srv/old\n"
    elif found == "a problem":
        nas.replaced_problem = COPIED.format("old", "smb.conf.unos-replaced-20261101_120000_000000")
    elif found == "a section that UNOS did not write":
        nas.installed += "\n[owner]\n   path = /srv/owner\n"
    else:
        nas.installed = "[global]\n   workgroup = WORKGROUP\n"
        nas.last_apply_ok = None if found == "the same, before any apply" else True
    before = nas.snapshot()
    status, output = run_check("smbconf", nas)
    assert status == 1
    assert re.search(r"^FAIL  3\. smb.conf is UNOS's, the Shares page reports no problem", output, re.M), output
    assert not [c for c in nas.calls if c[0] != "GET"] and nas.snapshot() == before


def test_the_smbconf_check_keeps_the_earlier_copies(nas):
    """Earlier copies smb.conf.unos-replaced-<time> stay through the check's apply and clean-up,
    and the Shares page names them beside the check's copy (ADR-0360)."""
    earlier = [f"smb.conf.unos-replaced-202611{day:02d}_120000_000000" for day in range(1, 10)]
    for name in earlier:
        nas.samba[name] = f"[global]\n\n[{name[-6:]}]\n   path = /srv/old\n"
    before = nas.snapshot()
    status, output = run_check("smbconf", nas)
    assert status == 0, output
    assert "PASS  3. smb.conf is UNOS's" in output and ": 9 earlier copies" in output
    assert "smb.conf.unos-replaced-20261112_120001_000000" in output
    assert nas.snapshot() == before


def test_the_smbconf_check_fails_when_an_apply_deletes_an_earlier_copy(nas):
    nas.samba["smb.conf.unos-replaced-20261101_120000_000000"] = "[global]\n\n[old]\n   path = /srv/old\n"
    nas.bugs.add("deletes-earlier-copies")
    status, output = run_check("smbconf", nas)
    assert status == 1
    failed = [line for line in step_lines(output) if line.startswith("FAIL  ")]
    assert failed[0].startswith("FAIL  7. ") and "the earlier copies stay" in failed[0], output
    assert "the apply deleted the earlier copies smb.conf.unos-replaced-20261101_120000_000000" in failed[0]
    # The check reports what it could not put back.
    assert failed[1:] == [line for line in failed if "gone smb.conf.unos-replaced-20261101_120000_000000" in line]


def test_the_smbconf_check_expects_unos_s_texts():
    """The texts the check reads on the Shares page are UNOS's: service.py renders them, and
    test_share_api.py asserts them against the real code."""
    from src.shares import service

    assert service.SMB_CONF_REPLACED == CONF_REPLACED and CONF_REPLACED.startswith(scenario.CONF_NOT_UNOS)
    api_tests = (REPO_ROOT / "supervisor" / "tests" / "test_share_api.py").read_text()
    copy = "smb.conf.unos-replaced-20261112_120001_000000"
    legacy = COPIED.format("legacy", copy)
    assert '"' + legacy[:legacy.index(copy)] + '" + copy + ": "' in api_tests
    assert '"create them again on the Shares page"]' in api_tests and legacy.startswith(scenario.copied_problem("legacy", copy))
    apply_tests = (REPO_ROOT / "supervisor" / "tests" / "test_share_apply.py").read_text()
    assert ('f"on the Shares page' + EARLIER_COPIES.format("{") in apply_tests
            and EARLIER_COPIES.format("").endswith("(UNOS keeps the newest 10): "))
    from src.shares import smb, smbconf

    assert scenario.REPLACED_COPIES_KEPT == smb.COPIES_KEPT == 10
    named = f"smb.conf.{smb.REPLACED}-" + datetime.now(timezone.utc).strftime(smb._COPY_TIME)
    assert scenario.REPLACED_COPY_NAME.fullmatch(named) and scenario.REPLACED_COPY_NAME.pattern.endswith(smb._COPY_TIME_PATTERN)

    assert scenario.UNOS_SHARE_LINES == smbconf._FIXED_SHARE_LINES
    assert set(scenario.NOT_SHARES) == {"global"} | smbconf.NOT_SHARES
    source = (REPO_ROOT / "supervisor" / "src" / "shares" / "service.py").read_text()
    assert 'f"Share {label} is not served: {problem}"' in source


def test_the_smart_check_refuses_a_partition_where_a_disk_has_one(nas):
    nas.partitions = [f"{DISK}1"]
    status, output = run_check("smart", nas)
    assert status == 0, output
    assert f"/health/disk/{DISK}1" in [path for _, path, _, _ in nas.calls] and f"mmcblk1, sdz, {DISK}1" in output


def test_the_smb_check_expects_unos_s_account_policies():
    """Step 3 of the smb check expects Samba's account policies as UNOS's reconcile sets them
    (ADR-0278, ADR-0334), and its helper on the device reads each of them."""
    from src.shares import accounts

    assert dict(scenario.SAMBA_POLICIES.values()) == dict(accounts.ACCOUNT_POLICIES)
    assert scenario.LOCKOUT_ATTEMPTS == accounts.BAD_LOCKOUT_ATTEMPTS == 5
    for key, (name, _) in scenario.SAMBA_POLICIES.items():
        assert f'echo "{key} $(pdbedit -P "{name}" 2> /dev/null | tail -n 1)"' in scenario.REMOTE_SAMBA_USERS


def test_the_smb_check_locks_its_account_last_and_deletes_it_locked(nas):
    """ADR-0334: the lock lasts 5 minutes, so the lockout step is the last that signs in, and the
    check deletes its account while it is still locked."""
    out = io.StringIO()
    check = scenario.Check("smb", nas, nas, out=out, run_id="unoscheck1a2b3c")
    signed_in, deleted = [], []
    sign_in, request = nas.sign_in, nas.request

    def signing_in(user, password):
        signed_in.append(check.number)
        return sign_in(user, password)

    def requesting(method, path, **kw):
        if method == "DELETE" and path.startswith("/auth/users/"):
            deleted.append(sorted(name for name, entry in nas.passdb.items() if entry["locked"]))
        return request(method, path, **kw)

    nas.sign_in, nas.request = signing_in, requesting
    status = check.execute(scenario.CHECKS["smb"])
    output = out.getvalue()
    assert status == 0, output
    lock = [int(line.split(".")[0].split()[1]) for line in step_lines(output)
            if "5 wrong passwords in a row lock the account" in line]
    assert lock and max(signed_in) == lock[0], output
    assert deleted == [["unoscheck1a2b3c"]]


def test_the_smb_check_asks_for_a_samr_password_change_that_samba_refuses(nas):
    """shares.password-changes-only-through-unos (ADR-0342): once the user's password signs in, the
    check asks Samba to change it over SAMR with smbpasswd -r, the stand-in for Windows' Change a
    password, signed in with that password; Samba refuses with NT_STATUS_PASSWORD_RESTRICTION, and
    the password still signs in. The step comes before the steps with wrong passwords, so that no
    failure is counted then and a refused change leaves passdb.tdb as it was."""
    changes, change = [], nas.smb_change_password

    def changing(user, old, new):
        account = nas.passdb[user]
        changes.append((user, old == account["password"], new != old, account["bad"]))
        return change(user, old, new)

    nas.smb_change_password = changing
    status, output = run_check("smb", nas)
    assert status == 0, output
    assert changes == [("unoscheck1a2b3c", True, True, 0)]
    steps = step_lines(output)
    samr = [n for n, line in enumerate(steps) if "through SAMR" in line]
    assert len(samr) == 1 and "NT_STATUS_PASSWORD_RESTRICTION" in steps[samr[0]], output
    assert "the new password signs in" in steps[samr[0] - 1] and "a wrong password" in steps[samr[0] + 1], output


def test_the_smb_check_reports_an_answer_of_smbpasswd_that_it_cannot_read_with_its_output(nas):
    """A refusal in a text that the check does not know, such as a later Samba's, fails the SAMR
    step with smbpasswd's exit status and the end of its output, so that the report shows what
    Samba said; the check still removes what it made."""
    before, change = nas.snapshot(), nas.smb_change_password
    text = SAMR_REFUSED + "A text that a later Samba prints."

    def changing(user, old, new):
        change(user, old, new)
        return scenario.SmbPasswordChange(1, text)

    nas.smb_change_password = changing
    status, output = run_check("smb", nas)
    assert status == 1, output
    failed = [line for line in step_lines(output) if line.startswith("FAIL  ")]
    assert len(failed) == 1 and "through SAMR" in failed[0], output
    assert f"smbpasswd -r answered status 1: {text}" in failed[0], output
    assert nas.snapshot() == before, output


@pytest.mark.parametrize("rc, output, status", [
    # Samba 4.22: MS-ERREF's description of the status.
    (1, SAMR_REFUSAL, "NT_STATUS_PASSWORD_RESTRICTION"),
    (1, SAMR_REFUSAL.upper(), "NT_STATUS_PASSWORD_RESTRICTION"),
    (1, SAMR_REFUSED + "When trying to update a password, this return status indicates that the value provided as the "
     "current password is not correct.", "NT_STATUS_WRONG_PASSWORD"),
    (1, SAMR_REFUSED + "The user account has been automatically locked because too many invalid logon attempts or "
     "password change attempts have been requested.", "NT_STATUS_ACCOUNT_LOCKED_OUT"),
    (1, SAMR_REFUSED + "Indicates a referenced user name and authentication information are valid, but some user "
     "account restriction has prevented successful authentication (such as time-of-day restrictions).",
     "NT_STATUS_ACCOUNT_RESTRICTION"),
    (1, SAMR_REFUSED + "The attempted logon is invalid. This is either due to a bad username or authentication "
     "information.", "NT_STATUS_LOGON_FAILURE"),
    (1, SAMR_REFUSED + "{Access Denied} A process has requested access to an object but has not been granted those "
     "access rights.", "NT_STATUS_ACCESS_DENIED"),
    (1, SAMR_REFUSED + "{Network Access Denied} Network access is denied.", "status 1"),
    (1, SAMR_REFUSED + "NT_STATUS_PASSWORD_RESTRICTION", "NT_STATUS_PASSWORD_RESTRICTION"),
    # Older releases: the short text of Samba's own table.
    (1, "machine 127.0.0.1 rejected the password change: Error was : Password restriction.\n",
     "NT_STATUS_PASSWORD_RESTRICTION"),
    (1, "machine 127.0.0.1 rejected the password change: Error was : Account restriction.\n",
     "NT_STATUS_ACCOUNT_RESTRICTION"),
    # An error of smbpasswd's own connection, a text that the check does not know, a change.
    (1, "Could not connect to machine 127.0.0.1: NT_STATUS_LOGON_FAILURE\n", "NT_STATUS_LOGON_FAILURE"),
    (1, SAMR_REFUSED + "A text that a later Samba prints.", "status 1"),
    (1, "Mismatch - password unchanged.\n", "status 1"),
    (0, "Password changed for user unoscheck1a2b3c on 127.0.0.1.\n", "status 0"),
])
def test_a_samr_password_change_is_read_as_its_nt_status(rc, output, status):
    """smbpasswd -r names a refusal of the server by Samba's description of its NT status
    (get_friendly_nt_error_msg): MS-ERREF's in Samba 4.22, a short text in older releases, the
    status's name for a status without one; it names an error of its own connection by the status
    itself. A text that the check does not know is read as smbpasswd's exit status."""
    assert scenario.SmbPasswordChange(rc, output).status == status
    assert scenario.SmbPasswordChange(rc, output).ok is (rc == 0)


def test_with_a_guest_share_the_smb_check_tries_no_null_session(nas, monkeypatch):
    """A guest share renders restrict anonymous = 1 and map to guest = Bad User (ADR-0339): a
    null session may then connect to the guest shares, so the check does not expect its refusal, and
    the [global] step of the shares without guests is skipped."""
    nas.guest_shares = ["DATA"]
    monkeypatch.setattr(nas, "smb_anonymous", lambda: pytest.fail("a null session was tried"))
    status, output = run_check("smb", nas)
    assert status == 0, output
    assert "a null session is not tried, as the device has guest shares" in output
    assert re.search(r"^SKIP  \d+\. with no guest share, .*: the device has guest shares: \['DATA'\]", output, re.M)


def test_the_volumes_check_takes_either_answer_to_a_quota(nas):
    status, output = run_check("volumes", nas)
    assert status == 0 and "refused while quotas are off" in output, output
    nas = FakeNas()
    nas.quotas = True
    status, output = run_check("volumes", nas)
    assert status == 0 and "made with a limit of 1 GiB" in output, output


def test_the_volumes_check_restores_a_snapshot_and_deletes_the_copy_and_then_the_volume(nas):
    """storage.restore-copies-deletable (ADR-0358): a snapshot of the check's volume, its restore, the
    copy listed with the volume's restored copies and a subvolume on the host, its Delete refused while
    a share uses it and done once the share is gone, and the volume's Delete after it."""
    status, output = run_check("volumes", nas)
    assert status == 0, output
    copy = f"{ROOT}/unoscheck1a2b3c/restore_20261112_100001"
    done = [(method, path, body, params) for method, path, body, params in nas.calls if path.startswith("/snapshots")]
    assert done == [
        ("POST", "/snapshots/", {"subvolume": f"{ROOT}/unoscheck1a2b3c", "name": "unoscheck1a2b3c"}, {}),
        ("GET", "/snapshots/", None, {"subvolume": f"{ROOT}/unoscheck1a2b3c"}),
        ("POST", "/snapshots/restore", {"snapshot_path": f"{ROOT}/unoscheck1a2b3c/.snapshots/unoscheck1a2b3c"}, {}),
        ("GET", "/snapshots/copies", None, {"subvolume": f"{ROOT}/unoscheck1a2b3c"}),
        ("GET", "/snapshots/", None, {"subvolume": f"{ROOT}/unoscheck1a2b3c"}),
        ("DELETE", "/snapshots/copies/restore_20261112_100001", None, {"subvolume": f"{ROOT}/unoscheck1a2b3c"}),
        ("GET", "/snapshots/copies", None, {"subvolume": f"{ROOT}/unoscheck1a2b3c"}),
        ("DELETE", "/snapshots/copies/restore_20261112_100001", None, {"subvolume": f"{ROOT}/unoscheck1a2b3c"}),
        ("GET", "/snapshots/copies", None, {"subvolume": f"{ROOT}/unoscheck1a2b3c"}),
        ("DELETE", "/snapshots/unoscheck1a2b3c", None, {"subvolume": f"{ROOT}/unoscheck1a2b3c"}),
        # The clean-up of the volume looks for its copies first, which its Delete would refuse: the
        # volume is gone by then (404).
        ("GET", "/snapshots/copies", None, {"subvolume": f"{ROOT}/unoscheck1a2b3c"}),
    ]
    text = "\n".join(step_lines(output))
    for words in ("a snapshot unoscheck1a2b3c of the volume is made and listed",
                  "Restore of the snapshot makes a copy in the volume, a subvolume on the host, listed with the "
                  f"volume's restored copies and not with its snapshots: {copy}",
                  "the copy's Delete is refused while the share unoscheck1a2b3cs uses it, and names the share",
                  "with the share deleted, the copy's Delete removes it, also on the host",
                  "with its snapshot deleted, Delete removes the volume, also on the host"):
        assert words in text, words
    assert copy not in nas.fs and f"{ROOT}/unoscheck1a2b3c" not in nas.fs


def test_the_volumes_check_removes_its_copy_and_snapshot_after_a_failed_step(nas):
    """A step that fails while the volume holds the check's snapshot and copy: the clean-up deletes them
    before the volume, whose Delete btrfs would refuse, also a copy that the list misses."""
    before = nas.snapshot()
    nas.bugs.add("copies-not-listed")
    status, output = run_check("volumes", nas)
    assert status == 1
    assert "done  clean up: delete the volume unoscheck1a2b3c with its restored copies and snapshots" in output
    assert nas.snapshot() == before, output


def test_the_fake_refuses_to_delete_a_volume_that_holds_a_copy_as_btrfs_does(nas):
    nas.fs[f"{ROOT}/media/restore_20261112_100001"] = Node("subvolume", 0o755)

    answer = nas.request("DELETE", "/storage/pools/data/volumes/media")

    assert answer.status_code == 409 and "contains snapshots or other subvolumes" in answer.text
    assert f"{ROOT}/media" in nas.fs


def test_the_volumes_check_reads_the_quota_on_the_host(nas):
    """The API's list of volumes carries no quota (pools.list_volumes): with quotas on, the check
    reads the limit of the new volume's quota group on the host, and a volume made without it
    fails the check."""
    nas.quotas = True
    real = nas.api_storage

    def no_limit(method, parts, body, params, upload):
        answer = real(method, parts, body, params, upload)
        nas.quota_of.clear()  # btrfs qgroup limit did not take
        return answer

    nas.api_storage = no_limit
    status, output = run_check("volumes", nas)
    assert status == 1
    assert re.search(r"^FAIL  4\. a volume with a quota of 1 GiB: btrfs shows the limit none on /mnt/pools/data/unoscheck1a2b3cq, "
                     r"not 1073741824", output, re.M), output


def test_a_quota_that_could_not_be_set_is_no_refusal_of_quotas_that_are_off(nas):
    """Only the 409 for quotas that are not enabled counts as that refusal: another 409 about a
    quota (one that could not be set, on a pool with quotas on) fails the step."""
    real = nas.api_storage

    def broken(method, parts, body, params, upload):
        if method == "POST" and body.get("quota"):
            raise Refused(409, f"Could not set the quota of volume {body['name']}: busy. The volume was removed again.")
        return real(method, parts, body, params, upload)

    nas.api_storage = broken
    status, output = run_check("volumes", nas)
    assert status == 1 and "the 409 is not the one for quotas that are off" in output, output


def test_the_smart_check_refuses_what_is_not_a_disk_and_starts_a_short_test(nas):
    status, output = run_check("smart", nas)
    assert status == 0, output
    paths = [path for method, path, _, _ in nas.calls]
    for name in ("mmcblk1", "sdz"):
        assert f"/health/disk/{name}" in paths and f"/storage/disks/{name}/smart" in paths, name
    assert f"/health/disk/{DISK}1" not in paths and "(no listed disk has a partition)" in output
    assert ("POST", f"/health/disk/{DISK}/test", None, {"test_type": "short"}) in nas.calls
    assert "sda: PASSED, 33 C, 2 attributes" in output


def test_the_offsite_check_covers_the_copy_its_snapshots_and_both_restores(nas):
    """ADR-0337: the steps of backup.off-device-copies that run on the device, on a folder of the pool
    as the source and another as the disk target, and the device as the check found it."""
    status, output = run_check("offsite", nas)
    assert status == 0, output
    text = "\n".join(step_lines(output))
    for words in ("no off-device copy is set up", "700 root:root", "on the disk it copies", "Run now", "restore",
                  "offsite_20261111_030001.tar.gz", "restored-", "byte for byte", "no password", "Forget this target"):
        assert words in text, words
    done = [(method, path) for method, path, _, _ in nas.calls]
    for call in [("PUT", "/offsite/settings"), ("POST", "/offsite/run"), ("GET", "/offsite/snapshots"),
                 ("POST", "/offsite/restore-configuration"), ("POST", "/offsite/restore-files"),
                 ("DELETE", "/offsite/settings")]:
        assert call in done, call
    settings = next(body for method, path, body, _ in nas.calls if (method, path) == ("PUT", "/offsite/settings"))
    assert settings["path"] == f"{ROOT}/unoscheck1a2b3ct" and settings["sources"] == [f"{ROOT}/unoscheck1a2b3c"]
    assert len(settings["password"]) >= 12


@pytest.mark.parametrize("text", ["2026-11-11T03:00:05.123456789Z", "2026-11-11T04:00:05.5+01:00",
                                  "2026-11-11T03:00:05+00:00"])
def test_the_offsite_check_reads_restics_times_as_python_3_9_does(text):
    """restic writes nanoseconds and Z for UTC (Go's RFC 3339), which datetime.fromisoformat of
    Python 3.9, a workstation's python3, reads neither: the name of a restored backup still comes out."""
    assert scenario.utc_stamp(text) == "20261111_030005"


def test_the_offsite_check_does_not_start_beside_a_task_that_copies(nas):
    """A task of the Scheduler would copy to the check's target while it runs, and fail every run
    once the check forgot it again."""
    nas.tasks["task_9"] = {"id": "task_9", "name": "Nightly off-device copy", "task_type": "offsite_copy",
                           "enabled": True, "frequency": "daily", "hour": 3, "minute": 0}
    before = nas.snapshot()
    status, output = run_check("offsite", nas)
    assert status == 1
    assert re.search(r"^FAIL  3\. no off-device copy is set up.*: the Scheduler has a task that copies: "
                     r"Nightly off-device copy", output, re.M), output
    assert nas.snapshot() == before
    assert not [c for c in nas.calls if c[0] != "GET"]


def test_the_offsite_check_does_not_start_beside_a_copy_that_is_set_up(nas):
    nas.offsite = {"kind": "disk", "path": "/mnt/usb/copy", "sources": [ROOT]}
    nas.offsite_password = "the owner's password"
    before = nas.snapshot()
    status, output = run_check("offsite", nas)
    assert status == 1
    assert re.search(r"^FAIL  3\. no off-device copy is set up, which the check would replace: one is set up", output,
                     re.M), output
    assert nas.snapshot() == before
    assert not [c for c in nas.calls if c[0] != "GET"]


def test_the_restore_check_restores_its_own_backup_twice(nas):
    """ADR-0341: the device steps of shares.restore-keeps-smb-with-proof and of
    backup.no-old-backup-import, on a backup that the check made after its baseline, which is the
    only one it restores; the scheduled backup task is disabled and keeps every backup (no
    keep_count, which would delete the device's own scheduled backups)."""
    status, output = run_check("restore", nas)
    assert status == 0, output
    text = "\n".join(step_lines(output))
    for words in ("holds no smb.conf", "manifest.json, unos.db, scheduled_tasks.json", "Run now",
                  "with nothing changed since", "byte for byte", "restored database, smb_config",
                  "a new password, which signs in", "SMB must be turned on again for unoscheck1a2b3cc",
                  "UNOS's rendering of the restored database", "which no restored row has, are removed",
                  "its password of the backup", "NT_STATUS_LOGON_FAILURE", "set by an administrator",
                  "which the backup's next uid would have given again", "no password of the check"):
        assert words in text, words
    calls = [(method, path) for method, path, _, _ in nas.calls]
    created = calls.index(("POST", "/backup/create"))
    restores = [i for i, (method, path) in enumerate(calls) if path.startswith("/backup/restore/")]
    assert len(restores) == 2 and created < restores[0]
    assert len(nas.restored) == 2 and nas.restored[0] == nas.restored[1]
    assert nas.restored[0].startswith("unos_backup_") and nas.restored[0] not in FakeNas().backups
    task = next(body for method, path, body, _ in nas.calls if (method, path) == ("POST", "/scheduler/tasks"))
    assert (task["task_type"], task["enabled"]) == ("backup", False) and "keep_count" not in task
    assert len(task["passphrase"]) >= 16
    # The user made after the second restore got the uid after the dropped user's, not the dropped one's.
    dropped = int(re.search(r"gets write access to the share: uid (\d+)", text).group(1))
    fresh = int(re.search(r"would have given again \(ADR-0109\): (\d+)", text).group(1))
    assert fresh == dropped + 1


def test_the_restore_check_does_not_start_beside_a_share_whose_access_a_restore_removes(nas):
    """The post-restore step removes the access of a share that is not served (ADR-0145): the check
    restores the device's own database, so on such a device it would change a share of the owner."""
    nas.fs[f"{ROOT}/linked"] = Node("link", 0o777, content="shared")
    nas.shares["LINKED"] = {"path": f"{ROOT}/linked", "comment": "", "version": 1,
                            "access": [{"user_id": 2, "username": "test", "level": "read"}]}
    before = nas.snapshot()
    status, output = run_check("restore", nas)
    assert status == 1
    assert re.search(r"^FAIL  3\. no share that is not served has access, .*: shares that are not served have access, "
                     r"which a restore removes: LINKED", output, re.M), output
    assert not [c for c in nas.calls if c[0] != "GET"] and nas.snapshot() == before


@pytest.mark.parametrize("found, starts", [(3, True), (4, False)])
def test_the_restore_check_pushes_out_none_of_the_copies_of_the_database_and_removes_its_own(nas, found, starts):
    """Each restore keeps a copy of the database from before it, and UNOS keeps the newest 5
    (src/backup/router.py): the check's two restores start only when they push out no copy that was
    there before, and the clean-up removes their copies, also that of a restore whose answer was lost."""
    nas.copies = [f"unos.db.pre-restore-20261101_0900{n:02d}_000000" for n in range(found)]
    before = nas.snapshot()
    status, output = run_check("restore", nas)
    if starts:
        assert status == 0, output
        assert f"push out none of the copies of the database from before a restore, of which UNOS keeps 5: the device " \
               f"has {found}" in output
        assert len(nas.restored) == 2 and "done  clean up: remove the copies of the database that the check's " \
                                          "restores made" in output
    else:
        assert status == 1
        assert re.search(r"^FAIL  4\. the check's 2 restores push out none of .*: the device has 4, "
                         r"unos.db.pre-restore-20261101_090000_000000, .*the check's restores would delete the oldest",
                         output, re.M), output
        assert not [c for c in nas.calls if c[0] != "GET"]
    assert nas.snapshot() == before


def test_a_restore_that_drops_what_is_not_the_check_s_fails_and_its_copy_stays(nas):
    """The first restore, with nothing changed since the backup, must keep all that the check
    compares: when it drops what is not the check's (here the owner's API token), the copy of the
    database from before it holds what was lost, so the clean-up keeps it and leaves it to the
    owner. A change of the check's own users or of smb.conf alone loses nothing of anyone's."""
    nas.bugs.add("restore-drops-tokens")
    status, output = run_check("restore", nas)
    assert status == 1
    failed = [line for line in step_lines(output) if line.startswith("FAIL  ")]
    assert "byte for byte" in failed[0] and "the API tokens: gone 1 home assistant" in failed[0], output
    assert len(nas.restored) == 1 and len(nas.copies) == 1
    assert re.search(rf"^FAIL  clean up: remove the copies .*: {nas.copies[0]} stay, as they hold the database from "
                     r"before a restore and the first restore changed the API tokens that are not the check's.*: "
                     r"removing them is the owner's decision", output, re.M), output


def test_a_second_restore_that_takes_what_is_not_the_check_s_fails_and_its_copies_stay(nas, monkeypatch):
    """What someone else makes after the check's backup, such as an API token, goes with the second
    restore: the step says so, and the copies, which hold it, stay for the owner."""
    real = nas.api_auth

    def owner_adds_a_token(method, parts, body, params, upload):
        answer = real(method, parts, body, params, upload)
        if method == "PUT" and len(nas.api_tokens) == 1:  # while the check gives its user a new password
            nas.api_tokens.append({**nas.api_tokens[0], "id": 2, "name": "the owner's new token",
                                   "token_prefix": "unos_cd34"})
        return answer

    monkeypatch.setattr(nas, "api_auth", owner_adds_a_token)
    status, output = run_check("restore", nas)
    assert status == 1
    failed = [line for line in step_lines(output) if line.startswith("FAIL  ")]
    assert "while what is not the check's stays: what is not the check's changed: the API tokens: gone 2 the " \
           "owner's new token" in failed[0], output
    assert len(nas.copies) == 2
    assert re.search(r"^FAIL  clean up: remove the copies .* and the second restore changed the API tokens that are "
                     r"not the check's: removing them is the owner's decision", output, re.M), output


def lose_a_restore_answer(nas: FakeNas, monkeypatch, which: int) -> list:
    """The restore number WHICH (0-based) loses its answer and runs on, as under asyncio.shield: its
    database is swapped at once, and its reconcile runs at the next call that waits for smb_lock
    (a change of a share, a user's Delete). A restart of the supervisor meanwhile fails the test.
    Returns the reconciles still pending."""
    pending: list[Callable[[], None]] = []
    seen: list[str] = []
    real_shares, real_auth, real_restart = nas.api_shares, nas.api_auth, nas.restart_supervisor
    real_restore = nas.restore

    def restore(name):
        seen.append(name)
        if len(seen) - 1 != which:
            return real_restore(name)
        _, reconcile = nas.restore_database(name)
        pending.append(reconcile)
        raise Refused(502, "the relay lost the answer")

    def run_pending() -> None:
        while pending:
            pending.pop(0)()

    def shares(method, parts, body, params, upload):
        if method != "GET":
            run_pending()
        return real_shares(method, parts, body, params, upload)

    def auth(method, parts, body, params, upload):
        if method == "DELETE":
            run_pending()
        return real_auth(method, parts, body, params, upload)

    def restart():
        assert not pending, "the supervisor restarted while a restore held smb_lock"
        return real_restart()

    monkeypatch.setattr(nas, "restore", restore)
    monkeypatch.setattr(nas, "api_shares", shares)
    monkeypatch.setattr(nas, "api_auth", auth)
    monkeypatch.setattr(nas, "restart_supervisor", restart)
    return pending


@pytest.mark.parametrize("which", [0, 1])
def test_the_clean_up_waits_for_a_restore_whose_answer_was_lost(nas, monkeypatch, which):
    """A restore whose answer was lost runs on, holding smb_lock to the end of its reconcile
    (asyncio.shield): the clean-up waits for it before it lists the copies of the database or looks
    for the dropped user's accounts, so it removes what that restore made, and restarts nothing in
    the middle of its reconcile."""
    before = nas.snapshot()
    pending = lose_a_restore_answer(nas, monkeypatch, which)
    status, output = run_check("restore", nas)
    assert status == 1 and len(nas.restored) == which + 1 and not pending, output
    assert "done  clean up: remove the copies of the database that the check's restores made" in output
    assert nas.restarts == 0 and nas.snapshot() == before, output


def test_a_lost_restore_that_drops_what_is_not_the_check_s_leaves_its_copy(nas, monkeypatch):
    """The first restore loses its answer and drops the owner's API token: with no comparison
    around it, the clean-up still finds the token missing against the baseline and keeps the copy."""
    nas.bugs.add("restore-drops-tokens")
    lose_a_restore_answer(nas, monkeypatch, 0)
    status, output = run_check("restore", nas)
    assert status == 1 and len(nas.copies) == 1
    assert re.search(r"^FAIL  clean up: remove the copies .*: \S+ stay, as they hold the database from before a "
                     r"restore and the API tokens that are not the check's differ from how they were at the baseline "
                     r"or before a restore: removing them is the owner's decision", output, re.M), output


def test_a_lost_second_restore_that_takes_what_someone_made_after_the_backup_leaves_its_copies(nas, monkeypatch):
    """The owner makes an API token after the check's backup, and the second restore, whose answer
    is lost, drops it: the baseline never had it, so the clean-up compares with how things were
    before that restore, and keeps both copies, the second of which holds the token."""
    lose_a_restore_answer(nas, monkeypatch, 1)
    real = nas.api_auth

    def owner_adds_a_token(method, parts, body, params, upload):
        answer = real(method, parts, body, params, upload)
        if method == "PUT" and len(nas.api_tokens) == 1:  # while the check gives its user a new password
            nas.api_tokens.append({**nas.api_tokens[0], "id": 2, "name": "the owner's new token",
                                   "token_prefix": "unos_cd34"})
        return answer

    monkeypatch.setattr(nas, "api_auth", owner_adds_a_token)
    status, output = run_check("restore", nas)
    assert status == 1 and len(nas.copies) == 2
    assert re.search(r"^FAIL  clean up: remove the copies .* and the API tokens that are not the check's differ from "
                     r"how they were at the baseline or before a restore: removing them", output, re.M), output


def test_the_restore_check_leaves_out_the_refused_sign_ins_where_the_device_has_a_guest_share(nas):
    """With a guest share Samba takes a user without a Samba account as a guest (map to guest = Bad
    User, ADR-0339), so the sign-ins of the user whose SMB a restore turned off cannot be refused:
    the check says so in place of that step, and passes."""
    nas.shares["DATA"]["guest_ok"] = True
    nas.apply()
    status, output = run_check("restore", nas)
    assert status == 0, output
    assert re.search(r"^SKIP  \d+\. the password of unoscheck1a2b3cc at the backup and its newer one are both refused "
                     r"with NT_STATUS_LOGON_FAILURE: the device has guest shares \(DATA\)", output, re.M), output


def test_the_restore_check_expects_unos_s_texts():
    """The texts the check reads: a restore's warning for an account that needs its password again
    (src/shares/accounts.py) and the output of a scheduled backup's run (src/scheduler/router.py)."""
    accounts_source = (REPO_ROOT / "supervisor" / "src" / "shares" / "accounts.py").read_text()
    assert 'say(f"SMB must be turned on again for {_shown(username)}")' in accounts_source
    assert scenario.TURN_ON_AGAIN == "SMB must be turned on again for "
    scheduler_source = (REPO_ROOT / "supervisor" / "src" / "scheduler" / "router.py").read_text()
    assert 'return True, f"Backup {backup_name} created.{cleanup_msg}"' in scheduler_source
    assert 'publish_backup_file(tmp, "auto_backup", ENCRYPTED_EXTENSION)' in scheduler_source
    assert scenario.SCHEDULED_BACKUP.match("Backup auto_backup_20261111_030000_1.tar.gz.enc created.").group(1) == (
        "auto_backup_20261111_030000_1.tar.gz.enc")
    # The copies of the database that a restore keeps, which the check's clean-up removes.
    from src.backup.router import PRE_RESTORE_COPIES

    assert scenario.PRE_RESTORE_COPIES == PRE_RESTORE_COPIES
    source = (REPO_ROOT / "supervisor" / "src" / "backup" / "router.py").read_text()
    assert 'copy_path = db_path.with_name(f"{db_path.name}.pre-restore-{timestamp}")' in source
    assert 'timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")' in source


@pytest.fixture
def backups_folder(tmp_path):
    """A plain backup and an encrypted one as src/backup/router.py writes them (the Scheduler's key),
    and a fake docker whose exec runs the command with this Python, as the container's would."""
    from src.backup.router import _write_archive, encrypt_with_key, new_backup_key

    folder = tmp_path / "backups"
    content = tmp_path / "content"
    folder.mkdir()
    content.mkdir()
    (content / "manifest.json").write_text("{}")
    (content / "unos.db").write_bytes(b"SQLite format 3\x00")
    archive = io.BytesIO()
    _write_archive(content, archive)
    (folder / "unos_backup_20261111_120000.tar.gz").write_bytes(archive.getvalue())
    key = new_backup_key("the passphrase of the task")
    (folder / "auto_backup_20261111_030000.tar.gz.enc").write_bytes(encrypt_with_key(archive.getvalue(), key))
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "docker").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{tmp_path}/docker.log"\n'
        '[ "$1 $2 $3 $4" = "exec -i unos-supervisor python" ] || exit 99\n'
        f'shift 4\nexec "{sys.executable}" "$@"\n')
    (stubs / "docker").chmod(0o755)
    return folder, {"PATH": f"{stubs}:/usr/bin:/bin", "UNOS_DEV_BACKUPS": str(folder)}


@pytest.mark.parametrize("name, passphrase, code, members", [
    ("unos_backup_20261111_120000.tar.gz", "", 0, ["manifest.json", "unos.db"]),
    ("auto_backup_20261111_030000.tar.gz.enc", "the passphrase of the task", 0, ["manifest.json", "unos.db"]),
    ("auto_backup_20261111_030000.tar.gz.enc", "a wrong passphrase", 1, []),
    ("../unos.db", "", 2, []),
    ("unos_backup_20261111_120000.tar.gz --rm", "", 2, []),
])
def test_the_archive_helper_lists_the_members_of_a_backup(backups_folder, name, passphrase, code, members):
    """REMOTE_ARCHIVE runs python in the supervisor's container with the passphrase on standard
    input, opens an encrypted backup as the backup router writes it, and takes only a backup's name."""
    folder, env = backups_folder
    payload = scenario.Device.payload(scenario.REMOTE_ARCHIVE, read_only=False) + passphrase + "\n"
    result = subprocess.run(["bash", "-s", "--", name], input=payload, env=env, capture_output=True, text=True)
    assert result.returncode == code, result.stderr
    assert [line.split(" ", 1)[1] for line in result.stdout.splitlines() if line.startswith("member ")] == members
    log = folder.parent / "docker.log"
    assert log.exists() == (code != 2)
    assert not passphrase or passphrase not in log.read_text()


def test_the_copies_helper_removes_only_copies_of_the_database(tmp_path):
    """REMOTE_DB_COPIES, which removes without the read-only namespace: only names of copies that a
    restore keeps, unos.db.pre-restore-<time>; a symbolic link is no copy."""
    data = tmp_path / "data"
    data.mkdir()
    names = ["unos.db.pre-restore-20261111_093655_615365", "unos.db.pre-restore-20261111_151329_766127"]
    for name in names + ["unos.db"]:
        (data / name).write_bytes(b"SQLite format 3\x00")
    (data / "unos.db.pre-restore-20261111_000000_000000").symlink_to(data / "unos.db")
    env = {"PATH": "/usr/bin:/bin", "UNOS_DEV_DATA": str(data)}

    def run(*args):
        result = subprocess.run(["bash", "-s", "--", *args], input=scenario.REMOTE_DB_COPIES, env=env,
                                capture_output=True, text=True)
        return result.returncode, [line[5:] for line in result.stdout.splitlines() if line.startswith("copy ")]

    assert run("list") == (0, names)
    for bad in ("unos.db", "../unos.db.pre-restore-20261111_093655_615365", "unos.db.pre-restore-*"):
        assert run("remove", names[1], bad)[0] == 2, bad
    assert run("list") == (0, names)
    assert run("remove", names[1]) == (0, names[:1])
    assert sorted(p.name for p in data.iterdir()) == sorted(["unos.db", names[0], "unos.db.pre-restore-20261111_000000_000000"])


def test_the_device_removes_copies_without_the_read_only_namespace_and_lists_them_with_it(stub):
    device = device_with_stub(stub)
    (stub.log / "reply").write_text("copy unos.db.pre-restore-20261111_093655_615365\n")
    assert device.db_copies("remove", ("unos.db.pre-restore-20261111_151329_766127",)) == [
        "unos.db.pre-restore-20261111_093655_615365"]
    argv = (stub.log / "argv").read_text()
    assert "unshare" not in argv and "remove unos.db.pre-restore-20261111_151329_766127" in argv
    for name in ("argv", "stdin"):
        (stub.log / name).unlink()
    assert device.db_copies() == ["unos.db.pre-restore-20261111_093655_615365"]
    assert "unshare --mount --propagation private bash -s -- list" in (stub.log / "argv").read_text()


def test_the_device_reads_a_backup_s_members_in_the_read_only_namespace_with_the_passphrase_on_standard_input(stub):
    device = device_with_stub(stub)
    (stub.log / "reply").write_text("member manifest.json\nmember unos.db\n")
    passphrase = "pp-" + os.urandom(8).hex()
    assert device.archive_members("auto_backup_20261111_030000.tar.gz.enc", passphrase) == ["manifest.json", "unos.db"]
    argv = (stub.log / "argv").read_text()
    assert passphrase not in argv and passphrase not in (stub.log / "env").read_text()
    assert "unshare --mount --propagation private bash -s -- auto_backup_20261111_030000.tar.gz.enc" in argv
    sent = (stub.log / "stdin").read_text()
    assert sent.startswith("{\n" + scenario.RO_ROOT + "\n") and sent.endswith("\n}\n" + passphrase + "\n")


def test_the_backups_check_refuses_restores_and_never_restores(nas):
    status, output = run_check("backups", nas)
    assert status == 0, output
    restores = [(path, body) for method, path, body, _ in nas.calls if path.startswith("/backup/restore/")]
    assert len(restores) == 3
    assert restores[0][0] == "/backup/restore/unoscheck1a2b3c.tar.gz"
    assert restores[1][1] == {} and set(restores[2][1]) == {"passphrase"}
    assert nas.restored == []


def test_no_temporary_secret_reaches_the_output(nas):
    seen = []
    real = nas.request

    def request(method, path, json_body=None, params=None, auth=True, upload=None):
        if json_body and isinstance(json_body, dict):
            seen.extend(v for k, v in json_body.items() if k == "password" or k == "passphrase")
        return real(method, path, json_body=json_body, params=params, auth=auth, upload=upload)

    nas.request = request
    out = []
    for area in ("smb", "backups", "restore"):
        status, output = run_check(area, nas)
        assert status == 0, output
        out.append(output)
    assert len(set(seen)) >= 4 and all(len(s) >= 16 for s in seen)
    assert not any(s in text for s in seen for text in out)


def test_a_password_with_a_tab_is_redacted_also_as_tail_joins_its_whitespace(nas):
    """A refusal's text goes through tail(), which turns a tab into a space."""
    out = io.StringIO()
    check = scenario.Check("shares", nas, nas, out=out, run_id="unoscheck1a2b3c")
    password = check.secret("first-half\tsecond-half")
    nas.secrets = ["dev-env\tsecret"]
    check.say("FAIL  1. it answered HTTP 422: " + scenario.tail(f"refused {password} and dev-env\tsecret"))
    assert "first-half" not in out.getvalue() and "second-half" not in out.getvalue(), out.getvalue()
    assert "dev-env" not in out.getvalue(), out.getvalue()


def test_the_output_shows_the_api_s_text_of_a_refusal_redacted(nas):
    nas.secrets = ["s3cret-of-dev-env"]
    real = nas.api_storage

    def refuse(method, parts, body, params, upload):
        if method == "POST":
            raise Refused(507, "No space left; the login s3cret-of-dev-env failed")
        return real(method, parts, body, params, upload)

    nas.api_storage = refuse
    status, output = run_check("volumes", nas)
    assert status == 1
    assert "answered HTTP 507: No space left; the login <redacted> failed" in output
    assert "s3cret-of-dev-env" not in output


# --- the contract with the API --------------------------------------------------------------


def test_every_call_of_the_checks_is_a_route_of_the_supervisor_with_a_body_it_accepts():
    """The fake answers what the real API answers only if the checks ask what the real API
    serves: every call of a passing run of each area matches a route of src.main.app with its
    method, sends only query parameters that the route reads, and a body that the route's model
    validates, but for the calls that the fake refuses with 422, whose bodies the model must
    refuse (the shares check's password of 73 bytes)."""
    from pydantic import TypeAdapter
    from starlette.routing import compile_path

    from .test_authz import _http_operations

    operations = [(method, compile_path(path)[0], dependant) for method, path, dependant in _http_operations()]
    calls, invalid = [], set()
    for area in scenario.AREAS:
        nas = FakeNas()
        assert run_check(area, nas)[0] == 0
        invalid |= {len(calls) + index for index in nas.invalid}
        calls += nas.calls
    assert invalid
    problems = []
    for index, (method, path, body, params) in enumerate(calls):
        matched = [dependant for m, regex, dependant in operations if m == method and regex.match("/api/v1" + path)]
        if not matched:
            problems.append(f"{method} {path}: no route")
            continue
        dependant = matched[0]
        names = {p.alias for p in dependant.query_params}
        if set(params) - names:
            problems.append(f"{method} {path}: unknown query parameters {sorted(set(params) - names)}")
        bodies = dependant.body_params
        if path == "/files/upload":
            continue  # multipart: the relay's upload, checked in test_dev_scripts.py
        if body is None:
            if any(p.field_info.is_required() for p in bodies):
                problems.append(f"{method} {path}: the route needs a body")
            continue
        if len(bodies) != 1:
            problems.append(f"{method} {path}: the route takes {len(bodies)} body parameters")
            continue
        try:
            TypeAdapter(bodies[0].field_info.annotation).validate_python(body)
        except Exception as e:  # pydantic's ValidationError
            if index not in invalid:
                problems.append(f"{method} {path}: the body {body!r} is refused: {e}")
        else:
            if index in invalid:
                problems.append(f"{method} {path}: the fake refuses the body {body!r}, which the route accepts")
    assert problems == []


# --- scenario.Device: what goes to the device ------------------------------------------------


def test_the_read_only_namespace_is_env_sh_s():
    text = (DEV / "env.sh").read_text()
    assert f"DEV_RO_ROOT='{scenario.RO_ROOT}'" in text


@pytest.fixture
def stub(tmp_path):
    return StubDevice(tmp_path)


def device_with_stub(stub: StubDevice) -> object:
    """scenario.Device with ssh.sh run in the stub's environment."""
    runner = stub.bin / "ssh-here"
    runner.write_text(f"#!/usr/bin/env bash\nexport {' '.join(f'{k}={v!r}' for k, v in stub.env().items())}\n"
                      f'exec "{DEV}/ssh.sh" "$@"\n')
    runner.chmod(0o755)
    sd = stub.bin / "sd-here"
    sd.write_text("#!/usr/bin/env bash\necho 4096\n")
    sd.chmod(0o755)
    return scenario.Device(ssh=runner, sd=sd)


def test_the_device_gets_the_script_and_the_secrets_on_standard_input_only(stub):
    device = device_with_stub(stub)
    (stub.log / "reply").write_text("end 1 " + scenario.b64text("session setup failed: NT_STATUS_LOGON_FAILURE") + "\n")
    password = "pw-" + os.urandom(8).hex()
    listing = device.smb_list("unoscheck1a2b3c", password)
    assert listing.rc == 1 and "NT_STATUS_LOGON_FAILURE" in listing.output
    argv = (stub.log / "argv").read_text()
    env = (stub.log / "env").read_text()
    sent = (stub.log / "stdin").read_text()
    assert password not in argv and password not in env
    assert sent.startswith("{\n" + scenario.RO_ROOT + "\n") and sent.endswith("\n}\n" + password + "\n")
    assert "unshare --mount --propagation private bash -s -- list unoscheck1a2b3c ''" in argv
    assert device.sectors() == 4096


def test_a_samr_password_change_sends_both_passwords_on_standard_input_only(stub):
    """ADR-0342: the old and the new password of smbpasswd -r reach the device after the script,
    on standard input, in the read-only namespace."""
    device = device_with_stub(stub)
    (stub.log / "reply").write_text("end 1 " + scenario.b64text(SAMR_REFUSAL) + "\n")
    old, new = "pw-" + os.urandom(8).hex(), "pw-" + os.urandom(8).hex()
    change = device.smb_change_password("unoscheck1a2b3c", old, new)
    assert change.rc == 1 and change.status == "NT_STATUS_PASSWORD_RESTRICTION"
    argv = (stub.log / "argv").read_text()
    env = (stub.log / "env").read_text()
    sent = (stub.log / "stdin").read_text()
    for password in (old, new):
        assert password not in argv and password not in env
    assert sent.startswith("{\n" + scenario.RO_ROOT + "\n") and sent.endswith(f"\n}}\n{old}\n{new}\n")
    assert "unshare --mount --propagation private bash -s -- change unoscheck1a2b3c ''" in argv


def test_the_device_reads_pdbedit_without_the_read_only_namespace(stub):
    """tdbsam opens passdb.tdb read-write, which a read-only root refuses (ADR-0304)."""
    device = device_with_stub(stub)
    (stub.log / "reply").write_text("user test\nuser unoscheck1a2b3c\n"
                                    'history account policy "password history" value is: 3\n'
                                    'duration account policy "lockout duration" value is: 5\n'
                                    "reset No account policy by that name!\n"
                                    'lockout account policy "bad lockout attempt" value is: 5\n')
    names, policies = device.passdb_entries()
    assert names == ["test", "unoscheck1a2b3c"]
    assert policies == {"history": "3", "duration": "5", "reset": "No account policy by that name!", "lockout": "5"}
    argv = (stub.log / "argv").read_text()
    assert "unshare" not in argv and argv.rstrip("\n").endswith("\0bash -s --")
    assert scenario.RO_ROOT not in (stub.log / "stdin").read_text()


def test_the_device_s_facts_are_parsed(stub):
    device = device_with_stub(stub)
    (stub.log / "reply").write_text(textwrap.dedent("""\
        now 1800000000
        tool smbclient
        tool testparm
        sha256 /etc/passwd abc
        sha256 /etc/samba/smb.conf def
        account u:x:70003:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin
        group unos-smb:x:70000:
        testparm ok
        passdb 2026-11-08 12:00:00.123456789 +0000
        size /run/samba/log.samba 800
        size /var/log/samba/log.nmbd missing
        """))
    facts = device.facts()
    assert facts["now"] == 1800000000 and facts["tools"] == ["smbclient", "testparm"]
    assert facts["sha256"] == {"/etc/passwd": "abc", "/etc/samba/smb.conf": "def"}
    assert facts["accounts"] == ["u:x:70003:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin"]
    assert facts["group"] == "unos-smb:x:70000:" and facts["testparm"] is True
    assert facts["passdb"] == "2026-11-08 12:00:00.123456789 +0000"
    assert facts["sizes"] == {"/run/samba/log.samba": "800", "/var/log/samba/log.nmbd": "missing"}


@pytest.mark.parametrize("bash", bashes())
def test_the_scripts_for_the_device_parse(bash):
    names = [name for name in dir(scenario) if name.startswith("REMOTE_")]
    assert len(names) >= 7
    for name in names:
        result = subprocess.run([bash, "-n"], input="{\n" + scenario.RO_ROOT + "\n" + getattr(scenario, name) + "\n}\n",
                                capture_output=True, text=True)
        assert result.returncode == 0, (name, result.stderr)


def test_the_device_edits_smb_conf_without_the_read_only_namespace_and_the_rest_with_it(stub):
    """The hand edit writes /etc/samba, which the read-only root refuses (ADR-0309); the link of a
    share folder and the shares check's entries (on the pool), getfacl and the restart (through
    Docker's socket) run in the namespace."""
    device = device_with_stub(stub)
    (stub.log / "reply").write_text("conf abc\ncopy smb.conf.unos-replaced-20261101_120000_000000 def\n")
    assert device.hand_edit("restore", "unoscheck1a2b3chand") == (
        "abc", {"smb.conf.unos-replaced-20261101_120000_000000": "def"})
    argv = (stub.log / "argv").read_text()
    assert "unshare" not in argv and "restore unoscheck1a2b3chand ''" in argv
    assert scenario.RO_ROOT not in (stub.log / "stdin").read_text()
    for call, reply, want in ((lambda: device.unserve("link", f"{ROOT}/unoscheck1a2b3c"), "link\n", "link"),
                              (device.restart_supervisor, "healthy 8\n", 8),
                              (lambda: device.entries("sticky", f"{ROOT}/unoscheck1a2b3c-sticky"),
                               "account nobody:nogroup 65534\n", {"user": "nobody", "group": "nogroup", "uid": "65534"}),
                              (lambda: device.acl(f"{ROOT}/unoscheck1a2b3c/acl.txt"), "acl user::rw-\nacl mask::rw-\n",
                               ["user::rw-", "mask::rw-"]),
                              (device.samba_files, "file smb.conf abc 17 644 root:root\n",
                               {"smb.conf": {"sha256": "abc", "mtime": "17", "mode": "644 root:root"}})):
        for name in ("argv", "stdin"):  # the stub appends each call's
            (stub.log / name).unlink()
        (stub.log / "reply").write_text(reply)
        assert call() == want
        assert "unshare --mount --propagation private" in (stub.log / "argv").read_text()
        assert (stub.log / "stdin").read_text().startswith("{\n" + scenario.RO_ROOT + "\n")


@pytest.mark.parametrize("reply", ["unhealthy 9\n", "healthy\n", ""])
def test_a_restart_that_does_not_end_healthy_fails(stub, reply):
    device = device_with_stub(stub)
    (stub.log / "reply").write_text(reply)
    with pytest.raises(scenario.Failed):
        device.restart_supervisor()


def run_helper(script: str, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ["PATH"] + ":/usr/sbin:/sbin", "UNOS_DEV_SAMBA": str(tmp_path / "samba"), "UNOS_DEV_SHM": str(tmp_path / "shm"),
           "UNOS_DEV_POOLS": str(tmp_path / "pools")}
    return subprocess.run(["bash", "-s", "--", *args], input=script, env=env, capture_output=True, text=True)


def test_the_hand_edit_helper_edits_smb_conf_and_puts_it_back(tmp_path):
    """REMOTE_HAND_EDIT, run here: the edit replaces UNOS's header and appends the check's section,
    with the original kept in RAM; the restore puts it back byte for byte when smb.conf is not
    UNOS's, deletes the RAM copy, and deletes the copies smb.conf.unos-replaced-<time> that hold
    exactly the check's edited file, and no other file."""
    samba, shm = tmp_path / "samba", tmp_path / "shm"
    samba.mkdir()
    shm.mkdir()
    conf = samba / "smb.conf"
    original = HEADER + "# when shares change.\n\n[global]\n   workgroup = WORKGROUP\n\n[DATA]\n   path = /x\n"
    conf.write_text(original)
    earlier = samba / "smb.conf.unos-replaced-20261101_120000_000000"
    earlier.write_text("[old]\n")
    section = "unoscheck1a2b3chand"
    result = run_helper(scenario.REMOTE_HAND_EDIT, tmp_path, "edit", section, digest(original))
    assert result.returncode == 0, result.stderr
    edited = conf.read_text()
    assert edited == ("# Edited by hand for a scenario check of UNOS\n\n[global]\n   workgroup = WORKGROUP\n\n[DATA]\n"
                      f"   path = /x\n\n[{section}]\n   path = /srv/{section}\n   available = no\n")
    assert result.stdout == f"conf {digest(edited)}\ncopy {earlier.name} {digest('[old]' + chr(10))}\n"
    assert [p.name for p in shm.iterdir()] == [f"unos-scenario-{section}.conf"]
    # An apply copied it: the restore deletes the copy with exactly the edited bytes, and keeps
    # those that hold anything else and files of other names.
    check_copy = samba / "smb.conf.unos-replaced-20261112_120000_000000"
    check_copy.write_text(edited)
    (samba / "smb.conf.unos-replaced-notes").write_text(edited)
    (samba / "smb.conf.unos-import-backup-20261112_120000_000000").write_text(edited)
    result = run_helper(scenario.REMOTE_HAND_EDIT, tmp_path, "restore", section, digest(edited))
    assert result.returncode == 0, result.stderr
    assert conf.read_text() == original and list(shm.iterdir()) == []
    assert not check_copy.exists() and earlier.read_text() == "[old]\n"
    assert sorted(p.name for p in samba.iterdir()) == [
        "smb.conf", "smb.conf.unos-import-backup-20261112_120000_000000", earlier.name, "smb.conf.unos-replaced-notes"]
    assert result.stdout == f"conf {digest(original)}\ncopy {earlier.name} {digest('[old]' + chr(10))}\n"
    check_copy.write_text(edited + "\n[owner]\n   path = /srv/owner\n")
    assert run_helper(scenario.REMOTE_HAND_EDIT, tmp_path, "restore", section, digest(edited)).returncode == 0
    assert check_copy.exists() and conf.read_text() == original


def test_the_hand_edit_helper_keeps_unos_s_newer_smb_conf(tmp_path):
    """Once an apply has written UNOS's rendering over the edited file, the restore keeps it and
    only deletes the kept original from RAM."""
    samba, shm = tmp_path / "samba", tmp_path / "shm"
    samba.mkdir()
    shm.mkdir()
    conf = samba / "smb.conf"
    original = HEADER + "\n[global]\n"
    conf.write_text(original)
    section = "unoscheck1a2b3chand"
    assert run_helper(scenario.REMOTE_HAND_EDIT, tmp_path, "edit", section, digest(original)).returncode == 0
    newer = HEADER + "\n[global]\n\n[unoscheck1a2b3c]\n   path = /x\n"
    conf.write_text(newer)
    result = run_helper(scenario.REMOTE_HAND_EDIT, tmp_path, "restore", section)
    assert result.returncode == 0, result.stderr
    assert conf.read_text() == newer and list(shm.iterdir()) == []


@pytest.mark.parametrize("args, why", [
    (("edit", "unoscheck1a2b3chand", "0" * 64), "smb.conf changed since the check read it"),
    (("edit", "unoscheck1a2b3c", "@"), "not a section of a scenario check"),
    (("edit", "DATA", "@"), "not a section of a scenario check"),
    (("restore", "unoscheckxyzxyzhand"), "not a section of a scenario check"),
    (("delete", "unoscheck1a2b3chand"), "not an action"),
])
def test_the_hand_edit_helper_refuses_what_is_not_its_own(tmp_path, args, why):
    (tmp_path / "samba").mkdir()
    (tmp_path / "shm").mkdir()
    conf = tmp_path / "samba" / "smb.conf"
    conf.write_text(HEADER + "\n[global]\n")
    args = tuple(digest(conf.read_text()) if a == "@" else a for a in args)
    result = run_helper(scenario.REMOTE_HAND_EDIT, tmp_path, *args)
    assert result.returncode in (1, 2) and why in result.stderr
    assert conf.read_text() == HEADER + "\n[global]\n" and list((tmp_path / "shm").iterdir()) == []


def test_the_hand_edit_helper_refuses_beside_ten_copies(tmp_path):
    """With 10 copies, the check's apply would make UNOS delete the oldest (ADR-0360); nine are fine."""
    (tmp_path / "samba").mkdir()
    (tmp_path / "shm").mkdir()
    conf = tmp_path / "samba" / "smb.conf"
    conf.write_text(HEADER + "\n[global]\n")
    for day in range(1, 10):
        (tmp_path / "samba" / f"smb.conf.unos-replaced-202611{day:02d}_120000_000000").write_text("[old]\n")
    (tmp_path / "samba" / "smb.conf.unos-replaced").write_text("[old]\n")  # not a copy of UNOS's
    section = "unoscheck1a2b3chand"
    result = run_helper(scenario.REMOTE_HAND_EDIT, tmp_path, "edit", section, digest(conf.read_text()))
    assert result.returncode == 0, result.stderr
    assert run_helper(scenario.REMOTE_HAND_EDIT, tmp_path, "restore", section).returncode == 0
    assert conf.read_text() == HEADER + "\n[global]\n"
    (tmp_path / "samba" / "smb.conf.unos-replaced-20261110_120000_000000").write_text("[old]\n")
    result = run_helper(scenario.REMOTE_HAND_EDIT, tmp_path, "edit", section, digest(conf.read_text()))
    assert result.returncode == 1 and "keeps 10 copies smb.conf.unos-replaced-<time>: an apply would delete the oldest" in result.stderr
    assert conf.read_text() == HEADER + "\n[global]\n" and list((tmp_path / "shm").iterdir()) == []


def test_the_unserve_helper_links_a_check_s_share_folder_and_puts_it_back(tmp_path):
    folder = tmp_path / "pools" / "data" / "unoscheck1a2b3c"
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("a")
    result = run_helper(scenario.REMOTE_UNSERVE, tmp_path, "link", str(folder))
    assert result.returncode == 0 and result.stdout == "link\n", result.stderr
    assert os.readlink(folder) == "unoscheck1a2b3c-moved" and (folder.parent / "unoscheck1a2b3c-moved" / "a.txt").exists()
    assert run_helper(scenario.REMOTE_UNSERVE, tmp_path, "link", str(folder)).returncode == 1
    result = run_helper(scenario.REMOTE_UNSERVE, tmp_path, "restore", str(folder))
    assert result.returncode == 0 and result.stdout == "folder\n", result.stderr
    assert not folder.is_symlink() and (folder / "a.txt").exists() and not (folder.parent / "unoscheck1a2b3c-moved").exists()
    assert run_helper(scenario.REMOTE_UNSERVE, tmp_path, "restore", str(folder)).stdout == "folder\n"


@pytest.mark.parametrize("path", ["{pools}/data/media", "{pools}/data/unoscheck1a2b3c/inner", "{pools}/unoscheck1a2b3c",
                                  "{pools}/a/b/unoscheck1a2b3c", "/etc/unoscheck1a2b3c", "{pools}/data/../unoscheck1a2b3c",
                                  "{pools}/data/unoscheck1a2b3c-moved"])
def test_the_unserve_helper_touches_only_a_check_s_share_folder(tmp_path, path):
    path = path.format(pools=tmp_path / "pools")
    (tmp_path / "pools" / "data" / "media").mkdir(parents=True)
    result = run_helper(scenario.REMOTE_UNSERVE, tmp_path, "link", path)
    assert result.returncode == 2 and "not a share folder of a scenario check" in result.stderr
    assert sorted(p.name for p in (tmp_path / "pools" / "data").iterdir()) == ["media"]


def run_entries(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """REMOTE_ENTRIES, run here: chown and setfacl are stubs that log their arguments (the tests do
    not run as root), and the other account is the user who runs them."""
    stubs = tmp_path / "stubs"
    if not stubs.exists():
        stubs.mkdir()
        for tool in ("chown", "setfacl"):
            (stubs / tool).write_text(f'#!/usr/bin/env bash\necho "{tool} $*" >> "{tmp_path}/tools.log"\n')
            (stubs / tool).chmod(0o755)
        (stubs / "chmod").write_text(f'#!/usr/bin/env bash\necho "chmod $*" >> "{tmp_path}/tools.log"\n'
                                     'exec /bin/chmod "$@"\n')
        (stubs / "chmod").chmod(0o755)
        (stubs / "getfacl").write_text('#!/usr/bin/env bash\nprintf "user::rw-\\nuser:nobody:r--\\ngroup::r--\\n'
                                       'mask::r--\\nother::---\\n\\n"\n')
        (stubs / "getfacl").chmod(0o755)
    env = {"PATH": f"{stubs}:{os.environ['PATH']}", "UNOS_DEV_POOLS": str(tmp_path / "pools"),
           "UNOS_DEV_OTHER_ACCOUNT": "someone:somegroup"}
    stubs_id = stubs / "id"
    stubs_id.write_text('#!/usr/bin/env bash\n[ "$*" = "-u someone" ] && echo 4242\n')
    stubs_id.chmod(0o755)
    return subprocess.run(["bash", "-s", "--", *args], input=scenario.REMOTE_ENTRIES, env=env, capture_output=True,
                          text=True)


def test_the_entries_helper_makes_the_shares_check_s_entries_with_their_owners_and_modes(tmp_path):
    folder = tmp_path / "pools" / "data" / "unoscheck1a2b3c"
    folder.mkdir(parents=True)
    result = run_entries(tmp_path, "repair", str(folder), "acl")
    assert result.returncode == 0 and result.stdout == "account someone:somegroup 4242\n", result.stderr
    for name, (made, _) in scenario.REPAIR_ENTRIES.items():
        if name != "link.txt":
            assert f"{stat.S_IMODE(os.lstat(folder / name).st_mode):o}" == made.split()[0], name
    assert os.readlink(folder / "link.txt") == "../unoscheck1a2b3c-outside/target.txt"
    outside = folder.parent / "unoscheck1a2b3c-outside"
    assert (folder / "link.txt").read_text() == scenario.DATA and oct(stat.S_IMODE(outside.stat().st_mode)) == "0o755"
    assert oct(stat.S_IMODE((outside / "target.txt").stat().st_mode)) == "0o644"
    log = (tmp_path / "tools.log").read_text().splitlines()
    made = {name for name in scenario.REPAIR_ENTRIES if name != "legacy.txt"}
    assert any(line.startswith("chown -h root:root ") and set(line.split()[3:]) == made | {
        str(outside), f"{outside}/target.txt"} for line in log), log
    assert "chown someone:somegroup legacy.txt" in log and "setfacl -m u:someone:r acl.txt" in log
    # Nothing is made twice.
    again = run_entries(tmp_path, "repair", str(folder), "acl")
    assert again.returncode == 1 and "exists" in again.stderr
    # The ACL of an entry, as the check reads it.
    result = run_entries(tmp_path, "acl", str(folder / "acl.txt"))
    assert result.returncode == 0 and result.stdout == ("acl user::rw-\nacl user:nobody:r--\nacl group::r--\n"
                                                        "acl mask::r--\nacl other::---\n"), result.stderr


def test_the_entries_helper_makes_the_sticky_folders_without_an_acl_on_request(tmp_path):
    sticky = tmp_path / "pools" / "data" / "unoscheck1a2b3c-sticky"
    sticky.parent.mkdir(parents=True)
    result = run_entries(tmp_path, "sticky", str(sticky))
    assert result.returncode == 0 and result.stdout == "account someone:somegroup 4242\n", result.stderr
    assert oct(stat.S_IMODE(sticky.stat().st_mode)) == oct(stat.S_IMODE((sticky / "theirs").stat().st_mode)) == "0o1777"
    log = (tmp_path / "tools.log").read_text().splitlines()
    # theirs gets its mode while its parent is root's 755: once the parent is open, the owner of
    # theirs could replace it with a symbolic link, which chmod would follow.
    assert log == [f"chown root:root {sticky}", f"chown someone:somegroup {sticky}/theirs",
                   f"chmod 01777 {sticky}/theirs", f"chmod 01777 {sticky}"]
    folder = sticky.parent / "unoscheck1a2b3c"
    folder.mkdir()
    assert run_entries(tmp_path, "repair", str(folder), "noacl").returncode == 0
    assert not [line for line in (tmp_path / "tools.log").read_text().splitlines() if line.startswith("setfacl")]


@pytest.mark.parametrize("action, path", [
    ("repair", "{pools}/data/media"), ("repair", "{pools}/data/unoscheck1a2b3c/inner"), ("repair", "{pools}/unoscheck1a2b3c"),
    ("repair", "/etc/unoscheck1a2b3c"), ("repair", "{pools}/data/../unoscheck1a2b3c"), ("repair", "{pools}/data/unoscheck1a2b3c-sticky"),
    ("sticky", "{pools}/data/unoscheck1a2b3c"), ("sticky", "{pools}/data/media-sticky"),
    ("acl", "/etc/shadow"), ("acl", "{pools}/data/unoscheck1a2b3c/../../media"), ("acl", "{pools}/data/media/a.txt"),
    ("chmod", "{pools}/data/unoscheck1a2b3c"),
])
def test_the_entries_helper_touches_only_a_check_s_folders(tmp_path, action, path):
    (tmp_path / "pools" / "data" / "media").mkdir(parents=True)
    result = run_entries(tmp_path, action, path.format(pools=tmp_path / "pools"))
    assert result.returncode == 2 and ("not a folder of a scenario check" in result.stderr or "not an action" in result.stderr)
    assert sorted(p.name for p in (tmp_path / "pools" / "data").iterdir()) == ["media"]
    assert not (tmp_path / "tools.log").exists()


def test_the_smb_helper_makes_the_files_the_checks_compare():
    for text in (scenario.DATA, scenario.DATA2):
        assert f'printf "{text.rstrip(chr(10))}\\n"' in scenario.REMOTE_SMB


# The smbclient of the SMB helper's session test: it checks its arguments (the auth file, the
# client configuration in RAM, signing), keeps the auth file's mode and content where the test
# finds them, and answers ls, put and the ! commands, line by line on standard input, as smbclient
# does without a terminal; "put" of the name "denied" fails, "quit-now" ends it.
STUB_SMBCLIENT = textwrap.dedent(
    """\
    #!@PYTHON@
    import os, stat, subprocess, sys
    args = sys.argv[1:]
    conf = args[args.index("-s") + 1]
    text = open(conf).read()
    folder = os.path.dirname(os.path.abspath(conf))
    assert "cache directory = " + folder in text and "private dir = " + folder in text
    with open(os.environ["FAKE_SMB_LOG"], "a") as log:
        log.write("argv " + " ".join(args) + "\\n")
        log.write("signing " + ("required" if "client signing = required" in text else "not required") + "\\n")
        log.write("ipc signing " + ("required" if "client ipc signing = required" in text else "not required") + "\\n")
        if "-A" in args:
            auth = args[args.index("-A") + 1]
            log.write("auth %o %s\\n" % (stat.S_IMODE(os.stat(auth).st_mode), open(auth).read().replace("\\n", "|")))
        log.write("dir " + folder + "\\n")
    if "-L" in args:
        if "-A" not in args:
            print("session setup failed: NT_STATUS_ACCESS_DENIED")
            sys.exit(1)
        print("Disk|unoscheck1a2b3c|")
        print("IPC|IPC$|IPC Service (UNOS NAS)")
        sys.exit(0)
    for line in sys.stdin:
        line = line.rstrip("\\n")
        sys.stdout.write("smb: \\\\> ")
        if line.startswith("!"):
            sys.stdout.flush()
            subprocess.run(line[1:], shell=True)
        elif line == "ls":
            print("  .  D  0  Sun Nov  8 12:00:00 2026")
        elif line.startswith("put "):
            name = line.split()[2]
            print("NT_STATUS_ACCESS_DENIED opening remote file \\\\" + name if name == "denied" else "putting file " + name)
        elif line == "quit-now":
            sys.exit(0)
        sys.stdout.flush()
    """
)


@pytest.fixture
def local_stub(stub):
    stub.stub("unshare", STUB_UNSHARE)
    stub.stub("mount", STUB_MOUNT)
    if shutil.which("flock") is None:
        stub.stub("flock", STUB_FLOCK)
    stub.stub("smbclient", STUB_SMBCLIENT)
    stub.stub("stdbuf", '#!/usr/bin/env bash\nwhile [ "${1#-}" != "$1" ]; do shift; done\nexec "$@"\n')
    (stub.tmp / "shm").mkdir()
    return stub


def test_the_smb_helper_runs_a_session_step_by_step_and_removes_its_files(local_stub):
    """REMOTE_SMB's session mode, run here: the password goes into an auth file of mode 600 in a
    private folder in RAM, never onto a command line; each run of commands ends at a pause, whose
    answer carries their output; a failed command shows its NT_STATUS; once smbclient has ended,
    a run says so; at the end the folder is gone."""
    stub = local_stub
    extra = {"FAKE_SSH_MODE": "local", "UNOS_DEV_SHM": str(stub.tmp / "shm"), "FAKE_SMB_LOG": str(stub.tmp / "smb.log")}
    runner = stub.bin / "ssh-here"
    runner.write_text(f"#!/usr/bin/env bash\nexport {' '.join(f'{k}={v!r}' for k, v in stub.env(**extra).items())}\n"
                      f'exec "{DEV}/ssh.sh" "$@"\n')
    runner.chmod(0o755)
    device = scenario.Device(ssh=runner, sd=runner)
    password = "pw-" + os.urandom(8).hex()
    with device.smb_session("unoscheck1a2b3c", password, "unoscheck1a2b3c") as session:
        first = session.run("ls", "put data.txt a.txt")
        second = session.run("put data.txt denied")
        third = session.run("!cat data.txt")
        session.run("quit-now")
        fourth = session.run("ls")
    with device.smb_guest_session("unoscheck1a2b3c") as session:
        guest = session.run("ls")
    listing = device.smb_list("unoscheck1a2b3c", password)
    anonymous = device.smb_anonymous()
    assert listing.ok and listing.disks == ["unoscheck1a2b3c"]
    assert not anonymous.ok and anonymous.status == "NT_STATUS_ACCESS_DENIED" and not anonymous.disks
    assert first.ok and "putting file a.txt" in first.output and not first.ended, first.output
    assert not second.ok and second.status == "NT_STATUS_ACCESS_DENIED"
    assert third.ok and scenario.DATA.strip() in third.output
    assert not fourth.ok and fourth.ended
    assert guest.ok and ".  D  0" in guest.output, guest.output
    log = (stub.tmp / "smb.log").read_text()
    assert f"auth 600 username = unoscheck1a2b3c|password = {password}|" in log
    assert password not in "".join(re.findall(r"^argv .*$", log, re.M))
    runs = log.split("argv ")[1:]
    # The session and the share list sign; the null session (no user, no password) and the guest
    # session leave signing to the server, so that only the server can refuse them.
    session = [r for r in runs if "//127.0.0.1/unoscheck1a2b3c" in r]
    lists = [r for r in runs if "-g -L //127.0.0.1" in r]
    assert len(session) == 2 and "-A " in session[0] and "\nsigning required" in session[0]
    assert "-N -U% //127.0.0.1/unoscheck1a2b3c" in session[1] and "-A " not in session[1]
    assert "\nsigning not required" in session[1] and "auth " not in session[1]
    assert len(lists) == 2 and "-A " in lists[0] and "\nsigning required" in lists[0] and "ipc signing required" in lists[0]
    assert "-N -U% " in lists[1] and "-A " not in lists[1]
    assert "\nsigning not required" in lists[1] and "ipc signing not required" in lists[1]
    for folder in re.findall(r"^dir (\S+)$", log, re.M):
        assert folder.startswith(str(stub.tmp / "shm") + "/") and not os.path.exists(folder)
    assert list((stub.tmp / "shm").iterdir()) == []


# The smbpasswd of the SMB helper's change test: it checks the client configuration in RAM, logs
# its arguments, its standard input (the old password, then the new one twice), the files of its
# folder and whether IPC signing is required, and refuses the change as Samba 4.22 refuses it when
# its check password script fails, with SAMR_REFUSAL on its standard error (ADR-0342).
STUB_SMBPASSWD = textwrap.dedent(
    """\
    #!@PYTHON@
    import os, sys
    args = sys.argv[1:]
    conf = args[args.index("-c") + 1]
    text = open(conf).read()
    folder = os.path.dirname(os.path.abspath(conf))
    assert "private dir = " + folder in text and "state directory = " + folder in text
    lines = sys.stdin.read().split("\\n")
    with open(os.environ["FAKE_SMB_LOG"], "a") as log:
        log.write("argv " + " ".join(args) + "\\n")
        log.write("stdin " + "|".join(lines) + "\\n")
        log.write("files " + " ".join(sorted(os.listdir(folder))) + "\\n")
        log.write("ipc signing " + ("required" if "client ipc signing = required" in text else "not required") + "\\n")
        log.write("dir " + folder + "\\n")
    sys.stderr.write(@REFUSAL@)
    sys.exit(1)
    """
).replace("@REFUSAL@", repr(SAMR_REFUSAL))


def test_the_smb_helper_asks_for_a_samr_password_change_with_the_passwords_on_standard_input(local_stub):
    """REMOTE_SMB's change mode, run here (ADR-0342): smbpasswd -r 127.0.0.1 with the client
    configuration in RAM and IPC signing required, as the user, gets the old password and the new
    one twice on standard input, never on a command line or in a file; the answer carries its exit
    status and output, and the folder is gone at the end."""
    stub = local_stub
    stub.stub("smbpasswd", STUB_SMBPASSWD)
    extra = {"FAKE_SSH_MODE": "local", "UNOS_DEV_SHM": str(stub.tmp / "shm"), "FAKE_SMB_LOG": str(stub.tmp / "smb.log")}
    runner = stub.bin / "ssh-here"
    runner.write_text(f"#!/usr/bin/env bash\nexport {' '.join(f'{k}={v!r}' for k, v in stub.env(**extra).items())}\n"
                      f'exec "{DEV}/ssh.sh" "$@"\n')
    runner.chmod(0o755)
    device = scenario.Device(ssh=runner, sd=runner)
    old, new = "pw-" + os.urandom(8).hex(), "pw-" + os.urandom(8).hex()

    change = device.smb_change_password("unoscheck1a2b3c", old, new)

    assert not change.ok and change.rc == 1 and change.status == "NT_STATUS_PASSWORD_RESTRICTION", change.output
    log = (stub.tmp / "smb.log").read_text()
    folder = re.search(r"^dir (\S+)$", log, re.M).group(1)
    assert f"argv -c {folder}/smb.conf -r 127.0.0.1 -s -U unoscheck1a2b3c\n" in log
    assert f"stdin {old}|{new}|{new}|\n" in log
    assert "files smb.conf\n" in log and "ipc signing required\n" in log
    assert folder.startswith(str(stub.tmp / "shm") + "/") and not os.path.exists(folder)
    assert list((stub.tmp / "shm").iterdir()) == []


# --- scenario.sh -------------------------------------------------------------------------------


def test_scenario_sh_help_needs_no_dev_env(stub):
    stub.env_file.unlink()
    result = stub.run(DEV / "scenario.sh", "--help")
    assert result.returncode == 0 and "files" in result.stdout and "smb" in result.stdout


@pytest.mark.parametrize("argv", [[], ["bogus"], ["files", "bogus"], ["--pool"], ["--pool", "a b", "files"], ["files", "--help"]])
def test_scenario_sh_refuses_bad_arguments_without_contacting_anything(stub, argv):
    result = stub.run(DEV / "scenario.sh", *argv)
    assert result.returncode == 2, argv
    assert not any(stub.log.iterdir())


@pytest.fixture
def lock_stub(stub):
    stub.stub("unshare", STUB_UNSHARE)
    stub.stub("mount", STUB_MOUNT)
    if shutil.which("flock") is None:
        stub.stub("flock", STUB_FLOCK)
    if shutil.which("timeout") is None:
        stub.stub("timeout", "#!/usr/bin/env bash\nshift\nexec \"$@\"\n")
    stub.stub("sync", "#!/usr/bin/env bash\nexit 0\n")
    return stub


def test_scenario_sh_holds_the_device_lock_and_reports_the_sd_writes_of_each_check(lock_stub):
    stub = lock_stub
    # The Python part, recorded: it sees the lock held and gets the area and the pool.
    fake = stub.bin / "fake-python"
    fake.write_text('#!/usr/bin/env bash\necho "python $* held=${UNOS_DEV_LOCK_HELD:+yes}" >> "$FAKE_LOG"\n'
                    '[ "$2" != smb ] || exit 1\n')
    fake.chmod(0o755)
    result = stub.run(DEV / "scenario.sh", "--pool", "data", "all", FAKE_SSH_MODE="local", UNOS_DEV_PYTHON=str(fake),
                      FAKE_LOG=str(stub.tmp / "calls"))
    assert result.returncode == 1, result.stdout + result.stderr
    calls = (stub.tmp / "calls").read_text().splitlines()
    assert calls == [f"python {DEV}/scenario.py {area} --pool data held=yes" for area in scenario.AREAS]
    for area in scenario.AREAS:
        assert f"SD writes scenario {area}: " in result.stderr
    assert "scenario.sh: failed: smb" in result.stderr


def test_scenario_sh_reports_an_interrupted_check_and_stops_there(lock_stub):
    """A check that a signal ended (130) still gets its SD writes line, and the run ends with it."""
    stub = lock_stub
    fake = stub.bin / "fake-python"
    fake.write_text('#!/usr/bin/env bash\necho "$2" >> "$FAKE_LOG"\n[ "$2" != volumes ] || exit 130\n')
    fake.chmod(0o755)
    result = stub.run(DEV / "scenario.sh", "all", FAKE_SSH_MODE="local", UNOS_DEV_PYTHON=str(fake),
                      FAKE_LOG=str(stub.tmp / "calls"))
    assert result.returncode == 130, result.stdout + result.stderr
    assert (stub.tmp / "calls").read_text().split() == ["files", "volumes"]
    assert "SD writes scenario volumes: " in result.stderr and "SD writes scenario smart" not in result.stderr


def test_a_signal_during_the_measurement_starts_no_check(lock_stub):
    """A Ctrl-C while scenario.sh reads the SD counter ends that ssh only: no check starts after
    it, and the run ends as interrupted. Here the measurement's sync sends SIGINT to the run's
    process group once (scenario.sh runs in a session of its own)."""
    stub = lock_stub
    stub.stub("sync", '#!/usr/bin/env bash\n[ -e "$FAKE_ONCE" ] || { touch "$FAKE_ONCE"; kill -INT 0; }\nexit 0\n')
    fake = stub.bin / "fake-python"
    fake.write_text('#!/usr/bin/env bash\necho "$2" >> "$FAKE_LOG"\n')
    fake.chmod(0o755)
    (stub.tmp / "calls").write_text("")
    result = subprocess.run([str(DEV / "scenario.sh"), "files"], cwd=REPO_ROOT, capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=60, start_new_session=True,
                            env=stub.env(FAKE_SSH_MODE="local", UNOS_DEV_PYTHON=str(fake), FAKE_LOG=str(stub.tmp / "calls"),
                                         FAKE_ONCE=str(stub.tmp / "once")))
    assert (stub.tmp / "once").exists()
    assert result.returncode == 130, result.stdout + result.stderr
    assert (stub.tmp / "calls").read_text() == ""


def test_scenario_sh_and_scenario_py_name_the_same_areas():
    text = (DEV / "scenario.sh").read_text()
    assert f"AREAS=({' '.join(scenario.AREAS)})" in text


# --- the feature-pass skill names the check of every host-level entry ------------------------


SKILL = REPO_ROOT / ".agents" / "skills" / "feature-pass" / "SKILL.md"


def check_table() -> dict[str, list[str]]:
    """The skill's table of device checks: each row's check (its first code span) -> the ids it names."""
    text = SKILL.read_text()
    section = text.split("## Device checks of host-level entries", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[0] in ("Check", "---"):
            continue
        check = re.match(r"`([^`]+)`", cells[0])
        assert check, line
        rows[check.group(1)] = re.findall(r"`([a-z]+\.[a-z0-9-]+)`", cells[1])
    return rows


def host_level_entries() -> list[str]:
    """The ledger's entries whose notes hold the reboot rule of ADR-0018: the host-level ones."""
    ledger = json.loads((REPO_ROOT / "feature-list.json").read_text())
    return sorted(f["id"] for f in ledger["features"] if "ADR-0018" in f.get("notes", ""))


def test_the_feature_pass_skill_names_the_check_of_every_host_level_entry():
    rows = check_table()
    named = [entry for ids in rows.values() for entry in ids]
    ids = {f["id"] for f in json.loads((REPO_ROOT / "feature-list.json").read_text())["features"]}
    assert sorted(set(named) - ids) == []
    assert sorted(e for e in set(named) if named.count(e) > 1) == []
    assert sorted(set(host_level_entries()) - set(named)) == []
    # Every scripted check is one of scenario.sh's areas, or all of them, and every area is in the table.
    scripted = [c for c in rows if c.startswith("scripts/dev/scenario.sh ")]
    assert sorted(c.split()[1] for c in scripted) == sorted(scenario.AREAS + ("all",))
    assert set(rows) - set(scripted) == {"by hand"}
