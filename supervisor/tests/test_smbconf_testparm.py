"""The rendered smb.conf against a real testparm (spec 9.3, P1).

Skipped unless testparm is installed. To run it with the device's Samba
(Debian trixie, Samba 4.22), see spec 9.3: a python:3.11-slim-trixie container
with samba-common-bin. Every line UNOS writes must be known to that Samba:
testparm must exit 0 and must not report an unknown parameter.
"""

import shutil
import subprocess

import pytest

from src.shares import smbconf
from src.shares.service import ShareRender

pytestmark = pytest.mark.skipif(shutil.which("testparm") is None, reason="testparm is not installed")

FOLDER = "/srv/unos-testparm"


def share(name, *, guest_ok=False, readers=(), writers=(), comment="", browseable=True):
    return ShareRender(name=name, path=f"{FOLDER}/{name}", comment=comment, browseable=browseable,
                       guest_ok=guest_ok, readers=tuple(readers), writers=tuple(writers))


def testparm(tmp_path, text: str) -> str:
    # The cache directory of [global]: testparm exits 1 without it (spec 2.2).
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
