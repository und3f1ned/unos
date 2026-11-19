"""iSCSI targets of volumes (ADR-0417): the API, the apply to configfs, the restore and the refusals.

The host is test_volumes' FakeHost with the pool "data" mounted and its volume "iscsi", and FakeLio:
a model of the kernel's configfs for LIO (what mkdir, rmdir, writes and links of each kind of entry
do, and refuse), of the host's scripts of shares/iscsi.py, of the LUN files and of a login, which
decides as LIO does from what is in configfs: a target portal group that is enabled, CHAP required
(attrib/authentication 1) or not, the initiator's ACL with its CHAP values, and the mapped LUN. Its
defaults are the permissive ones, so that only UNOS's writes make CHAP mandatory. The scripts
themselves run under sh and dash in test_iscsi_scripts.py.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import stat
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src import database
from src.audit import router as audit_router
from src.database import Base, get_db
from src.host import HostResult
from src.main import app
from src.shares import iscsi
from src.shares.models import IscsiInitiator, IscsiTarget
from src.storage import pools
from src.storage.schemas import VolumeInfo

from .audit_helpers import audit_entries
from .test_pool_persistence import DATA_UUID
from .test_volumes import api as volumes_api  # noqa: F401  (a fixture: the admin's client on the real app)
from .test_volumes import host as volumes_host  # noqa: F401  (a fixture: the FakeHost with the pool "data")

API = "/api/v1/shares/iscsi"
CFS = iscsi.TARGET_ROOT
VOLUME = "/mnt/pools/data/iscsi"
FOLDER = f"{VOLUME}/.unos-iscsi"
LINUX = "iqn.1993-08.org.debian:01:5f3c0a1b2c3d"
WINDOWS = "iqn.1991-05.com.microsoft:desktop-abc123"
OTHER = "iqn.2004-10.com.ubuntu:01:0123456789ab"
GIB = 1 << 30
PORTALS = ["192.168.1.10:3260", "[fd00::10]:3260"]

INTERFACES = [
    {"ifname": "lo", "flags": ["LOOPBACK", "UP", "LOWER_UP"], "operstate": "UNKNOWN",
     "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8},
                   {"family": "inet6", "local": "::1", "prefixlen": 128}]},
    {"ifname": "eth0", "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "operstate": "UP",
     "addr_info": [{"family": "inet", "local": "192.168.1.10", "prefixlen": 24},
                   {"family": "inet6", "local": "fd00::10", "prefixlen": 64},
                   {"family": "inet6", "local": "fe80::10", "prefixlen": 64},
                   {"family": "inet6", "local": "2001:db8::10", "prefixlen": 64}]},
    # Docker's bridge, a tunnel and an interface that is down: no portal there.
    {"ifname": "docker0", "flags": ["BROADCAST", "MULTICAST", "UP"], "operstate": "UP",
     "addr_info": [{"family": "inet", "local": "172.17.0.1", "prefixlen": 16}]},
    {"ifname": "wg0", "flags": ["POINTOPOINT", "NOARP", "UP", "LOWER_UP"], "operstate": "UNKNOWN",
     "addr_info": [{"family": "inet", "local": "10.8.0.1", "prefixlen": 24}]},
    {"ifname": "eth1", "flags": ["BROADCAST", "MULTICAST"], "operstate": "DOWN",
     "addr_info": [{"family": "inet", "local": "10.0.0.5", "prefixlen": 8}]},
]


@dataclass
class Node:
    kind: str  # dir, attr or link
    value: str = ""
    user: bool = False  # made with mkdir or ln, not one of the entries the kernel makes with it


class FakeLio:
    """The kernel's LIO in configfs, the host's scripts of shares/iscsi.py and the LUN files."""

    def __init__(self, fake) -> None:
        self.fake = fake
        self.modules = True  # the kernel has LIO's modules
        self.nodes: dict[str, Node] = {}
        self.interfaces = json.loads(json.dumps(INTERFACES))
        self.avail = 500 * GIB
        self.files: dict[str, list[int]] = {}  # path -> [uid, mode, size]
        self.folders: dict[str, list] = {}  # path -> [uid, mode, immutable]
        self.applies: list[str] = []  # the standard input of each apply
        self.fail_on: str | None = None  # an operation's path that the kernel refuses
        self.lio_created: list[str] = []  # files that LIO would have made empty (O_CREAT)
        other = fake._dispatch

        def dispatch(cmd, input):
            result = self.dispatch(cmd, input)
            return other(cmd, input) if result is None else result

        fake._dispatch = dispatch

    # -- the commands ---------------------------------------------------------------------------

    def dispatch(self, cmd, input):
        if cmd[:2] == ["/bin/sh", "-c"]:
            script, name, args = cmd[2], cmd[3], cmd[4:]
            if script == iscsi.READ_SCRIPT:
                assert args == [iscsi.CONFIGFS, iscsi.IQN_PREFIX, iscsi.HBA]
                return HostResult(0, self.read(), "")
            if script == iscsi.MODULES_SCRIPT:
                assert args == [iscsi.CONFIGFS, *iscsi.MODULES]
                if not self.modules:
                    return HostResult(1, "", "modprobe: FATAL: Module target_core_mod not found")
                self.load()
                return HostResult(0, "", "")
            if script == iscsi.PREPARE_SCRIPT:
                return HostResult(0, self.prepare(args), "")
            if script == iscsi.CREATE_SCRIPT:
                return self.create(*args)
            if script == iscsi.DELETE_SCRIPT:
                return self.delete(*args)
            if script == iscsi.APPLY_SCRIPT:
                assert args == [CFS] and name == "unos-iscsi-apply"
                self.applies.append(input)
                return self.apply(input)
            return None
        if cmd[:4] == ["modprobe", "-n", "-q", "-a"]:
            return HostResult(0 if self.modules else 1, "", "")
        if cmd == ["ip", "-j", "addr", "show"]:
            return HostResult(0, json.dumps(self.interfaces), "")
        if cmd[:4] == ["df", "-B1", "--output=avail", "--"]:
            return HostResult(0, f"      Avail\n{self.avail}\n", "")
        return None

    def load(self) -> None:
        for path in (CFS, f"{CFS}/core", f"{CFS}/iscsi"):
            self.nodes.setdefault(path, Node("dir"))

    def reboot(self) -> None:
        """configfs is in RAM."""
        self.nodes.clear()

    # -- the LUN files --------------------------------------------------------------------------

    def create(self, volume, folder, name, size):
        if volume not in self.fake.dirs:
            return HostResult(1, "", f"mkdir: cannot create directory '{volume}/{folder}': No such file or directory")
        path = f"{volume}/{folder}"
        entry = self.folders.setdefault(path, [0, 0o700, False])
        if entry[:2] != [0, 0o700]:
            return HostResult(3, "", f"{path} is not a folder of root with mode 700")
        file = f"{path}/{name}"
        entry[2] = True
        if file in self.files:
            return HostResult(4, "", "")
        self.files[file] = [0, 0o600, int(size)]
        return HostResult(0, "", "")

    def delete(self, volume, folder, name):
        path = f"{volume}/{folder}"
        if path not in self.folders:
            return HostResult(0, "", "")
        self.files.pop(f"{path}/{name}", None)
        if not any(file.startswith(path + "/") for file in self.files):
            del self.folders[path]
        return HostResult(0, "", "")

    def prepare(self, args):
        folder, rest, out = args[0], args[1:], []
        for index in range(0, len(rest), 3):
            volume, name, size = rest[index:index + 3]
            path = f"{volume}/{folder}"
            entry = self.folders.get(path)
            file = self.files.get(f"{path}/{name}")
            if entry is None:
                out.append(f"missing {name}")
            elif entry[:2] != [0, 0o700]:
                out.append(f"unsafe {name}")
            else:
                entry[2] = True
                if file is None:
                    out.append(f"missing {name}")
                elif file != [0, 0o600, int(size)]:
                    out.append(f"wrong {name}")
                else:
                    out.append(f"ok {name}")
        return "".join(line + "\n" for line in out)

    # -- configfs -------------------------------------------------------------------------------

    def value(self, path: str) -> str:
        return self.nodes[path].value

    def children(self, path: str) -> list[str]:
        prefix = path + "/"
        return sorted(p for p in self.nodes if p.startswith(prefix) and "/" not in p[len(prefix):])

    def _add(self, path: str, kind: str = "dir", value: str = "", user: bool = False) -> None:
        self.nodes[path] = Node(kind, value, user)

    def _auto(self, path: str, attrs: dict[str, str], dirs: tuple[str, ...] = ()) -> None:
        for name in dirs:
            self._add(f"{path}/{name}")
        for name, value in attrs.items():
            self._add(f"{path}/{name}", "attr", value)

    def mkdir(self, path: str) -> str | None:
        parent, _, name = path.rpartition("/")
        if path in self.nodes:
            return "File exists"
        if self.nodes.get(parent, Node("attr")).kind != "dir":
            return "No such file or directory"
        parts = path[len(CFS) + 1:].split("/")
        if parts[0] == "core" and len(parts) == 2 and parts[1].startswith("fileio_"):
            self._add(path, user=True)
        elif parts[0] == "core" and len(parts) == 3:
            self._add(path, user=True)
            self._auto(path, {"control": "", "enable": "0", "udev_path": "", "wwn/vpd_unit_serial": "",
                              "attrib/emulate_tpu": "0", "attrib/emulate_tpws": "0",
                              "attrib/emulate_write_cache": "0"}, ("wwn", "attrib"))
        elif parts[0] == "iscsi" and len(parts) == 2:
            self._add(path, user=True)
        elif parts[0] == "iscsi" and len(parts) == 3 and name.startswith("tpgt_"):
            self._add(path, user=True)
            self._auto(path, {"enable": "0", "attrib/authentication": "0", "attrib/generate_node_acls": "1",
                              "attrib/cache_dynamic_acls": "1", "attrib/demo_mode_write_protect": "0",
                              "param/AuthMethod": "CHAP,None"}, ("attrib", "param", "lun", "acls", "np"))
        elif parts[0] == "iscsi" and len(parts) == 5 and parts[3] == "lun" and name.startswith("lun_"):
            self._add(path, user=True)
        elif parts[0] == "iscsi" and len(parts) == 5 and parts[3] == "np":
            address = name.rpartition(":")[0].strip("[]")
            if not any(info["local"] == address for i in self.interfaces for info in i["addr_info"]):
                return "Cannot assign requested address"
            self._add(path, user=True)
        elif parts[0] == "iscsi" and len(parts) == 5 and parts[3] == "acls":
            self._add(path, user=True)
            self._auto(path, {"auth/userid": "", "auth/password": "", "auth/userid_mutual": "",
                              "auth/password_mutual": "",
                              "info": f"No active iSCSI Session for Initiator Endpoint: {name}\n"}, ("auth",))
        elif parts[0] == "iscsi" and len(parts) == 6 and parts[3] == "acls" and name.startswith("lun_"):
            self._add(path, user=True)
        else:
            return "Operation not permitted"
        return None

    def rmdir(self, path: str) -> str | None:
        node = self.nodes.get(path)
        if node is None or node.kind != "dir" or not node.user:
            return "Operation not permitted"
        prefix = path + "/"
        if any(p.startswith(prefix) and n.user for p, n in self.nodes.items()):
            return "Directory not empty"
        if any(n.kind == "link" and n.value == path for n in self.nodes.values()):
            return "Device or resource busy"
        for p in [p for p in self.nodes if p == path or p.startswith(prefix)]:
            del self.nodes[p]
        return None

    def write(self, path: str, value: str) -> str | None:
        node = self.nodes.get(path)
        if node is None or node.kind != "attr":
            return "Permission denied"
        parent, _, name = path.rpartition("/")
        if parent.startswith(f"{CFS}/core/") and name == "enable" and parent.count("/") == CFS.count("/") + 2:
            if node.value == "1" or value != "1":
                return "File exists"
            control = self.value(f"{parent}/control")
            if "fd_dev_name=" not in control:
                return "Invalid argument"
            fields = dict(item.split("=", 1) for item in control.split(","))
            if fields["fd_dev_name"] not in self.files:
                self.lio_created.append(fields["fd_dev_name"])
        elif name.startswith("emulate_tp"):
            if self.value(f"{parent.rpartition('/')[0]}/enable") != "1":
                return "the file is not open: LIO would oops"
        elif name == "vpd_unit_serial":
            obj = parent.rpartition("/")[0]
            if any(n.kind == "link" and n.value == obj for n in self.nodes.values()):
                return "Invalid argument"
        elif name == "enable" and node.value == value == "1":
            return "Invalid argument"
        node.value = value
        return None

    def link(self, path: str, target: str) -> str | None:
        parent = path.rpartition("/")[0]
        if path in self.nodes:
            return "File exists"
        if not self.nodes.get(parent, Node("attr")).user:
            return "Operation not permitted"
        if target not in self.nodes:
            return "No such file or directory"
        if target.startswith(f"{CFS}/core/") and self.value(f"{target}/enable") != "1":
            return "Invalid argument"
        if "/acls/" in parent and not any(self.nodes[c].kind == "link" for c in self.children(target)):
            return "Invalid argument"
        self.nodes[path] = Node("link", target, True)
        return None

    def apply(self, text: str) -> HostResult:
        for line in text.splitlines():
            op, path, value = line.split("\t")
            assert path.startswith(CFS + "/") and "/../" not in path, path
            if path == self.fail_on:
                return HostResult(1, "", f"failed: {op} {path}")
            if op == "mkdir":
                error = self.mkdir(path)
            elif op == "rmdir":
                error = self.rmdir(path)
            elif op == "write":
                error = self.write(path, value)
            elif op == "link":
                error = self.link(path, value)
            elif op == "unlink":
                error = None if self.nodes.get(path, Node("dir")).kind == "link" else "Not a link"
                if error is None:
                    del self.nodes[path]
            else:
                error = "unknown operation"
            if error is not None:
                return HostResult(1, "", f"failed: {op} {path} ({error})")
        return HostResult(0, "", "")

    def read(self) -> str:
        """What READ_SCRIPT prints from this configfs."""
        if CFS not in self.nodes:
            return ""
        out = ["loaded"] if f"{CFS}/iscsi" in self.nodes else []
        hba = f"{CFS}/core/{iscsi.HBA}"
        if hba in self.nodes:
            out.append("hba")
            for o in self.children(hba):
                out.append(f"object {o.rpartition('/')[2]} {self.value(o + '/enable')} {self.value(o + '/udev_path')}")
        for d in self.children(f"{CFS}/iscsi"):
            n = d.rpartition("/")[2]
            if not n.startswith(iscsi.IQN_PREFIX):
                continue
            out.append(f"target {n}")
            for g in self.children(d):
                p = g.rpartition("/")[2]
                out.append(f"tpg {n} {p} {self.value(g + '/enable')} {self.value(g + '/attrib/authentication')} "
                           f"{self.value(g + '/attrib/generate_node_acls')}")
                for lun in self.children(g + "/lun"):
                    out.append(f"lun {n} {p} {lun.rpartition('/')[2]}")
                    for x in self.children(lun):
                        out.append(f"lunlink {n} {p} {lun.rpartition('/')[2]} {x.rpartition('/')[2]}")
                for x in self.children(g + "/np"):
                    out.append(f"portal {n} {p} {x.rpartition('/')[2]}")
                for a in self.children(g + "/acls"):
                    initiator = a.rpartition("/")[2]
                    out.append(f"acl {n} {p} {initiator}")
                    for m in self.children(a):
                        if m.rpartition("/")[2].startswith("lun_"):
                            out.append(f"mapped {n} {p} {initiator} {m.rpartition('/')[2]}")
                            for x in self.children(m):
                                out.append(f"mappedlink {n} {p} {initiator} {m.rpartition('/')[2]} {x.rpartition('/')[2]}")
                    if "No active iSCSI Session" not in self.value(a + "/info"):
                        out.append(f"session {n} {p} {initiator}")
        return "".join(line + "\n" for line in out)

    # -- what an initiator sees -----------------------------------------------------------------

    def login(self, iqn: str, initiator: str, user: str | None = None, secret: str | None = None,
              mutual: str | None = None, portal: str = PORTALS[0]) -> str:
        """LIO's decision for a login to ``iqn`` through ``portal``: ok, refused, no target or no LUN."""
        g = f"{CFS}/iscsi/{iqn}/tpgt_1"
        if g not in self.nodes or self.value(g + "/enable") != "1" or f"{g}/np/{portal}" not in self.nodes:
            return "no target"
        acl = f"{g}/acls/{initiator}"
        if acl not in self.nodes and self.value(g + "/attrib/generate_node_acls") != "1":
            return "refused"
        chap_required = self.value(g + "/attrib/authentication") == "1" or "None" not in self.value(
            g + "/param/AuthMethod").split(",")
        if user is None:
            if chap_required:
                return "refused"
        elif acl not in self.nodes or (self.value(acl + "/auth/userid"), self.value(acl + "/auth/password")) != (
                user, secret):
            return "refused"
        if mutual is not None and (acl not in self.nodes or self.value(acl + "/auth/password_mutual") != mutual):
            return "refused"
        if acl not in self.nodes or not any(self.nodes[c].kind == "link" for c in self.children(acl + "/lun_0")):
            return "no LUN"
        return "ok"

    def session(self, iqn: str, initiator: str) -> None:
        self.nodes[f"{CFS}/iscsi/{iqn}/tpgt_1/acls/{initiator}/info"].value = (
            f"InitiatorName: {initiator}\nSession State: TARG_SESS_STATE_LOGGED_IN\n")

    def unos_paths(self) -> list[str]:
        return sorted(p for p in self.nodes if p.startswith((f"{CFS}/core/{iscsi.HBA}", f"{CFS}/iscsi/{iscsi.IQN_PREFIX}")))


@pytest.fixture
def host(request):
    return request.getfixturevalue("volumes_host")


@pytest.fixture
def api(request):
    return request.getfixturevalue("volumes_api")


@pytest.fixture
def lio(host, monkeypatch, tmp_path):
    host.subvols.add(DATA_UUID, "iscsi")
    host.dirs.add(VOLUME)
    fake = FakeLio(host)
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(iscsi, "SECRETS_FILE", str(tmp_path / "iscsi_secrets.json"))
    monkeypatch.setattr(iscsi, "_problems", {})
    monkeypatch.setattr(iscsi, "_last_portals", None)
    monkeypatch.setattr(iscsi, "_index", {})
    db_path = tmp_path / "unos.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool),
                               expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", maker)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    fake.db_path = db_path
    yield fake
    app.dependency_overrides.pop(get_db, None)


def create(api, name="backup", **fields):
    body = {"name": name, "pool": "data", "volume": "iscsi", "size_gib": 2, "initiators": [LINUX], **fields}
    return api.post(f"{API}/targets", json=body)


def created(api, name="backup", **fields):
    response = create(api, name, **fields)
    assert response.status_code == 201, response.text
    return response.json()


def listed(api) -> dict:
    response = api.get(API)
    assert response.status_code == 200, response.text
    return response.json()


def stored_secrets(tmp_path) -> dict:
    return json.loads((tmp_path / "iscsi_secrets.json").read_text())


def audit() -> list[dict]:
    return [e for e in audit_entries(__import__("pathlib").Path(audit_router.AUDIT_FILE))
            if e["action"].startswith("iscsi_")]


def restore() -> None:
    asyncio.run(iscsi.restore_targets())


def rows(lio) -> list[tuple[str, list[str]]]:
    engine = create_engine(f"sqlite:///{lio.db_path}")
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        targets = session.execute(select(IscsiTarget)).scalars().all()
        found = [(t.name, [i.name for i in session.execute(
            select(IscsiInitiator).where(IscsiInitiator.target_id == t.id)).scalars()]) for t in targets]
    engine.dispose()
    return found


# ---------------------------------------------------------------------------
# Step 1: a target from a volume with one LUN and CHAP credentials
# ---------------------------------------------------------------------------


class TestCreate:
    def test_a_target_serves_a_sparse_file_of_the_volume_as_its_one_lun_with_chap(self, api, lio, tmp_path):
        body = created(api, size_gib=2)

        target, secret = body["target"], body["chap_secret"]
        iqn = target["iqn"]
        uid = iqn.rpartition(".")[2]
        assert iqn.startswith("iqn.2003-01.org.linux-iscsi.unos:backup.") and len(uid) == 8
        assert iscsi.SECRET_PATTERN.fullmatch(secret) and len(secret) == 16
        assert body["mutual_secret"] is None
        assert {k: target[k] for k in ("name", "pool", "volume", "size_gib", "path", "initiators", "chap_user",
                                       "mutual", "served", "problem", "sessions")} == {
            "name": "backup", "pool": "data", "volume": "iscsi", "size_gib": 2,
            "path": f"{FOLDER}/backup.lun", "initiators": [LINUX], "chap_user": "backup", "mutual": False,
            "served": True, "problem": None, "sessions": [],
        }
        # The LUN: a new file of root, mode 0600, in a folder of root, mode 0700, kept immutable.
        assert lio.files == {f"{FOLDER}/backup.lun": [0, 0o600, 2 * GIB]}
        assert lio.folders == {FOLDER: [0, 0o700, True]}
        # Its storage object: fileio over that file, write-through, UNMAP on, a stable serial.
        [obj] = [p for p in lio.children(f"{CFS}/core/{iscsi.HBA}")]
        full_uid = obj.rpartition("/")[2]
        assert full_uid.startswith(uid) and len(full_uid) == 32
        assert lio.value(f"{obj}/control") == f"fd_dev_name={FOLDER}/backup.lun,fd_dev_size={2 * GIB}"
        assert lio.value(f"{obj}/enable") == "1"
        assert lio.value(f"{obj}/attrib/emulate_write_cache") == "0"
        assert (lio.value(f"{obj}/attrib/emulate_tpu"), lio.value(f"{obj}/attrib/emulate_tpws")) == ("1", "1")
        assert lio.value(f"{obj}/wwn/vpd_unit_serial").replace("-", "") == full_uid
        assert lio.lio_created == []
        # The target: CHAP required, no initiator but the allowed ones, LUN 0, the LAN's portals.
        g = f"{CFS}/iscsi/{iqn}/tpgt_1"
        assert lio.value(f"{g}/enable") == "1"
        assert {a: lio.value(f"{g}/attrib/{a}") for a, _ in iscsi.TPG_ATTRIBUTES} == dict(iscsi.TPG_ATTRIBUTES)
        assert lio.value(f"{g}/param/AuthMethod") == "CHAP"
        assert lio.nodes[f"{g}/lun/lun_0/unos"].value == obj
        assert lio.children(f"{g}/np") == [f"{g}/np/{portal}" for portal in sorted(PORTALS)]
        assert lio.children(f"{g}/acls") == [f"{g}/acls/{LINUX}"]
        assert (lio.value(f"{g}/acls/{LINUX}/auth/userid"), lio.value(f"{g}/acls/{LINUX}/auth/password")) == (
            "backup", secret)
        assert lio.nodes[f"{g}/acls/{LINUX}/lun_0/unos"].value == f"{g}/lun/lun_0"
        assert listed(api)["targets"] == [target]

    def test_the_portals_are_the_lan_addresses_only(self, api, lio):
        """Not the loopback, Docker's bridge, a tunnel, an interface that is down, a global or a
        link-local IPv6 address."""
        body = listed(api)
        assert body["portals"] == PORTALS and body["kernel_support"] is True
        iqn = created(api)["target"]["iqn"]
        assert lio.children(f"{CFS}/iscsi/{iqn}/tpgt_1/np") == [
            f"{CFS}/iscsi/{iqn}/tpgt_1/np/{portal}" for portal in sorted(PORTALS)]

    def test_the_secret_is_stored_mode_0600_and_shown_only_in_the_answer(self, api, lio, tmp_path, caplog):
        caplog.set_level(logging.DEBUG)
        body = created(api, mutual=True)
        secret, mutual = body["chap_secret"], body["mutual_secret"]
        assert mutual and mutual != secret
        path = tmp_path / "iscsi_secrets.json"
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        uid = body["target"]["iqn"].rpartition(".")[2]
        [(key, value)] = stored_secrets(tmp_path).items()
        assert key.startswith(uid) and value == {"chap": secret, "mutual": mutual}
        listing = json.dumps(listed(api))
        for text in (secret, mutual):
            assert text not in listing
            assert text not in caplog.text
            assert text not in json.dumps(audit())
            assert not [cmd for cmd in lio.fake.calls if any(text in part for part in cmd)]
            assert text.encode() not in lio.db_path.read_bytes()
        assert "x" * 12 not in repr(iscsi.IscsiTargetCreate(name="a", pool="p", volume="v", size_gib=1,
                                                           initiators=[], chap_secret="x" * 12))
        [entry] = audit()
        assert entry["action"] == "iscsi_target_created" and entry["resource_id"] == "backup"
        assert entry["details"] == (
            f"Created the iSCSI target backup ({body['target']['iqn']}), a LUN of 2 GiB in the volume iscsi of the "
            f"pool data, CHAP user backup, mutual CHAP, initiators {LINUX}")

    def test_a_typed_secret_and_user(self, api, lio):
        body = created(api, chap_user="nas.user@home", chap_secret="Abc-123_xyz.!", initiators=[WINDOWS.upper()])
        assert body["chap_secret"] == "Abc-123_xyz.!"
        g = f"{CFS}/iscsi/{body['target']['iqn']}/tpgt_1"
        # Initiator names are lower case, as iSCSI compares them.
        assert lio.value(f"{g}/acls/{WINDOWS}/auth/userid") == "nas.user@home"
        assert lio.value(f"{g}/acls/{WINDOWS}/auth/password") == "Abc-123_xyz.!"

    @pytest.mark.parametrize(
        ("fields", "detail"),
        [
            ({"name": "Backup"}, iscsi.NAME_RULE),
            ({"name": "-x"}, iscsi.NAME_RULE),
            ({"name": "a" * 33}, iscsi.NAME_RULE),
            ({"pool": "../etc"}, None),
            ({"volume": "a/b"}, None),
            ({"size_gib": 0}, iscsi.SIZE_RULE),
            ({"size_gib": iscsi.MAX_SIZE_GIB + 1}, iscsi.SIZE_RULE),
            ({"initiators": []}, iscsi.NO_INITIATORS),
            ({"initiators": ["windows-pc"]}, f"windows-pc {iscsi.INITIATOR_RULE}"),
            ({"initiators": ["iqn.2026-13.com.example:x"]}, f"iqn.2026-13.com.example:x {iscsi.INITIATOR_RULE}"),
            ({"initiators": [f"iqn.2026-01.com.example:{i}" for i in range(17)]}, iscsi.TOO_MANY_INITIATORS),
            ({"chap_user": "a b"}, iscsi.USER_RULE),
            # LIO takes a value that starts with NULL for none.
            ({"chap_user": "NULLuser"}, iscsi.USER_RULE),
            ({"chap_secret": "NULLsecret-12"}, iscsi.SECRET_RULE),
            ({"mutual": True, "mutual_secret": "NULLsecret-34"}, iscsi.SECRET_RULE),
            ({"chap_secret": "short"}, iscsi.SECRET_RULE),
            ({"chap_secret": "seventeen-chars-x"}, iscsi.SECRET_RULE),
            ({"chap_secret": "with space 1234"}, iscsi.SECRET_RULE),
            ({"chap_secret": "quote'1234567"}, iscsi.SECRET_RULE),
            ({"mutual": True, "chap_secret": "Same-secret-12", "mutual_secret": "Same-secret-12"}, iscsi.SAME_SECRETS),
            ({"mutual": True, "mutual_secret": "bad"}, iscsi.SECRET_RULE),
        ],
    )
    def test_values_that_break_their_rules_are_refused_and_nothing_is_made(self, api, lio, fields, detail):
        response = create(api, **fields)
        assert response.status_code == 400, response.text
        if detail is not None:
            assert response.json()["detail"] == detail
        assert lio.files == {} and lio.unos_paths() == [] and lio.applies == []
        assert rows(lio) == [] and audit() == []

    def test_eui_and_naa_names(self, api, lio):
        body = created(api, initiators=["eui.0123456789ABCDEF", "naa.0123456789abcdef0123456789abcdef"])
        assert body["target"]["initiators"] == ["eui.0123456789abcdef", "naa.0123456789abcdef0123456789abcdef"]

    def test_a_name_that_is_taken(self, api, lio):
        created(api)
        response = create(api)
        assert response.status_code == 409
        assert response.json()["detail"] == "An iSCSI target named backup exists already"
        assert len(lio.files) == 1

    def test_a_pool_that_is_not_mounted_and_a_volume_that_is_not_there(self, api, lio):
        response = create(api, pool="media")
        assert response.status_code == 409
        assert "not mounted" in response.json()["detail"]
        response = create(api, volume="nothing")
        assert response.status_code == 404
        assert response.json()["detail"] == "The volume nothing is not in the pool data"
        assert lio.files == {} and lio.unos_paths() == [] and rows(lio) == []

    def test_a_lun_larger_than_the_free_space_is_refused(self, api, lio):
        lio.avail = 3 * GIB + 5
        response = create(api, size_gib=4)
        assert response.status_code == 409
        assert response.json()["detail"] == "The volume has 3 GiB free: a LUN of 4 GiB does not fit"
        assert lio.files == {} and rows(lio) == []

    def test_a_lun_over_the_volumes_quota_is_refused(self, api, lio, monkeypatch):
        quota = VolumeInfo(name="iscsi", pool="data", path=VOLUME, used=GIB, quota=4 * GIB, usage="counted")
        monkeypatch.setattr(pools, "list_volumes", lambda pool: [quota])
        response = create(api, size_gib=4)
        assert response.status_code == 409
        assert response.json()["detail"] == "The volume's quota leaves 3 GiB: a LUN of 4 GiB does not fit"
        assert lio.files == {}
        unread = quota.model_copy(update={"used": None, "usage": "unknown"})
        monkeypatch.setattr(pools, "list_volumes", lambda pool: [unread])
        response = create(api, size_gib=1)
        assert (response.status_code, response.json()["detail"]) == (409, iscsi.QUOTA_UNREAD)
        monkeypatch.setattr(pools, "list_volumes", lambda pool: [quota])
        assert created(api, size_gib=3)["target"]["served"] is True  # 1 GiB used: 3 GiB fit
        assert lio.files == {f"{FOLDER}/backup.lun": [0, 0o600, 3 * GIB]}

    def test_an_existing_lun_file_is_never_overwritten(self, api, lio):
        lio.folders[FOLDER] = [0, 0o700, True]
        lio.files[f"{FOLDER}/backup.lun"] = [0, 0o600, 7 * GIB]
        response = create(api)
        assert response.status_code == 409
        assert response.json()["detail"] == f"The volume holds a LUN file {FOLDER}/backup.lun already: choose another name"
        assert lio.files == {f"{FOLDER}/backup.lun": [0, 0o600, 7 * GIB]} and rows(lio) == []

    def test_a_folder_that_is_not_roots_is_refused(self, api, lio):
        lio.folders[FOLDER] = [1000, 0o777, False]
        response = create(api)
        assert response.status_code == 409
        assert response.json()["detail"] == iscsi.LUN_UNSAFE.format(folder=FOLDER)
        assert lio.files == {} and rows(lio) == []

    def test_a_create_that_the_kernel_refuses_is_taken_back_whole(self, api, lio, tmp_path):
        lio.modules = False
        response = create(api)
        assert response.status_code == 500
        assert "LIO's modules could not be loaded" in response.json()["detail"]
        assert rows(lio) == [] and lio.files == {} and FOLDER not in lio.folders
        assert stored_secrets(tmp_path) == {} and iscsi.pool_user("data") is None
        assert audit() == []

    def test_a_failed_operation_leaves_no_part_of_the_target(self, api, lio, tmp_path, monkeypatch):
        real = lio.apply

        def fail_at_the_portal(text):
            for line in text.splitlines():
                if "/np/" in line and line.startswith("mkdir"):
                    lio.fail_on = line.split("\t")[1]
            return real(text)

        monkeypatch.setattr(lio, "apply", fail_at_the_portal)
        response = create(api)
        assert response.status_code == 500
        assert response.json()["detail"].startswith(
            "The new target is not served: The kernel's iSCSI configuration could not be changed: failed: mkdir")
        assert lio.unos_paths() == [f"{CFS}/core/{iscsi.HBA}"]
        assert rows(lio) == [] and lio.files == {} and stored_secrets(tmp_path) == {}

    def test_every_route_answers_503_where_host_commands_run_locally(self, api, lio, monkeypatch):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")
        calls = len(lio.fake.calls)
        for response in (api.get(API), create(api), api.patch(f"{API}/targets/backup", json={}),
                         api.post(f"{API}/targets/backup/delete", json={"confirm": "backup"})):
            assert (response.status_code, response.json()["detail"]) == (503, iscsi.NOT_A_HOST)
        assert len(lio.fake.calls) == calls

    def test_a_kernel_without_lio(self, api, lio):
        lio.modules = False
        assert listed(api)["kernel_support"] is False


# ---------------------------------------------------------------------------
# Step 2: an initiator with the credentials connects; one without is refused
# ---------------------------------------------------------------------------


class TestLogin:
    def test_only_the_allowed_initiator_with_the_credentials_logs_in(self, api, lio):
        body = created(api)
        iqn, secret = body["target"]["iqn"], body["chap_secret"]
        assert lio.login(iqn, LINUX, "backup", secret) == "ok"
        assert lio.login(iqn, LINUX, "backup", secret, portal=PORTALS[1]) == "ok"
        assert lio.login(iqn, LINUX) == "refused"  # no CHAP
        assert lio.login(iqn, LINUX, "backup", "Wrong-secret-1") == "refused"
        assert lio.login(iqn, LINUX, "other", secret) == "refused"
        assert lio.login(iqn, OTHER, "backup", secret) == "refused"  # not allowed, though it has the secret
        assert lio.login(iqn, LINUX, "backup", secret, portal="172.17.0.1:3260") == "no target"

    def test_mutual_chap(self, api, lio):
        body = created(api, mutual=True)
        iqn, secret, mutual = body["target"]["iqn"], body["chap_secret"], body["mutual_secret"]
        g = f"{CFS}/iscsi/{iqn}/tpgt_1/acls/{LINUX}/auth"
        assert (lio.value(f"{g}/userid_mutual"), lio.value(f"{g}/password_mutual")) == ("backup", mutual)
        assert lio.login(iqn, LINUX, "backup", secret, mutual=mutual) == "ok"
        assert lio.login(iqn, LINUX, "backup", secret, mutual="Wrong-secret-1") == "refused"


# ---------------------------------------------------------------------------
# Step 3: several initiators
# ---------------------------------------------------------------------------


class TestInitiators:
    def test_several_initiators_each_log_in_with_the_credentials(self, api, lio):
        body = created(api, initiators=[LINUX, WINDOWS])
        iqn, secret = body["target"]["iqn"], body["chap_secret"]
        assert body["target"]["initiators"] == [LINUX, WINDOWS]
        for initiator in (LINUX, WINDOWS):
            assert lio.login(iqn, initiator, "backup", secret) == "ok"
        assert lio.login(iqn, OTHER, "backup", secret) == "refused"

    def test_a_change_of_the_initiators(self, api, lio):
        body = created(api, initiators=[LINUX, WINDOWS])
        iqn, secret = body["target"]["iqn"], body["chap_secret"]
        response = api.patch(f"{API}/targets/backup", json={"initiators": [WINDOWS, OTHER]})
        assert response.status_code == 200, response.text
        changed = response.json()
        assert changed["chap_secret"] is None and changed["target"]["initiators"] == [WINDOWS, OTHER]
        assert lio.login(iqn, OTHER, "backup", secret) == "ok"
        assert lio.login(iqn, WINDOWS, "backup", secret) == "ok"
        assert lio.login(iqn, LINUX, "backup", secret) == "refused"
        assert rows(lio) == [("backup", [WINDOWS, OTHER])]
        assert audit()[-1]["details"] == f"Changed the iSCSI target backup: initiators {WINDOWS}, {OTHER}"
        # The same list again changes nothing and writes no audit entry.
        before = (len(lio.applies), len(audit()))
        assert api.patch(f"{API}/targets/backup", json={"initiators": [WINDOWS, OTHER]}).status_code == 200
        assert (len(lio.applies), len(audit())) == before


class TestChange:
    def test_a_new_secret_ends_the_sessions_of_the_old_one(self, api, lio):
        """A leaked secret is replaced: an initiator logged in with it does not stay in."""
        iqn = created(api, initiators=[LINUX, WINDOWS])["target"]["iqn"]
        lio.session(iqn, LINUX)
        lio.session(iqn, WINDOWS)
        assert api.patch(f"{API}/targets/backup", json={"new_secret": True}).status_code == 200
        assert listed(api)["targets"][0]["sessions"] == []

    def test_a_change_of_the_initiators_only_keeps_the_others_sessions(self, api, lio):
        iqn = created(api, initiators=[LINUX, WINDOWS])["target"]["iqn"]
        lio.session(iqn, LINUX)
        lio.session(iqn, WINDOWS)
        assert api.patch(f"{API}/targets/backup", json={"initiators": [LINUX, OTHER]}).status_code == 200
        assert listed(api)["targets"][0]["sessions"] == [LINUX]

    def test_a_new_secret_that_the_kernel_does_not_take_is_answered_all_the_same(self, api, lio, tmp_path):
        iqn = created(api)["target"]["iqn"]
        lio.fail_on = f"{CFS}/iscsi/{iqn}/tpgt_1/acls/{LINUX}/auth/password"
        response = api.patch(f"{API}/targets/backup", json={"new_secret": True})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["chap_secret"] and list(stored_secrets(tmp_path).values())[0]["chap"] == body["chap_secret"]
        assert body["target"]["served"] is False
        assert body["target"]["problem"].endswith(f"failed: write {lio.fail_on}")
        assert body["chap_secret"] not in body["target"]["problem"]
        # Nothing half made stays: no ACL without its CHAP values.
        assert lio.unos_paths() == [f"{CFS}/core/{iscsi.HBA}"]
        # The next apply serves it with the new secret.
        lio.fail_on = None
        restore()
        assert lio.login(iqn, LINUX, "backup", body["chap_secret"]) == "ok"

    def test_a_new_secret_replaces_the_old_one_and_is_shown_once(self, api, lio, tmp_path):
        body = created(api)
        iqn, old = body["target"]["iqn"], body["chap_secret"]
        response = api.patch(f"{API}/targets/backup", json={"new_secret": True})
        assert response.status_code == 200, response.text
        new = response.json()["chap_secret"]
        assert new and new != old
        assert lio.login(iqn, LINUX, "backup", new) == "ok"
        assert lio.login(iqn, LINUX, "backup", old) == "refused"
        assert list(stored_secrets(tmp_path).values()) == [{"chap": new, "mutual": None}]
        assert new not in json.dumps(listed(api)) and new not in json.dumps(audit())
        assert audit()[-1]["details"] == "Changed the iSCSI target backup: a new CHAP secret"

    def test_mutual_chap_on_and_off(self, api, lio, tmp_path):
        body = created(api)
        iqn, secret = body["target"]["iqn"], body["chap_secret"]
        response = api.patch(f"{API}/targets/backup", json={"mutual": True, "mutual_secret": "Mutual-secret-1"})
        assert response.status_code == 200, response.text
        assert response.json()["mutual_secret"] == "Mutual-secret-1" and response.json()["target"]["mutual"] is True
        assert lio.login(iqn, LINUX, "backup", secret, mutual="Mutual-secret-1") == "ok"
        response = api.patch(f"{API}/targets/backup", json={"mutual": False})
        assert response.json()["target"]["mutual"] is False
        auth = f"{CFS}/iscsi/{iqn}/tpgt_1/acls/{LINUX}/auth"
        assert (lio.value(f"{auth}/userid_mutual"), lio.value(f"{auth}/password_mutual")) == ("", "")
        assert lio.login(iqn, LINUX, "backup", secret) == "ok"
        assert list(stored_secrets(tmp_path).values()) == [{"chap": secret, "mutual": None}]
        assert [e["details"] for e in audit()[1:]] == [
            "Changed the iSCSI target backup: mutual CHAP on", "Changed the iSCSI target backup: mutual CHAP off"]
        response = api.patch(f"{API}/targets/backup", json={"mutual": True, "mutual_secret": secret})
        assert (response.status_code, response.json()["detail"]) == (400, iscsi.SAME_SECRETS)

    def test_an_unknown_target(self, api, lio):
        response = api.patch(f"{API}/targets/nothing", json={"new_secret": True})
        assert (response.status_code, response.json()["detail"]) == (404, "No iSCSI target is named nothing")


# ---------------------------------------------------------------------------
# Delete: a typed confirmation, the sessions end, the LUN goes
# ---------------------------------------------------------------------------


class TestDelete:
    def test_a_delete_needs_the_name_typed_again(self, api, lio):
        created(api)
        for confirm in ("", "Backup", "back"):
            response = api.post(f"{API}/targets/backup/delete", json={"confirm": confirm})
            assert (response.status_code, response.json()["detail"]) == (400, iscsi.CONFIRM_NAME)
        assert len(lio.files) == 1 and rows(lio) == [("backup", [LINUX])]
        assert listed(api)["targets"][0]["served"] is True

    def test_a_lun_in_use_is_deleted_with_its_target_and_its_sessions_end(self, api, lio, tmp_path):
        body = created(api)
        iqn = body["target"]["iqn"]
        lio.session(iqn, LINUX)
        assert listed(api)["targets"][0]["sessions"] == [LINUX]
        response = api.post(f"{API}/targets/backup/delete", json={"confirm": "backup"})
        assert response.status_code == 200, response.text
        assert response.json() == {"name": "backup", "sessions": [LINUX]}
        assert lio.unos_paths() == [f"{CFS}/core/{iscsi.HBA}"]
        assert lio.files == {} and lio.folders == {}
        assert rows(lio) == [] and stored_secrets(tmp_path) == {}
        assert iscsi.pool_user("data") is None
        entry = audit()[-1]
        assert entry["action"] == "iscsi_target_deleted" and entry["severity"] == "warning"
        assert entry["details"].endswith(f"with the data of its LUN; it ended the sessions of {LINUX}")
        # Another target in the same volume keeps the folder.

    def test_the_folder_stays_for_the_other_luns(self, api, lio):
        created(api, "one")
        created(api, "two")
        assert api.post(f"{API}/targets/one/delete", json={"confirm": "one"}).status_code == 200
        assert lio.files == {f"{FOLDER}/two.lun": [0, 0o600, 2 * GIB]} and lio.folders[FOLDER][2] is True
        assert [t["name"] for t in listed(api)["targets"]] == ["two"]
        assert listed(api)["targets"][0]["served"] is True

    def test_a_target_whose_pool_is_not_mounted_is_not_deleted(self, api, lio, monkeypatch):
        created(api)

        def unmounted(pool):
            raise pools.PoolConflictError("Pool 'data' is not mounted at /mnt/pools/data.")

        monkeypatch.setattr(pools, "list_volumes", unmounted)
        response = api.post(f"{API}/targets/backup/delete", json={"confirm": "backup"})
        assert (response.status_code, response.json()["detail"]) == (409, iscsi.DELETE_UNMOUNTED.format(pool="data"))
        assert len(lio.files) == 1 and rows(lio) == [("backup", [LINUX])]


# ---------------------------------------------------------------------------
# The pool and the volume of a LUN
# ---------------------------------------------------------------------------


class TestPoolAndVolume:
    @pytest.mark.parametrize(("method", "url"), [("post", "/api/v1/storage/pools/data/unmount"),
                                                 ("delete", "/api/v1/storage/pools/data")])
    def test_the_pool_is_neither_unmounted_nor_forgotten(self, api, lio, method, url):
        created(api)
        response = getattr(api, method)(url)
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Pool 'data' holds the LUN of the iSCSI target backup: delete the target on the Shares page first.")
        assert listed(api)["targets"][0]["served"] is True

    def test_the_volume_is_not_deleted(self, api, lio):
        created(api)
        response = api.delete("/api/v1/storage/pools/data/volumes/iscsi")
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Volume 'iscsi' holds the LUN of the iSCSI target backup: delete the target on the Shares page first.")

    def test_the_index_comes_from_the_database_at_the_start(self, api, lio, monkeypatch):
        created(api)
        monkeypatch.setattr(iscsi, "_index", {})
        assert iscsi.pool_user("data") is None
        asyncio.run(iscsi.load_index())
        assert iscsi.pool_user("data") == "backup"
        assert iscsi.volume_user(VOLUME) == "backup"
        assert iscsi.volume_user("/mnt/pools/data/other") is None


# ---------------------------------------------------------------------------
# The restore at the start: idempotent, and what UNOS no longer knows goes
# ---------------------------------------------------------------------------


class TestRestore:
    def test_after_a_reboot_every_target_is_served_again_as_it_was(self, api, lio, caplog):
        body = created(api, initiators=[LINUX, WINDOWS], mutual=True)
        before = {p: (n.kind, n.value) for p, n in lio.nodes.items()}
        lio.reboot()
        assert listed(api)["targets"][0]["served"] is False
        caplog.set_level(logging.INFO, logger="src.shares.iscsi")
        restore()
        assert {p: (n.kind, n.value) for p, n in lio.nodes.items()} == before
        assert lio.login(body["target"]["iqn"], WINDOWS, "backup", body["chap_secret"],
                         mutual=body["mutual_secret"]) == "ok"
        assert "Applied the iSCSI targets: 1 served (backup)" in caplog.text
        assert body["chap_secret"] not in caplog.text

    def test_a_restore_that_finds_configfs_as_it_should_be_changes_and_logs_nothing(self, api, lio, caplog):
        created(api)
        applies = len(lio.applies)
        caplog.set_level(logging.DEBUG, logger="src.shares.iscsi")
        caplog.clear()
        restore()
        assert len(lio.applies) == applies, lio.applies[applies:]
        assert [r for r in caplog.records if r.name == "src.shares.iscsi"] == []

    def test_nothing_at_all_without_targets(self, api, lio):
        restore()
        assert lio.applies == [] and lio.nodes == {}
        assert [cmd for cmd in lio.fake.calls if cmd[:1] == ["modprobe"] or iscsi.MODULES_SCRIPT in cmd] == []

    def test_targets_and_objects_that_unos_no_longer_knows_are_removed_and_others_stay(self, api, lio):
        created(api)
        lio.load()
        stale = f"{iscsi.IQN_PREFIX}old.0123abcd"
        lio.apply("".join(f"{op}\t{path}\t{value}\n" for op, path, value in [
            ("mkdir", f"{CFS}/core/{iscsi.HBA}/{'0' * 32}", ""),
            ("mkdir", f"{CFS}/iscsi/{stale}", ""), ("mkdir", f"{CFS}/iscsi/{stale}/tpgt_1", ""),
            ("mkdir", f"{CFS}/iscsi/{stale}/tpgt_1/np/{PORTALS[0]}", ""),
            ("mkdir", f"{CFS}/iscsi/{stale}/tpgt_1/acls/{OTHER}", ""),
            ("write", f"{CFS}/iscsi/{stale}/tpgt_1/enable", "1"),
            # A target made by hand, of another name, in another HBA.
            ("mkdir", f"{CFS}/core/fileio_0", ""), ("mkdir", f"{CFS}/core/fileio_0/disk", ""),
            ("mkdir", f"{CFS}/iscsi/iqn.2003-01.org.linux-iscsi.host:sn.1", ""),
        ]))
        restore()
        assert not [p for p in lio.nodes if stale in p or "0" * 32 in p]
        assert f"{CFS}/core/fileio_0/disk" in lio.nodes
        assert f"{CFS}/iscsi/iqn.2003-01.org.linux-iscsi.host:sn.1" in lio.nodes
        assert listed(api)["targets"][0]["served"] is True

    def test_a_target_without_its_secret_is_not_served(self, api, lio, tmp_path):
        body = created(api)
        (tmp_path / "iscsi_secrets.json").write_text("{}")
        lio.reboot()
        restore()
        assert lio.unos_paths() == []
        [target] = listed(api)["targets"]
        assert (target["served"], target["problem"]) == (False, iscsi.NO_SECRET)
        # A change without a new secret cannot serve it; one with a new secret does.
        response = api.patch(f"{API}/targets/backup", json={"initiators": [OTHER]})
        assert (response.status_code, response.json()["detail"]) == (409, iscsi.SECRET_NEEDED)
        response = api.patch(f"{API}/targets/backup", json={"new_secret": True})
        assert response.json()["target"]["served"] is True
        assert lio.login(body["target"]["iqn"], LINUX, "backup", response.json()["chap_secret"]) == "ok"

    def test_a_secrets_file_that_cannot_be_read_is_kept_aside(self, api, lio, tmp_path):
        created(api)
        (tmp_path / "iscsi_secrets.json").write_text("[not json")
        restore()
        assert (tmp_path / "iscsi_secrets.json.bad").read_text() == "[not json"
        assert listed(api)["targets"][0]["problem"] == iscsi.NO_SECRET
        assert lio.unos_paths() == [f"{CFS}/core/{iscsi.HBA}"]

    @pytest.mark.parametrize(
        ("damage", "problem"),
        [
            (lambda lio: lio.files.clear(), iscsi.LUN_MISSING.format(path=f"{FOLDER}/backup.lun")),
            (lambda lio: lio.files[f"{FOLDER}/backup.lun"].__setitem__(2, GIB), iscsi.LUN_WRONG.format(
                path=f"{FOLDER}/backup.lun", size=2)),
            (lambda lio: lio.files[f"{FOLDER}/backup.lun"].__setitem__(0, 1000), iscsi.LUN_WRONG.format(
                path=f"{FOLDER}/backup.lun", size=2)),
            (lambda lio: lio.folders[FOLDER].__setitem__(1, 0o777), iscsi.LUN_UNSAFE.format(folder=FOLDER)),
        ],
    )
    def test_a_lun_file_that_is_not_as_unos_made_it_is_not_served(self, api, lio, damage, problem):
        created(api)
        damage(lio)
        lio.reboot()
        restore()
        assert lio.unos_paths() == [] and lio.lio_created == []
        [target] = listed(api)["targets"]
        assert (target["served"], target["problem"]) == (False, problem)

    def test_a_pool_that_did_not_mount_leaves_its_targets_unserved(self, api, lio, monkeypatch):
        created(api)

        def unmounted(pool):
            raise pools.PoolConflictError("not mounted")

        monkeypatch.setattr(pools, "list_volumes", unmounted)
        lio.reboot()
        restore()
        assert lio.unos_paths() == []
        assert listed(api)["targets"][0]["problem"] == "The pool data is not mounted"

    def test_chap_turned_off_by_hand_is_turned_on_again(self, api, lio):
        iqn = created(api)["target"]["iqn"]
        g = f"{CFS}/iscsi/{iqn}/tpgt_1"
        lio.nodes[f"{g}/attrib/authentication"].value = "0"
        lio.nodes[f"{g}/attrib/generate_node_acls"].value = "1"
        lio.nodes[f"{g}/param/AuthMethod"].value = "CHAP,None"
        assert lio.login(iqn, OTHER) == "no LUN"  # the kernel would let anyone in now
        restore()
        assert lio.value(f"{g}/attrib/authentication") == "1"
        assert lio.value(f"{g}/param/AuthMethod") == "CHAP"
        assert lio.login(iqn, OTHER) == "refused"

    def test_a_storage_object_of_another_file_is_made_anew(self, api, lio):
        iqn = created(api)["target"]["iqn"]
        [obj] = lio.children(f"{CFS}/core/{iscsi.HBA}")
        lio.nodes[f"{obj}/udev_path"].value = "/somewhere/else"
        restore()
        assert lio.value(f"{obj}/udev_path") == f"{FOLDER}/backup.lun"
        assert lio.nodes[f"{CFS}/iscsi/{iqn}/tpgt_1/lun/lun_0/unos"].value == obj

    def test_a_target_that_the_kernel_refuses_leaves_the_others_served(self, api, lio):
        created(api, "one")
        created(api, "two")
        lio.reboot()
        real = lio.apply

        def refuse_two(text):
            for line in text.splitlines():
                if line.startswith("mkdir") and ":two." in line and "/np/" in line:
                    lio.fail_on = line.split("\t")[1]
            return real(text)

        lio.apply = refuse_two
        restore()
        targets = {t["name"]: t for t in listed(api)["targets"]}
        assert targets["one"]["served"] is True
        assert targets["two"]["served"] is False and "failed: mkdir" in targets["two"]["problem"]
        assert not [p for p in lio.nodes if ":two." in p]

    def test_a_volume_that_cannot_be_checked_now_leaves_its_target_as_it_is(self, api, lio, monkeypatch):
        """btrfs that fails or does not answer is no reason to end the sessions of a served target."""
        iqn = created(api)["target"]["iqn"]
        lio.session(iqn, LINUX)
        before = {p: (n.kind, n.value) for p, n in lio.nodes.items()}

        def fails(pool):
            raise RuntimeError("btrfs did not answer")

        monkeypatch.setattr(pools, "list_volumes", fails)
        restore()
        assert {p: (n.kind, n.value) for p, n in lio.nodes.items()} == before
        [target] = listed(api)["targets"]
        assert target["served"] is True and target["sessions"] == [LINUX]

    def test_a_pool_mounted_later_serves_its_targets(self, api, lio, monkeypatch):
        created(api)

        def unmounted(pool):
            raise pools.PoolConflictError("not mounted")

        real = pools.list_volumes
        monkeypatch.setattr(pools, "list_volumes", unmounted)
        lio.reboot()
        restore()
        assert listed(api)["targets"][0]["served"] is False
        monkeypatch.setattr(pools, "list_volumes", real)
        assert api.post("/api/v1/storage/pools/data/mount").status_code == 200
        assert listed(api)["targets"][0]["served"] is True

    def test_the_follow_loop_does_nothing_while_nothing_is_served(self, api, lio, monkeypatch, tmp_path):
        created(api)
        (tmp_path / "iscsi_secrets.json").write_text("{}")
        restore()
        monkeypatch.setattr(iscsi, "FOLLOW_INTERVAL", 0)
        calls = len(lio.fake.calls)

        async def passes():
            task = asyncio.create_task(iscsi._follow())
            await asyncio.sleep(0.2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(passes())
        assert lio.fake.calls[calls:] == []

    def test_tentative_and_failed_addresses_get_no_portal(self, api, lio):
        lio.interfaces[1]["addr_info"][1]["tentative"] = True
        lio.interfaces[1]["addr_info"].append({"family": "inet6", "local": "fd00::11", "prefixlen": 64,
                                               "dadfailed": True, "tentative": True})
        assert listed(api)["portals"] == ["192.168.1.10:3260"]

    def test_the_portals_follow_the_hosts_addresses(self, api, lio):
        iqn = created(api)["target"]["iqn"]
        lio.interfaces[1]["addr_info"][0]["local"] = "192.168.1.20"
        assert asyncio.run(asyncio.to_thread(iscsi.lan_portals)) == ["192.168.1.20:3260", "[fd00::10]:3260"]
        restore()
        g = f"{CFS}/iscsi/{iqn}/tpgt_1/np"
        assert lio.children(g) == [f"{g}/192.168.1.20:3260", f"{g}/[fd00::10]:3260"]

    def test_the_follow_loop_applies_only_when_the_addresses_changed(self, api, lio, monkeypatch, caplog):
        created(api)
        monkeypatch.setattr(iscsi, "FOLLOW_INTERVAL", 0)
        caplog.set_level(logging.DEBUG, logger="src.shares.iscsi")
        caplog.clear()

        async def passes(n):
            task = asyncio.create_task(iscsi._follow())
            for _ in range(n):
                await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        applies = len(lio.applies)
        asyncio.run(passes(5))
        assert len(lio.applies) == applies
        assert [r for r in caplog.records if r.name == "src.shares.iscsi"] == []
        lio.interfaces[1]["addr_info"][0]["local"] = "192.168.1.20"
        asyncio.run(passes(5))
        assert len(lio.applies) == applies + 1
        assert "on 192.168.1.20:3260, [fd00::10]:3260" in caplog.text

    def test_the_restore_after_a_database_restore(self, api, lio):
        """The backup's restore applies the restored database's targets (backup/router.py)."""
        import inspect

        backup_router = importlib.import_module("src.backup.router")

        assert "iscsi.restore_targets(after_restore=True)" in inspect.getsource(backup_router._after_database_restore)

    def test_after_a_database_restore_nothing_runs_without_targets_before_or_after(self, api, lio):
        asyncio.run(iscsi.restore_targets(after_restore=True))
        assert not [c for c in lio.fake.calls if "unos-iscsi-read" in c]

    def test_after_a_database_restore_without_the_target_it_is_removed(self, api, lio):
        created(api)
        engine = create_engine(f"sqlite:///{lio.db_path}")
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM iscsi_initiators")
            connection.exec_driver_sql("DELETE FROM iscsi_targets")
        engine.dispose()
        asyncio.run(iscsi.restore_targets(after_restore=True))
        assert lio.unos_paths() == [f"{CFS}/core/{iscsi.HBA}"]
        assert iscsi.pool_user("data") is None


class TestPlan:
    def test_an_apply_of_the_state_it_made_has_no_operations(self, api, lio, tmp_path):
        created(api, initiators=[LINUX, WINDOWS], mutual=True)
        rows_ = asyncio.run(_rows())
        secrets = iscsi.load_secrets()
        state = iscsi.parse_state(lio.read())
        serve = [iscsi.Serve(r.name, r.uid, r.iqn, iscsi.lun_path(r.pool, r.volume, r.name), r.size_gib * GIB,
                             r.chap_user, r.initiators, secrets[r.uid]) for r in rows_]
        assert iscsi.plan(state, serve, PORTALS) == []
        assert iscsi.plan(state, serve, None) == []

    def test_the_operations_name_no_value_on_failure(self, api, lio, monkeypatch):
        with pytest.raises(iscsi.IscsiError) as raised:
            lio.fail_on = f"{CFS}/x"
            iscsi.run_ops([("write", f"{CFS}/x", "Secret-value-1")])
        assert "Secret-value-1" not in str(raised.value)
        with pytest.raises(iscsi.IscsiError):
            iscsi.run_ops([("write", f"{CFS}/x", "a\tb")])


async def _rows():
    async with database.async_session() as db:
        return await iscsi._rows(db)


def test_the_web_ui_repeats_the_servers_texts():
    """webui/tests/iscsi.spec.ts answers the tab's requests itself: with the server's texts (ADR-0189)."""
    from pathlib import Path

    spec = (Path(__file__).resolve().parents[2] / "webui" / "tests" / "iscsi-fakes.ts").read_text(encoding="utf-8")
    for text in (iscsi.CONFIRM_NAME, iscsi.NO_SECRET, iscsi.SECRET_RULE, iscsi.NAME_RULE):
        assert text in spec, text
