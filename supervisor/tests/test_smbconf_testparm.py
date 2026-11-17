"""The rendered smb.conf against a real testparm (ADR-0189).

Skipped unless testparm is installed. To run it with the device's Samba
(Debian trixie, Samba 4.22), put the absolute path of scripts/dev/bin first on
PATH (its testparm runs the device's), or run it in a python:3.11-slim-trixie
container with samba-common-bin. Every line UNOS writes must be known to that
Samba: testparm must exit 0 and must not report an unknown parameter.
"""

import shutil
import subprocess

import pytest

from src.shares import smbconf
from src.shares.service import ShareRender

pytestmark = pytest.mark.skipif(shutil.which("testparm") is None, reason="testparm is not installed")

FOLDER = "/srv/unos-testparm"


def share(name, *, guest_ok=False, readers=(), writers=(), comment="", browseable=True, **options):
    return ShareRender(name=name, path=f"{FOLDER}/{name}", comment=comment, browseable=browseable,
                       guest_ok=guest_ok, readers=tuple(readers), writers=tuple(writers), **options)


def testparm(tmp_path, text: str) -> str:
    # The cache directory of [global]: testparm exits 1 without it (ADR-0277).
    subprocess.run(["mkdir", "-p", "/run/samba"], capture_output=True, check=False)
    conf = tmp_path / "smb.conf"
    conf.write_text(text)
    result = subprocess.run(["testparm", "-s", "--suppress-prompt", str(conf)],
                            capture_output=True, text=True, timeout=60)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Unknown parameter" not in output, output
    assert "Ignoring unknown parameter" not in output, output
    return result.stdout


@pytest.mark.parametrize("guest", [False, True])
def test_global_section(tmp_path, guest):
    shares = [share("guests", guest_ok=True)] if guest else []
    output = testparm(tmp_path, smbconf.render_config(shares))
    assert "[global]" in output


@pytest.mark.parametrize("interfaces", [("eth0",), ("enx00e04c680001", "eth0", "wlan0"), ()])
def test_the_chosen_interfaces(tmp_path, interfaces):
    """Both lines of a choice of interfaces (ADR-0388), with names that the host may lack: testparm
    reads the list, and smbd skips a name that is not on the host when it starts."""
    output = testparm(tmp_path, smbconf.render_config([share("docs", readers=["alice"])], interfaces))
    lines = {key.strip(): value.strip() for key, _, value in
             (line.partition("=") for line in output.splitlines() if "=" in line)}
    assert lines["interfaces"].replace(",", " ").split() == ["lo", *sorted(interfaces)]
    assert lines["bind interfaces only"].lower() == "yes"


@pytest.mark.parametrize(
    "render",
    [
        share("noprincipal"),
        share("readers", readers=["alice", "bob"]),
        share("writers", readers=["bob"], writers=["alice"]),
        share("guests", guest_ok=True),
        share("guestwriters", guest_ok=True, writers=["carol"]),
        share("hidden", readers=["alice"], comment="Team files", browseable=False),
    ],
    ids=lambda s: s.name,
)
def test_every_share_case(tmp_path, render):
    output = testparm(tmp_path, smbconf.render_config([render]))
    assert f"[{render.name}]" in output


def test_many_users_and_a_long_comment(tmp_path):
    users = [f"user{i:02d}" for i in range(1, 51)]
    big = share("big", readers=users[:25], writers=users[25:], comment="c" * 256)
    output = testparm(tmp_path, smbconf.render_config([big, share("noprincipal")]))
    assert "[big]" in output and "[noprincipal]" in output


def sections(output: str) -> list[str]:
    """The section names of testparm's dump, in order."""
    return [line[1:-1] for line in (raw.strip() for raw in output.splitlines()) if line[:1] == "[" and line[-1:] == "]"]


def test_every_rendered_share_is_a_section_of_its_own(tmp_path):
    """Samba reads some names as the global section (a share named globals became
    server-wide defaults, also for the guest share after it): every share UNOS renders
    must come back from testparm as a section of its own, whatever its name and place."""
    shares = [
        share("Alpha", readers=["alice"], writers=["bob"]),
        share("global_files", readers=["alice"]),
        share("Globalsx", readers=["bob"]),
        share("noprincipal"),
        share("zeta", guest_ok=True),
    ]
    output = testparm(tmp_path, smbconf.render_config(shares))
    assert sections(output) == ["global", "Alpha", "global_files", "Globalsx", "noprincipal", "zeta"]


def test_samba_takes_the_logging_lines(tmp_path):
    """Samba takes the logging lines of [global] as UNOS writes them (ADR-0277): its log file in
    /run/samba with a size limit, the syslog backend at level 2, and no header line before each
    message, which testparm's dump names timestamp logs (UNOS writes its other name, debug
    timestamp). The dump of testparm -s shows no log level line, not even for a debug class that
    Samba lacks (Samba 4.22.6 on the dev device, 2026-11-08), so of that line testparm checks only
    the parameter's name, which testparm() asserts for every line. The class auth_audit at level 2
    is asserted on the rendered text by test_smbconf.py, and at work by the device check of
    shares.smb-auth-audit-logging."""
    output = testparm(tmp_path, smbconf.render_config([]))
    lines = {" ".join(line.split()).lower() for line in output.splitlines()}

    assert "log file = /run/samba/log.samba" in lines
    assert "max log size = 100" in lines
    assert "logging = syslog@2" in lines
    assert "timestamp logs = no" in lines, output


# Every combination of the protocol options of a share (ADR-0380): encryption required, Time
# Machine, and Previous Versions (rendered with the pool or volume whose snapshots it lists).
OPTION_SETS = [
    {"encryption_required": encryption, "time_machine": time_machine,
     "snapshot_root": FOLDER if snapshots else None}
    for encryption in (False, True) for time_machine in (False, True) for snapshots in (False, True)
]


@pytest.mark.parametrize("options", OPTION_SETS,
                         ids=lambda o: "-".join(k for k, v in o.items() if v) or "none")
def test_every_combination_of_the_protocol_options(tmp_path, options):
    shares = [share("opts", readers=["alice"], writers=["bob"], **options), share("plain", readers=["bob"])]
    output = testparm(tmp_path, smbconf.render_config(shares))
    dump = {" ".join(line.split()).lower() for line in output.splitlines()}

    assert sections(output) == ["global", "opts", "plain"]
    # testparm prints a share's parameters that differ from [global]: encryption under its name of
    # Samba 4.14 and later, server smb encrypt, of which smb encrypt is a synonym.
    assert any(line.endswith("smb encrypt = required") for line in dump) == options["encryption_required"]
    assert ("fruit:time machine = yes" in dump) == options["time_machine"]
    assert ("shadow:snapdir = .snapshots" in dump) == bool(options["snapshot_root"])

