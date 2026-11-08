"""The scenario checks of the dev device (scripts/dev/scenario.sh and scenario.py, ADR-0304),
tested without the device.

FakeNas stands in for the dev device: the API that scenario.py calls through api.py's relay
(request, upload) and the host that it reads through ssh.sh (the methods of scenario.Device),
over one model in memory of a pool with its folders, trash and volumes, the shares, the UNOS
users, the SMB host accounts and Samba's passdb, the disks and the backups. Its Samba serves
sessions as the device's does for the checks: access per share, a closed tree connect when a
share's access shrinks (ADR-0103), the end of an account's sessions at a password change or a
deletion (ADR-0104), and a failed sign-in that changes passdb.tdb unless the password is one of
the two that the password history holds (ADR-0278).

Covered (the steps of the ledger entry device.scenario-checks-in-repo):
* every area (files, volumes, smart, backups, smb) passes against the fake, prints a PASS line
  with its SD MiB for each step and a summary, and leaves the device as it found it;
* a failure at any API call of a step, before or after the call changed something (a lost
  answer), an interruption, an output that can no longer be written or a refusal of the device
  still removes what the check made, and the check fails; only a backup whose answer was lost is
  named and left; a failed clean-up, also one that meets an answer of an unexpected shape, is
  reported and fails the check; signals wait while the clean-up runs;
* the check refuses to start beside leftovers of an earlier one, and scenario.py refuses to run
  without the lock of scenario.sh;
* the defects the checks exist for fail them: a share access that shrinks without closing the
  open tree connects, a password change that leaves the sessions open, a share folder or an
  upload with the wrong mode, a volume that the File Manager deletes, a password in a log;
* no temporary password reaches the output, a command line or the environment: scenario.Device
  sends them on standard input, and the SMB helper keeps them in a file of mode 600 in RAM that
  it deletes;
* every call the checks make is a route of the supervisor in this tree, with a body its model
  accepts (the contract with the API);
* the scripts that run on the device parse; the SMB helper's session and share-list protocol
  works against a stand-in smbclient, with signing required except for the null session; the
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
import subprocess
import sys
import textwrap
import types
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


# --- the fake device --------------------------------------------------------------------------


class Node:
    def __init__(self, kind: str, mode: int, owner: str = "root", group: str = "root", content: str = "") -> None:
        self.kind = kind  # dir, file or subvolume (a volume, or one made by the files check)
        self.mode = mode
        self.owner = owner
        self.group = group
        self.content = content

    def facts(self) -> tuple:
        return (self.kind, self.mode, self.owner, self.group, self.content)


class Refused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Interrupt(BaseException):
    """Raised by FakeNas in place of KeyboardInterrupt; the tests check the same handling."""


class FakeSmbSession:
    """One smbclient session to a share, as REMOTE_SMB's session mode gives it to scenario.py."""

    def __init__(self, nas: FakeNas, user: str, password: str, share: str) -> None:
        self.nas = nas
        self.user = user
        self.share = share
        self.local: dict[str, str] = {"data.txt": scenario.DATA, "data2.txt": scenario.DATA2}
        self.problem = None
        account = nas.passdb.get(user)
        if account is None or account["password"] != password:
            nas.failed_sign_in(user, password)
            self.problem = "session setup failed: NT_STATUS_LOGON_FAILURE"
        elif nas.level(share, user) is None:
            self.problem = "tree connect failed: NT_STATUS_ACCESS_DENIED"
        self.level = nas.level(share, user)  # what the tree connect was granted
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


class FakeNas:
    """The dev device for scenario.py: request() and upload() as api.DeviceApi has them, and the
    methods of scenario.Device. fail_at and interrupt_at name an API call (0-based) that answers
    500 or raises Interrupt; refuse_device names a device method that fails; bugs names defects
    to inject."""

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
        self.shares = {"DATA": {"path": f"{ROOT}/shared", "comment": "", "access": [], "version": 1}}
        self.backups = {"unos_backup_20261107_000000.tar.gz": None}  # name -> passphrase
        self.quotas = False
        self.sessions: list[FakeSmbSession] = []
        self.passdb_mtime = 1
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
        self.bugs: set[str] = set()
        self.secrets: list[str] = []  # api.DeviceApi's: dev.env's secrets

    # --- what the tests compare

    def snapshot(self) -> dict:
        return {
            "fs": {path: node.facts() for path, node in self.fs.items()},
            "trash": dict(self.trash),
            "users": copy.deepcopy(self.users),
            "accounts": dict(self.accounts),
            "passdb": sorted(self.passdb),
            "shares": copy.deepcopy(self.shares),
            "backups": dict(self.backups),
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
        return {"state": "off", "allowed": False}

    def user_info(self, uid: int) -> dict:
        user = self.users[uid]
        return {"id": uid, "username": user["username"], "is_admin": user["is_admin"], "smb": self.smb_state(uid),
                "warnings": []}

    def share_info(self, name: str) -> dict:
        info = self.shares[name]
        access = [{**a, "smb_state": "active" if a["username"] in self.passdb else "off",
                   "effective": a["username"] in self.passdb} for a in info["access"]]
        return {"name": name, "path": info["path"], "comment": info["comment"], "browseable": True, "guest_ok": False,
                "access": access, "version": info["version"], "served": True, "path_problem": None,
                "available": any(a["effective"] for a in access), "pool": "data", "pool_mounted": True,
                "permissions_ok": True, "import_notes": [], "warnings": []}

    def smb_conf(self) -> str:
        sections = "".join(f"\n[{name}]\n   path = {info['path']}\n" for name, info in sorted(self.shares.items()))
        return ("# Generated by UNOS from its database.\n\n[global]\n   server signing = mandatory\n"
                "   map to guest = Never\n   restrict anonymous = 2\n   log file = /run/samba/log.samba\n"
                "   max log size = 100\n" + sections)

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

    def failed_sign_in(self, user: str, password: str) -> None:
        account = self.passdb.get(user)
        if account is not None and (password not in account["history"] or "no-history" in self.bugs):
            self.passdb_mtime += 1

    # --- the API

    def upload(self, path: str, params: dict, filename: str, content: bytes) -> Answer:
        return self.request("POST", path, params=params, upload=(filename, content.decode()))

    def request(self, method, path, json_body=None, params=None, auth=True, upload=None) -> Answer:
        index = len(self.calls)
        self.calls.append((method, path, copy.deepcopy(json_body), dict(params or {})))
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
        return Answer(200, body if isinstance(body, str) else json.dumps(body))

    def route(self, method, path, body, params, upload):
        parts = path.strip("/").split("/")
        handler = getattr(self, "api_" + parts[0].replace("-", "_"))
        return handler(method, parts[1:], body, params, upload)

    def api_storage(self, method, parts, body, params, upload):
        if parts == ["disks"] and self.partitions:
            return [{"name": DISK, "path": f"/dev/{DISK}", "size": 640, "rotational": True, "type": "disk",
                     "partitions": [{"name": p, "path": f"/dev/{p}", "size": 1} for p in self.partitions]}]
        if parts == ["pools"]:
            return [{"name": "data", "mountpoint": ROOT, "mounted": True, "persistent": True, "read_only": False,
                     "devices": ["/dev/sda"], "total_size": 1, "used_size": 1}]
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
            self.remove(path)
            self.quota_of.pop(path, None)
            self.trash = {t: o for t, o in self.trash.items() if not t.startswith(path + "/")}
            return {"status": "deleted"}
        raise AssertionError(f"the fake does not know {method} /storage/{'/'.join(parts)}")

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

    def api_shares(self, method, parts, body, params, upload):
        if parts == ["smb-status"]:
            return {"samba_tools_missing": [], "group_ok": True, "problems": [], "guest_shares": self.guest_shares,
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
            if path not in self.fs:
                self.fs[path] = Node("dir", 0o750 if "share-folder-mode" in self.bugs else 0o2770, "root", SMB_GROUP)
            self.shares[name] = {"path": path, "comment": body.get("comment", ""), "access": body.get("access", []),
                                 "version": 1}
            return self.share_info(name)
        name = parts[1]
        if name not in self.shares:
            raise Refused(404, f"Share {name} not found")
        if method == "GET":
            return self.share_info(name)
        if method == "PATCH":
            info = self.shares[name]
            if "expected_version" in body and body["expected_version"] != info["version"]:
                raise Refused(409, "The share was changed meanwhile")
            if "access" in body:
                old = {a["username"]: a["level"] for a in info["access"]}
                new = {a["username"]: a["level"] for a in body["access"]}
                shrunk = any(u not in new or (lvl == "write" and new[u] == "read") for u, lvl in old.items())
                info["access"] = body["access"]
                if shrunk:
                    self.close_share(name)
            if "comment" in body:
                info["comment"] = body["comment"]
            info["version"] += 1
            return self.share_info(name)
        del self.shares[name]
        self.close_share(name)
        return {"status": "deleted", "warnings": []}

    def api_auth(self, method, parts, body, params, upload):
        if parts == ["users"] and method == "GET":
            return [self.user_info(uid) for uid in sorted(self.users)]
        if parts == ["users"]:
            uid = self.next_user
            self.next_user += 1
            self.users[uid] = {"username": body["username"], "is_admin": False}
            if body.get("smb_enabled"):
                self.accounts[body["username"]] = 70000 + uid
                self.passdb[body["username"]] = {"password": body["password"], "history": []}
            if "log-password" in self.bugs:
                self.log.append(f"created {body['username']} with {body['password']}")
            return self.user_info(uid)
        uid = int(parts[1])
        if uid not in self.users:
            raise Refused(404, "User not found")
        name = self.users[uid]["username"]
        if method == "PUT":
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
        for share in self.shares.values():
            share["access"] = [a for a in share["access"] if a["username"] != name]
        self.end_sessions(name)
        return {"status": "ok", "message": "User deleted", "warnings": []}

    def api_backup(self, method, parts, body, params, upload):
        if parts == ["list"]:
            return {"backups": [{"filename": n, "encrypted": p is not None, "size": 1} for n, p in sorted(self.backups.items())]}
        if parts == ["create"]:
            name = f"unos_backup_20261108_1200{len(self.backups):02d}.tar.gz" + (".enc" if body.get("encrypt") else "")
            self.backups[name] = body.get("passphrase") if body.get("encrypt") else None
            return {"status": "success", "encrypted": bool(body.get("encrypt")), "backup": {"filename": name}}
        if parts[0] == "restore":
            if parts[1] not in self.backups:
                raise Refused(404, "Backup not found")
            passphrase = (body or {}).get("passphrase")
            if self.backups[parts[1]] is not None and not passphrase:
                raise Refused(400, "Passphrase required for encrypted backup")
            if passphrase != self.backups[parts[1]]:
                raise Refused(400, "Wrong passphrase, or the backup is damaged")
            raise AssertionError("the checks never restore a backup")
        if parts[0] not in self.backups:
            raise Refused(404, "Backup not found")
        del self.backups[parts[0]]
        return {"status": "deleted", "filename": parts[0]}

    # --- the host (scenario.Device's methods)

    def device_call(self, name: str) -> None:
        if self.refuse_device == name:
            raise scenario.Failed(f"the device answered status 1 for {name}")

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
            return self.smb_conf()
        node = self.fs.get(path)
        return node.content if node is not None and node.kind == "file" else None

    def facts(self):
        self.device_call("facts")
        accounts = [f"{n}:x:{uid}:70000:UNOS SMB user:/nonexistent:/usr/sbin/nologin" for n, uid in sorted(self.accounts.items())]
        digest = hashlib.sha256("\n".join(accounts).encode()).hexdigest()
        return {
            "now": 1_800_000_000,
            "tools": ["getent", "pdbedit", "smbclient", "stdbuf", "testparm"],
            "sha256": {"/etc/passwd": digest, "/etc/group": "g", "/etc/shadow": digest, "/etc/gshadow": "gs",
                       "/etc/samba/smb.conf": hashlib.sha256(self.smb_conf().encode()).hexdigest()},
            "accounts": accounts,
            "group": "unos-smb:x:70000:",
            "testparm": True,
            "passdb": str(self.passdb_mtime),
            "sizes": {"/var/log/samba/log.smbd": "120", "/var/log/samba/log.nmbd": "missing", "/run/samba/log.samba": "800"},
        }

    def passdb_entries(self):
        self.device_call("passdb_entries")
        return sorted(self.passdb), "3"

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
        account = self.passdb.get(user)
        if account is None or account["password"] != password:
            self.failed_sign_in(user, password)
            return scenario.SmbListing(1, "session setup failed: NT_STATUS_LOGON_FAILURE")
        disks = [f"Disk|{name}|" for name in sorted(self.shares) if self.level(name, user)]
        return scenario.SmbListing(0, "\n".join(disks + ["IPC|IPC$|IPC Service"]))

    def smb_anonymous(self):
        self.device_call("smb_anonymous")
        return scenario.SmbListing(1, "session setup failed: NT_STATUS_ACCESS_DENIED")

    def smb_session(self, user, password, share):
        self.device_call("smb_session")
        return FakeSmbSession(self, user, password, share)


@pytest.fixture
def nas():
    return FakeNas()


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
                  "during the session", "NT_STATUS_LOGON_FAILURE", "passdb.tdb", "anonymous", "mandatory",
                  "/run/samba/log.samba", "no password", "smb.conf", "host account"):
        assert words in text, words
    # The temporary account had read-write access, then read, then none, then read-write again.
    levels = [tuple(a["level"] for a in body["access"]) for method, path, body, _ in nas.calls
              if method == "PATCH" and body and "access" in body]
    assert levels == [("read",), (), ("write",)]


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


@pytest.mark.parametrize("area", ["files", "smb", "volumes"])
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


@pytest.mark.parametrize("area", ["files", "volumes", "smb"])
def test_a_check_refuses_to_start_beside_leftovers_of_an_earlier_one(nas, area):
    nas.users[9] = {"username": "unoscheck0a1b2c", "is_admin": False}
    nas.fs[f"{ROOT}/unoscheck0a1b2c"] = Node("dir", 0o755)
    nas.shares["unoscheck0a1b2c"] = {"path": f"{ROOT}/unoscheck0a1b2c", "comment": "", "access": [], "version": 1}
    before = nas.snapshot()
    status, output = run_check(area, nas)
    assert status == 1
    assert re.search(r"^FAIL  2\. the device holds nothing of an earlier check: .*unoscheck0a1b2c", output, re.M), output
    assert nas.snapshot() == before
    assert not [c for c in nas.calls if c[0] != "GET"]


# --- the defects the checks exist for -------------------------------------------------------


@pytest.mark.parametrize("area, bug, words", [
    ("smb", "no-close-share", "lowering the access to read during the session makes its next write fail"),
    ("smb", "sessions-stay", "a new password set by an administrator ends the user's open session"),
    ("smb", "share-folder-mode", "2770 root:unos-smb"),
    ("smb", "upload-mode", "660 root:unos-smb"),
    ("smb", "no-history", "passdb.tdb"),
    ("smb", "log-password", "no password of the check"),
    ("files", "volume-deletable", "refused with 409"),
    ("files", "upload-mode", "644 root:root"),
    ("volumes", "volume-delete-ignores-shares", "names the share"),
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


def test_the_smart_check_refuses_a_partition_where_a_disk_has_one(nas):
    nas.partitions = [f"{DISK}1"]
    status, output = run_check("smart", nas)
    assert status == 0, output
    assert f"/health/disk/{DISK}1" in [path for _, path, _, _ in nas.calls] and f"mmcblk1, sdz, {DISK}1" in output


def test_with_a_guest_share_the_smb_check_tries_no_null_session(nas, monkeypatch):
    """A guest share renders restrict anonymous = 1 and map to guest = Bad User (ADR-0098): a
    null session may then list the guest shares, so the check does not expect its refusal, and
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


def test_the_backups_check_refuses_restores_and_never_restores(nas):
    status, output = run_check("backups", nas)
    assert status == 0, output
    restores = [(path, body) for method, path, body, _ in nas.calls if path.startswith("/backup/restore/")]
    assert len(restores) == 3
    assert restores[0][0] == "/backup/restore/unoscheck1a2b3c.tar.gz"
    assert restores[1][1] == {} and set(restores[2][1]) == {"passphrase"}


def test_no_temporary_secret_reaches_the_output(nas):
    seen = []
    real = nas.request

    def request(method, path, json_body=None, params=None, auth=True, upload=None):
        if json_body and isinstance(json_body, dict):
            seen.extend(v for k, v in json_body.items() if k == "password" or k == "passphrase")
        return real(method, path, json_body=json_body, params=params, auth=auth, upload=upload)

    nas.request = request
    out = []
    for area in ("smb", "backups"):
        status, output = run_check(area, nas)
        assert status == 0, output
        out.append(output)
    assert len(set(seen)) >= 4 and all(len(s) >= 16 for s in seen)
    assert not any(s in text for s in seen for text in out)


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
    validates."""
    from pydantic import TypeAdapter
    from starlette.routing import compile_path

    from .test_authz import _http_operations

    operations = [(method, compile_path(path)[0], dependant) for method, path, dependant in _http_operations()]
    calls = []
    for area in scenario.AREAS:
        nas = FakeNas()
        assert run_check(area, nas)[0] == 0
        calls += nas.calls
    problems = []
    for method, path, body, params in calls:
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
            problems.append(f"{method} {path}: the body {body!r} is refused: {e}")
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


def test_the_device_reads_pdbedit_without_the_read_only_namespace(stub):
    """tdbsam opens passdb.tdb read-write, which a read-only root refuses (ADR-0304)."""
    device = device_with_stub(stub)
    (stub.log / "reply").write_text("user test\nuser unoscheck1a2b3c\nhistory account policy value is: 3\n")
    names, history = device.passdb_entries()
    assert names == ["test", "unoscheck1a2b3c"] and history == "3"
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
    listing = device.smb_list("unoscheck1a2b3c", password)
    anonymous = device.smb_anonymous()
    assert listing.ok and listing.disks == ["unoscheck1a2b3c"]
    assert not anonymous.ok and anonymous.status == "NT_STATUS_ACCESS_DENIED" and not anonymous.disks
    assert first.ok and "putting file a.txt" in first.output and not first.ended, first.output
    assert not second.ok and second.status == "NT_STATUS_ACCESS_DENIED"
    assert third.ok and scenario.DATA.strip() in third.output
    assert not fourth.ok and fourth.ended
    log = (stub.tmp / "smb.log").read_text()
    assert f"auth 600 username = unoscheck1a2b3c|password = {password}|" in log
    assert password not in "".join(re.findall(r"^argv .*$", log, re.M))
    runs = log.split("argv ")[1:]
    # The session and the share list sign; the null session (no user, no password) leaves
    # signing to the server, so that only the server can refuse it.
    session = [r for r in runs if "//127.0.0.1/unoscheck1a2b3c" in r]
    lists = [r for r in runs if "-g -L //127.0.0.1" in r]
    assert len(session) == 1 and "signing required" in session[0]
    assert len(lists) == 2 and "-A " in lists[0] and "\nsigning required" in lists[0] and "ipc signing required" in lists[0]
    assert "-N -U% " in lists[1] and "-A " not in lists[1]
    assert "\nsigning not required" in lists[1] and "ipc signing not required" in lists[1]
    for folder in re.findall(r"^dir (\S+)$", log, re.M):
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
