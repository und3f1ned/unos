"""The host scripts of src/network/virtual.py, run against stub tools (ADR-0422).

Each test runs with /bin/sh and with dash, the /bin/sh of Debian and Ubuntu hosts (where it is
installed). ``ip``, ``networkctl`` and ``systemctl`` are stubs on PATH that log their arguments;
``ip link show dev <name>`` finds the links of STUB_LINKS, and a command that starts with STUB_FAIL
fails. The state, live and kept folders are temporary ones. test_virtual_interfaces.py
runs the API against a fake host that stands for these scripts.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.network import virtual

STUB = r'''#!{python}
import os, sys

tool, args = os.path.basename(sys.argv[0]), sys.argv[1:]
with open(os.path.join(os.environ["STUB_STATE"], "log"), "a") as out:
    out.write(" ".join([tool, *args]) + "\n")
if os.environ.get("STUB_FAIL") and " ".join([tool, *args]).startswith(os.environ["STUB_FAIL"]):
    sys.exit(tool + ": failed")
if tool == "ip" and args[:3] == ["link", "show", "dev"]:
    sys.exit(0 if args[3] in os.environ.get("STUB_LINKS", "").split() else 'Device "%s" does not exist.' % args[3])
sys.exit(0)
'''

SHELLS = ["/bin/sh", "dash"]


@pytest.fixture(params=SHELLS)
def host(request, tmp_path):
    shell = request.param
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed: Debian and Ubuntu hosts run the scripts with it")
    bin_dir, stub_state = tmp_path / "bin", tmp_path / "stub"
    bin_dir.mkdir()
    stub_state.mkdir()
    stub = bin_dir / "stub"
    stub.write_text(STUB.replace("{python}", sys.executable))
    stub.chmod(0o755)
    for tool in ("ip", "networkctl", "systemctl"):
        (bin_dir / tool).symlink_to(stub)
    state, live, kept = tmp_path / "run-unos-netdevs", tmp_path / "run-systemd-network", tmp_path / "etc-unos-netdevs"
    live.mkdir()
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_STATE": str(stub_state), "LC_ALL": "C"}

    def run(name, *args, stdin=None, **extra):
        return subprocess.run(
            [shell, "-c", virtual.SCRIPTS[name], name, str(state), str(live), str(kept), *args],
            env={**env, **extra}, input=stdin, capture_output=True, text=True, timeout=30,
        )

    def log():
        path = stub_state / "log"
        return path.read_text().splitlines() if path.exists() else []

    def files(folder: Path) -> dict[str, str]:
        return {p.name: p.read_text() for p in folder.iterdir()} if folder.exists() else {}

    return SimpleNamespace(run=run, log=log, state=state, live=live, kept=kept, files=files, shell=shell)


def _recorded_bond(host):
    """The live files of a bond of eth1 that replaces eth0's VLAN file and removes an old port's, saved."""
    (host.live / "04-unos-eth0.network").write_text("old parent\n")
    (host.live / "04-unos-br0+eth2.network").write_text("old port\n")
    result = host.run("unos-netdevs-save", "add:05-unos-bond0.netdev", "add:05-unos-bond0.network",
                      "replace:04-unos-eth0.network", "remove:04-unos-br0+eth2.network",
                      "created:bond0", "change:Create bond bond0")
    assert result.returncode == 0, result.stderr
    (host.live / "05-unos-bond0.netdev").write_text("netdev\n")
    (host.live / "05-unos-bond0.network").write_text("network\n")
    (host.live / "04-unos-eth0.network").write_text("new parent\n")
    (host.live / "04-unos-br0+eth2.network").unlink()


class TestSave:
    def test_save_records_the_ops_in_order_and_copies_what_the_change_replaces_or_removes(self, host):
        _recorded_bond(host)

        assert (host.state / "ops").read_text() == (
            "add 05-unos-bond0.netdev\nadd 05-unos-bond0.network\nreplace 04-unos-eth0.network\n"
            "remove 04-unos-br0+eth2.network\n")
        assert host.files(host.state / "saved") == {"04-unos-eth0.network": "old parent\n",
                                                    "04-unos-br0+eth2.network": "old port\n"}
        assert (host.state / "created").read_text() == "bond0\n"
        assert (host.state / "change").read_text() == "Create bond bond0\n"
        # Only root reads the record.
        assert host.state.stat().st_mode & 0o777 == 0o700
        assert (host.state / "ops").stat().st_mode & 0o777 == 0o600

    def test_a_save_forgets_an_earlier_record(self, host):
        host.state.mkdir()
        (host.state / "failed").write_text("failed\n")

        assert host.run("unos-netdevs-save", "add:05-unos-br0.netdev").returncode == 0

        assert sorted(p.name for p in host.state.iterdir()) == ["created", "ops", "saved"]

    def test_a_save_that_cannot_copy_fails(self, host):
        result = host.run("unos-netdevs-save", "remove:05-unos-missing.network")

        assert result.returncode != 0


class TestRevert:
    def test_without_the_marker_the_revert_does_nothing(self, host):
        _recorded_bond(host)

        result = host.run("unos-netdevs-revert", STUB_LINKS="bond0")

        assert result.returncode == 0 and host.log() == []
        assert (host.live / "05-unos-bond0.netdev").exists()

    def test_the_revert_puts_the_live_files_back_deletes_what_it_made_and_reloads(self, host):
        _recorded_bond(host)
        (host.state / "pending").write_text("1700000120\n")

        result = host.run("unos-netdevs-revert", STUB_LINKS="bond0 eth0")

        assert result.returncode == 0, result.stderr
        assert host.files(host.live) == {"04-unos-eth0.network": "old parent\n",
                                         "04-unos-br0+eth2.network": "old port\n"}
        assert host.log() == ["ip link show dev bond0", "ip link delete dev bond0", "networkctl reload"]
        assert sorted(p.name for p in host.state.iterdir()) == []

    def test_an_interface_that_is_gone_already_is_not_deleted(self, host):
        _recorded_bond(host)
        (host.state / "pending").write_text("1700000120\n")

        assert host.run("unos-netdevs-revert", STUB_LINKS="").returncode == 0

        assert host.log() == ["ip link show dev bond0", "networkctl reload"]

    def test_the_revert_also_undoes_a_change_that_failed_and_cannot_be_kept(self, host):
        _recorded_bond(host)
        (host.state / "undoing").write_text("1700000120\n")

        assert host.run("unos-netdevs-cancel").returncode == virtual.NOTHING_PENDING
        assert host.run("unos-netdevs-revert", STUB_LINKS="bond0").returncode == 0

        assert host.files(host.live) == {"04-unos-eth0.network": "old parent\n",
                                         "04-unos-br0+eth2.network": "old port\n"}

    def test_an_undo_step_that_fails_does_not_stop_the_others_nor_the_reload(self, host):
        _recorded_bond(host)
        (host.state / "pending").write_text("1700000120\n")

        result = host.run("unos-netdevs-revert", STUB_LINKS="bond0", STUB_FAIL="ip link delete")

        assert result.returncode == 1 and (host.state / "failed").exists()
        assert host.log()[-1] == "networkctl reload"
        assert host.files(host.live)["04-unos-eth0.network"] == "old parent\n"

    def test_a_revert_that_fails_says_so(self, host):
        _recorded_bond(host)
        (host.state / "pending").write_text("1700000120\n")

        result = host.run("unos-netdevs-revert", STUB_LINKS="bond0", STUB_FAIL="networkctl reload")

        assert result.returncode == 1
        assert (host.state / "failed").read_text() == "failed\n" and not (host.state / "pending").exists()

    def test_undo_does_the_same_without_the_marker(self, host):
        _recorded_bond(host)

        assert host.run("unos-netdevs-undo", STUB_LINKS="bond0").returncode == 0

        assert host.files(host.live) == {"04-unos-eth0.network": "old parent\n",
                                         "04-unos-br0+eth2.network": "old port\n"}
        assert (host.state / "ops").exists()

    def test_the_revert_has_nothing_that_systemd_expands(self):
        script = virtual.REVERT_SCRIPT
        assert "${" not in script and "$$" not in script and "%" not in script


class TestKeep:
    def test_keep_takes_the_marker_and_stops_the_timer(self, host):
        _recorded_bond(host)
        (host.state / "pending").write_text("1700000120\n")

        assert host.run("unos-netdevs-cancel").returncode == 0

        assert not (host.state / "pending").exists() and (host.state / "kept").read_text() == "1700000120\n"
        assert host.log() == ["systemctl stop unos-netdevs-revert.timer"]
        # The revert finds no change to undo.
        assert host.run("unos-netdevs-revert").returncode == 0
        assert (host.live / "05-unos-bond0.netdev").exists()

    def test_keep_without_a_change_that_waits_exits_3(self, host):
        assert host.run("unos-netdevs-cancel").returncode == virtual.NOTHING_PENDING

    def test_persist_writes_the_change_into_the_kept_folder_and_forgets_it(self, host):
        host.kept.mkdir()
        (host.kept / "04-unos-eth0.network").write_text("old parent\n")
        (host.kept / "04-unos-br0+eth2.network").write_text("old port\n")
        (host.kept / "05-unos-br0.netdev").write_text("another interface\n")
        _recorded_bond(host)

        result = host.run("unos-netdevs-persist")

        assert result.returncode == 0, result.stderr
        assert host.files(host.kept) == {"05-unos-bond0.netdev": "netdev\n", "05-unos-bond0.network": "network\n",
                                         "04-unos-eth0.network": "new parent\n", "05-unos-br0.netdev": "another interface\n"}
        assert not host.state.exists()

    def test_persist_without_a_record_only_forgets_the_marker(self, host):
        host.state.mkdir()
        (host.state / "kept").write_text("1\n")

        assert host.run("unos-netdevs-persist").returncode == 0
        assert not host.state.exists() and not host.kept.exists()

    def test_persist_makes_the_kept_folder(self, host):
        _recorded_bond(host)

        assert host.run("unos-netdevs-persist").returncode == 0
        assert "05-unos-bond0.netdev" in host.files(host.kept)

    def test_discard_forgets_the_change_its_marker_and_timer(self, host):
        _recorded_bond(host)
        (host.state / "pending").write_text("1\n")

        assert host.run("unos-netdevs-discard").returncode == 0

        assert not host.state.exists() and host.log() == ["systemctl stop unos-netdevs-revert.timer"]


class TestFiles:
    def test_write_and_remove_a_live_file(self, host):
        assert host.run("unos-netdevs-write", "05-unos-br0.netdev", stdin="[NetDev]\n").returncode == 0
        assert host.files(host.live) == {"05-unos-br0.netdev": "[NetDev]\n"}

        assert host.run("unos-netdevs-remove", "05-unos-br0.netdev").returncode == 0
        assert host.files(host.live) == {}

    def test_items_prints_the_metadata_of_unos_netdevs_only(self, host):
        assert host.run("unos-netdevs-items").stdout == ""
        (host.live / "05-unos-br0.netdev").write_text('# Made by UNOS\n# unos: {"name":"br0"}\n[NetDev]\n')
        (host.live / "10-other.netdev").write_text('# unos: {"name":"x"}\n')

        result = host.run("unos-netdevs-items")

        assert result.returncode == 0 and result.stdout == '{"name":"br0"}\n'

    def test_kept_any_says_whether_an_interface_is_kept(self, host):
        assert host.run("unos-netdevs-kept-any").returncode == 1
        host.kept.mkdir()
        (host.kept / "04-unos-eth0.network").write_text("x")
        assert host.run("unos-netdevs-kept-any").returncode == 1
        (host.kept / "05-unos-br0.netdev").write_text("x")
        assert host.run("unos-netdevs-kept-any").returncode == 0

    def test_the_boot_units_command_copies_the_kept_files_where_networkd_reads_them(self, host, tmp_path):
        command = re.search(r"^ExecStart=/bin/sh -c '(.*)'$", virtual.BOOT_UNIT_TEXT, re.MULTILINE).group(1)
        # systemd reads "$$" in a unit file as "$".
        command = command.replace("$$", "$").replace(virtual.KEPT_DIR, str(host.kept))
        command = command.replace(virtual.LIVE_DIR, str(tmp_path / "live2"))
        host.kept.mkdir()
        (host.kept / "05-unos-br0.netdev").write_text("netdev\n")
        (host.kept / "04-unos-br0+eth1.network").write_text("port\n")

        result = subprocess.run([host.shell, "-x", "-c", command], capture_output=True, text=True, timeout=30)

        assert result.returncode == 0, result.stderr
        assert host.files(tmp_path / "live2") == {"05-unos-br0.netdev": "netdev\n", "04-unos-br0+eth1.network": "port\n"}
        # The .netdev files first: a copy that stops part way names no interface it lacks.
        copies = [line for line in result.stderr.splitlines() if "cp -p" in line]
        assert copies[0].endswith(f"{tmp_path / 'live2'}/") and "05-unos-br0.netdev" in copies[0]

    def test_the_sysfs_script_lists_the_interfaces_it_finds(self, host):
        result = host.run("unos-netdevs-sysfs")

        assert result.returncode == 0
        assert all(len(line.split()) == 3 for line in result.stdout.splitlines())

    def test_the_drop_ins_script_finds_none_where_there_are_none(self, host):
        result = subprocess.run([host.shell, "-c", virtual.DROPINS_SCRIPT, "unos-netdevs-dropins", "10-unoscheck.network"],
                                capture_output=True, text=True, timeout=30)

        assert result.returncode == 0 and result.stdout == ""
