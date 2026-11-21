"""Temporary files of the supervisor (src/main.py).

In the container the tempfile module writes to the tmpfs at /tmp, in RAM
(deploy/docker-compose.yml): what is left there takes memory until a
restart, and what goes to another directory lands on the system disk (an SD
card). These tests check that src.main pins the directory of the tempfile
module to $TMPDIR before anything uses it, so that a full /tmp fails instead
of sending temporary files to /var/tmp. The in-app updater downloads nothing
into it: a release's images are the release installer's (ADR-0443).

Certificate uploads are checked in test_security.py, file and backup uploads
in test_upload_streaming.py.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src.main import pin_temporary_directory

SUPERVISOR = Path(__file__).resolve().parents[1]


class TestPinnedTemporaryDirectory:
    """src.main pins the directory of the tempfile module to $TMPDIR.

    tempfile chooses its directory on first use, the first of $TMPDIR, /tmp,
    /var/tmp, ... where it can write a small file, and keeps it. With the
    tmpfs at /tmp full at that moment, every temporary file of the
    supervisor went to /var/tmp, on the system disk.
    """

    @pytest.fixture(autouse=True)
    def unchosen(self, monkeypatch):
        """No directory chosen yet, as in a new process."""
        monkeypatch.setattr(tempfile, "tempdir", None)
        monkeypatch.delenv("TEMP", raising=False)
        monkeypatch.delenv("TMP", raising=False)

    def test_tmpdir_is_pinned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TMPDIR", str(tmp_path))

        pin_temporary_directory()

        assert tempfile.tempdir == str(tmp_path)
        with tempfile.NamedTemporaryFile() as file:
            assert Path(file.name).parent == tmp_path

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                        reason="root can write into a directory without write permission")
    def test_full_tmpdir_fails_instead_of_moving_to_another_directory(self, tmp_path, monkeypatch):
        tmpfs = tmp_path / "tmpfs"
        var_tmp = tmp_path / "var-tmp"  # stands for /var/tmp: the next directory tempfile tries
        tmpfs.mkdir()
        var_tmp.mkdir()
        monkeypatch.setenv("TMPDIR", str(tmpfs))
        monkeypatch.setenv("TEMP", str(var_tmp))
        # Nothing can be written in it for now, as in a full tmpfs.
        tmpfs.chmod(0o500)
        try:
            # On its own, tempfile silently takes the next directory.
            assert tempfile.gettempdir() == str(var_tmp)
            tempfile.tempdir = None

            pin_temporary_directory()

            assert tempfile.gettempdir() == str(tmpfs)
            with pytest.raises(PermissionError):
                tempfile.mkstemp()
            with pytest.raises(PermissionError):
                tempfile.NamedTemporaryFile()  # noqa: SIM115 - it raises
        finally:
            tmpfs.chmod(0o700)
        assert list(var_tmp.iterdir()) == []
        assert list(tmpfs.iterdir()) == []

    def test_unset_tmpdir_changes_nothing(self, monkeypatch):
        monkeypatch.delenv("TMPDIR", raising=False)

        pin_temporary_directory()

        assert tempfile.tempdir is None

    @pytest.mark.parametrize("value", ["", "missing", "file"])
    def test_tmpdir_that_is_not_a_directory_changes_nothing(self, tmp_path, monkeypatch, value):
        (tmp_path / "file").write_text("x")
        monkeypatch.setenv("TMPDIR", str(tmp_path / value) if value else "")

        pin_temporary_directory()

        assert tempfile.tempdir is None

    def test_importing_the_application_pins_it_before_anything_uses_tempfile(self, tmp_path):
        """In a new interpreter, as uvicorn imports src.main:app."""
        script = (
            "import tempfile\n"
            "probes = []\n"
            "choose = tempfile._get_default_tempdir\n"
            "tempfile._get_default_tempdir = lambda: probes.append(1) or choose()\n"
            "import src.main\n"
            "print(tempfile.tempdir, len(probes))\n"
        )
        env = {**os.environ, "TMPDIR": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-c", script], cwd=SUPERVISOR, env=env,
                                capture_output=True, text=True, timeout=300)

        assert result.returncode == 0, result.stderr
        # Pinned, and tempfile never had to choose a directory.
        assert result.stdout.split() == [str(tmp_path), "0"]
