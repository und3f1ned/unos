"""The host scripts of src/shares/iscsi.py (ADR-0417), run with /bin/sh and with dash, the /bin/sh of
Debian and Ubuntu hosts (where it is installed, as on macOS).

configfs is a temporary folder here: the scripts that read and change it use only the shell, cat,
grep, mkdir, rmdir, ln and rm, which act on it as on any folder. ``stat``, ``lsattr``, ``chattr``,
``truncate``, ``modprobe``, ``mountpoint`` and ``mount`` are stubs on PATH: stat answers as GNU stat
does, with the test's own user as root (uid 0); the immutable flag is a list of paths in the stub's
state, which lsattr reads and chattr changes; the others log their arguments. test_iscsi.py runs the
API against a fake host that stands for these scripts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.shares import iscsi

STUB = r'''#!{python}
import os, sys

tool, args = os.path.basename(sys.argv[0]), sys.argv[1:]
state = os.environ["STUB_STATE"]
flags = os.path.join(state, "immutable")


def immutable():
    return set(open(flags).read().split("\n")) - {""} if os.path.exists(flags) else set()


with open(os.path.join(state, "log"), "a") as out:
    out.write(" ".join([tool, *args]) + "\n")
if tool == "stat":
    assert args[0] == "-c" and args[2] == "--", args
    st = os.lstat(args[3])
    uid = 0 if st.st_uid == os.getuid() else st.st_uid
    print(args[1].replace("%u", str(uid)).replace("%a", format(st.st_mode & 0o7777, "o"))
          .replace("%s", str(st.st_size)))
elif tool == "lsattr":
    assert args[:2] == ["-d", "--"], args
    print(("----i---------" if os.path.abspath(args[2]) in immutable() else "--------------") + " " + args[2])
elif tool == "chattr":
    assert args[1] == "--", args
    found = immutable()
    path = os.path.abspath(args[2])
    found = found | {path} if args[0] == "+i" else found - {path}
    with open(flags, "w") as out:
        out.write("\n".join(sorted(found)) + "\n")
elif tool == "truncate":
    assert args[0] == "-s" and args[2] == "--", args
    os.truncate(args[3], int(args[1]))
elif tool == "mountpoint":
    sys.exit(0 if os.environ.get("STUB_MOUNTED") else 1)
elif tool == "mount":
    os.makedirs(os.path.join(args[-1], "target"))
elif tool == "modprobe":
    sys.exit(1 if os.environ.get("STUB_NO_MODULES") else 0)
elif tool == "mkdir":
    # Linux's mkdir in a setgid folder (an SMB share's): the new folder inherits the setgid bit,
    # which mkdir -m 700 does not clear.
    assert args[:3] == ["-m", "700", "--"], args
    os.mkdir(args[3], 0o700)
    os.chmod(args[3], 0o2700)
'''

SHELLS = ["/bin/sh", "dash"]
SECRET = "Secret%s-val\\ue1"


@pytest.fixture(params=SHELLS)
def sh(request, tmp_path):
    shell = request.param
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed: Debian and Ubuntu hosts run the scripts with it")
    bin_dir, stub_state = tmp_path / "bin", tmp_path / "stub"
    bin_dir.mkdir()
    stub_state.mkdir()
    stub = bin_dir / "stub"
    stub.write_text(STUB.replace("{python}", sys.executable))
    stub.chmod(0o755)
    for tool in ("stat", "lsattr", "chattr", "truncate", "modprobe", "mountpoint", "mount"):
        (bin_dir / tool).symlink_to(stub)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_STATE": str(stub_state), "LC_ALL": "C"}

    def run(script, name, *args, input=None, **extra):
        return subprocess.run([shell, "-c", script, name, *map(str, args)], input=input,
                              env={**env, **extra}, capture_output=True, text=True, timeout=30)

    def immutable():
        found = stub_state / "immutable"
        return set(found.read_text().split("\n")) - {""} if found.exists() else set()

    def log():
        found = stub_state / "log"
        return found.read_text().splitlines() if found.exists() else []

    return SimpleNamespace(run=run, immutable=immutable, log=log, bin=bin_dir)


# ---------------------------------------------------------------------------
# APPLY_SCRIPT: the operations from standard input, below the root only
# ---------------------------------------------------------------------------


def ops(*lines: tuple[str, str, str]) -> str:
    return "".join(f"{op}\t{path}\t{value}\n" for op, path, value in lines)


class TestApply:
    def test_the_operations_in_their_order(self, sh, tmp_path):
        root = tmp_path / "target"
        root.mkdir()
        result = sh.run(iscsi.APPLY_SCRIPT, "unos-iscsi-apply", root, input=ops(
            ("mkdir", f"{root}/a", ""),
            ("mkdir", f"{root}/a/b", ""),
            ("write", f"{root}/a/password", SECRET),
            ("write", f"{root}/a/control", "fd_dev_name=/mnt/pools/data/v/.unos-iscsi/x.lun,fd_dev_size=1073741824"),
            ("link", f"{root}/a/l", f"{root}/a/b"),
            ("unlink", f"{root}/a/l", ""),
            ("rmdir", f"{root}/a/b", ""),
        ))
        assert result.returncode == 0, result.stderr
        # The value as it is: no line break added, no escape read.
        assert (root / "a" / "password").read_bytes() == SECRET.encode()
        assert (root / "a" / "control").read_text() == (
            "fd_dev_name=/mnt/pools/data/v/.unos-iscsi/x.lun,fd_dev_size=1073741824")
        assert sorted(p.name for p in (root / "a").iterdir()) == ["control", "password"]

    def test_a_link_points_to_its_value(self, sh, tmp_path):
        root = tmp_path / "target"
        (root / "lun").mkdir(parents=True)
        result = sh.run(iscsi.APPLY_SCRIPT, "unos-iscsi-apply", root, input=ops(
            ("link", f"{root}/lun/unos", f"{root}/core/fileio_3260/x")))
        assert result.returncode == 0, result.stderr
        assert os.readlink(root / "lun" / "unos") == f"{root}/core/fileio_3260/x"

    @pytest.mark.parametrize(
        "line",
        [
            ("mkdir", "/etc/x", ""),
            ("write", "{root}/../escape", "x"),
            ("write", "{root}/a/..", "x"),
            ("link", "{root}/l", "/etc/passwd"),
            ("chmod", "{root}/a", "777"),
            ("mkdir", "{root}2/x", ""),
        ],
    )
    def test_a_path_outside_the_root_or_an_unknown_operation_ends_it_before_it_runs(self, sh, tmp_path, line):
        root = tmp_path / "target"
        root.mkdir()
        op, path, value = line
        result = sh.run(iscsi.APPLY_SCRIPT, "unos-iscsi-apply", root, input=ops(
            (op, path.format(root=root), value), ("mkdir", f"{root}/after", "")))
        assert result.returncode == 2
        assert result.stderr.startswith("refused: ")
        assert not (root / "after").exists() and not (tmp_path / "escape").exists()
        assert not (tmp_path / "target2").exists()

    def test_the_first_failure_ends_it_and_its_message_has_no_value(self, sh, tmp_path):
        root = tmp_path / "target"
        root.mkdir()
        result = sh.run(iscsi.APPLY_SCRIPT, "unos-iscsi-apply", root, input=ops(
            ("mkdir", f"{root}/a", ""),
            ("write", f"{root}/missing/password", SECRET),
            ("mkdir", f"{root}/after", "")))
        assert result.returncode == 1
        assert f"failed: write {root}/missing/password" in result.stderr
        assert SECRET not in result.stderr and "Secret" not in result.stderr
        assert (root / "a").is_dir() and not (root / "after").exists()

    def test_nothing_to_do(self, sh, tmp_path):
        assert sh.run(iscsi.APPLY_SCRIPT, "unos-iscsi-apply", tmp_path, input="").returncode == 0


# ---------------------------------------------------------------------------
# READ_SCRIPT: UNOS's part of configfs, and the sessions
# ---------------------------------------------------------------------------


def make_configfs(base: Path) -> tuple[str, str]:
    """A configfs with one target of UNOS, one made by hand and a storage object of UNOS."""
    t = base / "target"
    iqn = f"{iscsi.IQN_PREFIX}backup.0123abcd"
    uid = "0123abcd" + "0" * 24
    obj = t / "core" / iscsi.HBA / uid
    (obj / "attrib").mkdir(parents=True)
    (obj / "enable").write_text("1\n")
    (obj / "udev_path").write_text("/mnt/pools/data/iscsi/.unos-iscsi/backup.lun\n")
    (t / "core" / iscsi.HBA / "hba_info").write_text("HBA Index: 3260 plugin: fileio version: v5.0\n")
    g = t / "iscsi" / iqn / "tpgt_1"
    for folder in ("attrib", "param", "lun/lun_0", "np/192.168.1.10:3260", "np/[fd00::10]:3260",
                   "acls/iqn.1993-08.org.debian:01:5f3c0a1b2c3d/lun_0",
                   "acls/iqn.1991-05.com.microsoft:pc/lun_0", "acls/iqn.1991-05.com.microsoft:pc/auth"):
        (g / folder).mkdir(parents=True, exist_ok=True)
    (g / "enable").write_text("1\n")
    (g / "attrib" / "authentication").write_text("1\n")
    (g / "attrib" / "generate_node_acls").write_text("0\n")
    (g / "lun" / "lun_0" / "unos").symlink_to(obj)
    (g / "acls/iqn.1993-08.org.debian:01:5f3c0a1b2c3d/lun_0/unos").symlink_to(g / "lun" / "lun_0")
    (g / "acls/iqn.1993-08.org.debian:01:5f3c0a1b2c3d/info").write_text(
        "InitiatorName: iqn.1993-08.org.debian:01:5f3c0a1b2c3d\nSession State: TARG_SESS_STATE_LOGGED_IN\n")
    (g / "acls/iqn.1991-05.com.microsoft:pc/info").write_text(
        "No active iSCSI Session for Initiator Endpoint: iqn.1991-05.com.microsoft:pc\n")
    (t / "iscsi" / "iqn.2003-01.org.linux-iscsi.host:sn.1" / "tpgt_1").mkdir(parents=True)
    (t / "core" / "fileio_0" / "disk").mkdir(parents=True)
    return iqn, uid


class TestRead:
    def test_it_prints_unos_targets_objects_and_sessions(self, sh, tmp_path):
        iqn, uid = make_configfs(tmp_path)
        result = sh.run(iscsi.READ_SCRIPT, "unos-iscsi-read", tmp_path, iscsi.IQN_PREFIX, iscsi.HBA)
        assert result.returncode == 0, result.stderr
        debian, windows = "iqn.1993-08.org.debian:01:5f3c0a1b2c3d", "iqn.1991-05.com.microsoft:pc"
        assert result.stdout.splitlines() == [
            "loaded", "hba",
            f"object {uid} 1 /mnt/pools/data/iscsi/.unos-iscsi/backup.lun",
            f"target {iqn}",
            f"tpg {iqn} tpgt_1 1 1 0",
            f"lun {iqn} tpgt_1 lun_0",
            f"lunlink {iqn} tpgt_1 lun_0 unos",
            f"portal {iqn} tpgt_1 192.168.1.10:3260",
            f"portal {iqn} tpgt_1 [fd00::10]:3260",
            f"acl {iqn} tpgt_1 {windows}",
            f"mapped {iqn} tpgt_1 {windows} lun_0",
            f"acl {iqn} tpgt_1 {debian}",
            f"mapped {iqn} tpgt_1 {debian} lun_0",
            f"mappedlink {iqn} tpgt_1 {debian} lun_0 unos",
            f"session {iqn} tpgt_1 {debian}",
        ]
        state = iscsi.parse_state(result.stdout)
        tpg = state.targets[iqn].tpgs["tpgt_1"]
        assert (state.loaded, state.hba, tpg.enabled, tpg.chap) == (True, True, True, True)
        assert state.objects == {uid: iscsi.ObjectState(True, "/mnt/pools/data/iscsi/.unos-iscsi/backup.lun")}
        assert tpg.portals == {"192.168.1.10:3260", "[fd00::10]:3260"}
        assert {name: (acl.mapped, acl.session) for name, acl in tpg.acls.items()} == {
            debian: ({"lun_0": ["unos"]}, True), windows: ({"lun_0": []}, False)}

    def test_nothing_without_configfs(self, sh, tmp_path):
        result = sh.run(iscsi.READ_SCRIPT, "unos-iscsi-read", tmp_path / "none", iscsi.IQN_PREFIX, iscsi.HBA)
        assert (result.returncode, result.stdout) == (0, "")


# ---------------------------------------------------------------------------
# The LUN files: made new, checked, kept immutable, deleted
# ---------------------------------------------------------------------------


class TestLunFiles:
    @pytest.fixture
    def volume(self, tmp_path):
        folder = tmp_path / "pools" / "data" / "iscsi"
        folder.mkdir(parents=True)
        return folder

    def create(self, sh, volume, name="backup.lun", size=4096):
        return sh.run(iscsi.CREATE_SCRIPT, "unos-iscsi-create", volume, iscsi.LUN_FOLDER, name, size)

    def test_a_new_sparse_file_of_mode_0600_in_a_folder_of_mode_0700_kept_immutable(self, sh, volume):
        result = self.create(sh, volume)
        assert result.returncode == 0, result.stderr
        folder, file = volume / iscsi.LUN_FOLDER, volume / iscsi.LUN_FOLDER / "backup.lun"
        assert oct(folder.stat().st_mode & 0o777) == "0o700"
        assert (oct(file.stat().st_mode & 0o777), file.stat().st_size) == ("0o600", 4096)
        assert sh.immutable() == {str(folder)}

    def test_a_folder_made_in_a_setgid_folder_loses_the_setgid_bit(self, sh, volume):
        """An SMB share's folders are setgid (2770): the new folder would inherit the bit."""
        (sh.bin / "mkdir").symlink_to(sh.bin / "stub")
        result = self.create(sh, volume)
        assert result.returncode == 0, result.stderr
        assert oct((volume / iscsi.LUN_FOLDER).stat().st_mode & 0o7777) == "0o700"
        assert self.create(sh, volume, "other.lun").returncode == 0

    def test_an_existing_file_is_never_overwritten(self, sh, volume):
        self.create(sh, volume, size=4096)
        (volume / iscsi.LUN_FOLDER / "backup.lun").write_bytes(b"data")
        result = self.create(sh, volume, size=8192)
        assert result.returncode == 4
        assert (volume / iscsi.LUN_FOLDER / "backup.lun").read_bytes() == b"data"
        assert sh.immutable() == {str(volume / iscsi.LUN_FOLDER)}
        assert self.create(sh, volume, "other.lun").returncode == 0
        assert sh.immutable() == {str(volume / iscsi.LUN_FOLDER)}

    def test_a_folder_that_is_a_link_or_not_roots_with_mode_700_is_refused(self, sh, volume, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        (volume / iscsi.LUN_FOLDER).symlink_to(elsewhere)
        assert self.create(sh, volume).returncode == 3
        assert list(elsewhere.iterdir()) == []
        (volume / iscsi.LUN_FOLDER).unlink()
        (volume / iscsi.LUN_FOLDER).mkdir()
        (volume / iscsi.LUN_FOLDER).chmod(0o755)
        assert self.create(sh, volume).returncode == 3
        assert list((volume / iscsi.LUN_FOLDER).iterdir()) == []

    def test_prepare_reports_each_file_and_keeps_its_folder_immutable(self, sh, volume, tmp_path):
        self.create(sh, volume, "a.lun", 4096)
        self.create(sh, volume, "b.lun", 4096)
        sh.run("chattr -i -- \"$1\"", "x", volume / iscsi.LUN_FOLDER)
        assert sh.immutable() == set()
        other = tmp_path / "pools" / "data" / "other"
        (other / iscsi.LUN_FOLDER).mkdir(parents=True, mode=0o777)
        (other / iscsi.LUN_FOLDER).chmod(0o777)
        result = sh.run(iscsi.PREPARE_SCRIPT, "unos-iscsi-prepare", iscsi.LUN_FOLDER,
                        volume, "a.lun", 4096, volume, "b.lun", 8192, volume, "c.lun", 4096,
                        other, "d.lun", 4096, tmp_path / "nothing", "e.lun", 4096)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["ok a.lun", "wrong b.lun", "missing c.lun", "unsafe d.lun",
                                              "missing e.lun"]
        assert sh.immutable() == {str(volume / iscsi.LUN_FOLDER)}

    def test_prepare_refuses_a_link(self, sh, volume, tmp_path):
        self.create(sh, volume, "a.lun", 4096)
        (volume / iscsi.LUN_FOLDER / "a.lun").unlink()
        (volume / iscsi.LUN_FOLDER / "a.lun").symlink_to(tmp_path / "x")
        (tmp_path / "x").write_bytes(b"")
        result = sh.run(iscsi.PREPARE_SCRIPT, "unos-iscsi-prepare", iscsi.LUN_FOLDER, volume, "a.lun", 0)
        assert result.stdout.splitlines() == ["missing a.lun"]
        moved = tmp_path / "moved"
        (volume / iscsi.LUN_FOLDER).rename(moved)
        (volume / iscsi.LUN_FOLDER).symlink_to(moved)
        result = sh.run(iscsi.PREPARE_SCRIPT, "unos-iscsi-prepare", iscsi.LUN_FOLDER, volume, "a.lun", 0)
        assert result.stdout.splitlines() == ["unsafe a.lun"]

    def test_delete_removes_the_file_and_an_empty_folder(self, sh, volume):
        self.create(sh, volume, "a.lun")
        self.create(sh, volume, "b.lun")
        folder = volume / iscsi.LUN_FOLDER
        result = sh.run(iscsi.DELETE_SCRIPT, "unos-iscsi-delete", volume, iscsi.LUN_FOLDER, "a.lun")
        assert result.returncode == 0, result.stderr
        assert [p.name for p in folder.iterdir()] == ["b.lun"] and sh.immutable() == {str(folder)}
        result = sh.run(iscsi.DELETE_SCRIPT, "unos-iscsi-delete", volume, iscsi.LUN_FOLDER, "b.lun")
        assert result.returncode == 0, result.stderr
        assert not folder.exists()
        assert sh.run(iscsi.DELETE_SCRIPT, "unos-iscsi-delete", volume, iscsi.LUN_FOLDER, "b.lun").returncode == 0


class TestModules:
    def test_it_loads_the_modules_mounts_configfs_and_registers_iscsi(self, sh, tmp_path):
        configfs = tmp_path / "config"
        configfs.mkdir()
        result = sh.run(iscsi.MODULES_SCRIPT, "unos-iscsi-modules", configfs, *iscsi.MODULES)
        assert result.returncode == 0, result.stderr
        assert sh.log() == [f"modprobe -a {' '.join(iscsi.MODULES)}", f"mountpoint -q {configfs}",
                            f"mount -t configfs configfs {configfs}"]
        assert (configfs / "target" / "iscsi").is_dir()
        result = sh.run(iscsi.MODULES_SCRIPT, "unos-iscsi-modules", configfs, *iscsi.MODULES)
        assert result.returncode == 0 and sh.log()[3:] == [f"modprobe -a {' '.join(iscsi.MODULES)}"]

    def test_a_kernel_without_the_modules(self, sh, tmp_path):
        result = sh.run(iscsi.MODULES_SCRIPT, "unos-iscsi-modules", tmp_path, *iscsi.MODULES, STUB_NO_MODULES="1")
        assert result.returncode == 1 and not (tmp_path / "target").exists()
