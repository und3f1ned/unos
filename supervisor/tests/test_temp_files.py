"""Temporary files of the supervisor (src/updates/updater.py, src/main.py).

In the container the tempfile module writes to the tmpfs at /tmp, in RAM
(deploy/docker-compose.yml): what is left there takes memory until a
restart, and what goes to another directory lands on the system disk (an SD
card). These tests check that:

* an update keeps the downloaded release in a temporary directory that is
  removed once the update is done with it: after success, a failed checksum
  check, any other failure and a cancelled download alike;
* src.main pins the directory of the tempfile module to $TMPDIR before
  anything uses it, so that a full /tmp fails instead of sending temporary
  files to /var/tmp.

Certificate uploads are checked in test_security.py, file and backup uploads
in test_upload_streaming.py.
"""

import asyncio
import errno
import hashlib
import importlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.main import pin_temporary_directory
from src.updates.schemas import CurrentVersion, ReleaseInfo, UpdateStatus

updater_module = importlib.import_module("src.updates.updater")

SUPERVISOR = Path(__file__).resolve().parents[1]
TAG = "v9.9.9"


def _source_tarball() -> bytes:
    """A release tarball as GitHub makes it: one directory, unos-<tag without the v>."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        readme = b"UNOS\n"
        info = tarfile.TarInfo(f"unos-{TAG[1:]}/README.md")
        info.size = len(readme)
        tar.addfile(info, io.BytesIO(readme))
    return buffer.getvalue()


TARBALL = _source_tarball()
CHECKSUM = hashlib.sha256(TARBALL).hexdigest()


def _release(checksum: str | None = CHECKSUM) -> ReleaseInfo:
    return ReleaseInfo(
        tag_name=TAG,
        name=TAG,
        body="Changes",
        published_at=datetime(2026, 1, 1),
        html_url=f"https://github.com/und3f1ned/unos/releases/tag/{TAG}",
        checksum_sha256=checksum,
    )


class _Stalled(httpx.AsyncByteStream):
    """A download that sends its first 8 KiB, then nothing more."""

    def __init__(self):
        self.written = asyncio.Event()

    async def __aiter__(self):
        yield bytes(8192)
        # Asked for more: the first part was written to the archive.
        self.written.set()
        await asyncio.Event().wait()


@pytest.fixture
async def update(tmp_path, monkeypatch):
    """An Updater with an empty directory for the tempfile module, and fakes
    for the backup, git, docker and the download (no network)."""
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    data = tmp_path / "data"
    monkeypatch.setattr(updater_module, "UPDATE_STATE_FILE", data / "update_state.json")
    monkeypatch.setattr(updater_module, "UPDATE_HISTORY_FILE", data / "update_history.json")
    monkeypatch.setattr(updater_module, "INSTALL_DIR", tmp_path / "unos")

    updater = updater_module.Updater()
    monkeypatch.setattr(updater, "get_current_version", AsyncMock(
        return_value=CurrentVersion(version="v1.0.0", commit_hash="0123456789ab")))
    monkeypatch.setattr(updater, "_create_backup", AsyncMock(return_value=tmp_path / "backup"))

    env = SimpleNamespace(updater=updater, temp=temp_dir, downloads=[], commands=[],
                          archive_while_applied=[], response=lambda: httpx.Response(200, content=TARBALL))

    def github(request):
        env.downloads.append(str(request.url))
        return env.response()

    updater._http_client = httpx.AsyncClient(transport=httpx.MockTransport(github))

    # The commands of _apply_update: tar "extracts" the release, the others succeed.
    def run(cmd, **kwargs):
        env.commands.append(cmd)
        if cmd[0] == "tar":
            archive, extract = Path(cmd[2]), Path(cmd[4])
            env.archive_while_applied.append(archive.read_bytes() == TARBALL)
            (extract / f"unos-{TAG[1:]}").mkdir()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    try:
        yield env
    finally:
        await updater._http_client.aclose()


class TestUpdateDownloadIsRemoved:
    """install_update removes the directory with the downloaded release.

    It was removed only when the download itself failed or by _apply_update:
    a failed checksum check, any other exception before _apply_update and a
    cancelled download left the tarball in /tmp until a restart.
    """

    async def test_success(self, update):
        await update.updater.install_update(_release())

        assert update.updater.get_progress().status == UpdateStatus.COMPLETED
        assert update.downloads == [f"https://github.com/und3f1ned/unos/archive/refs/tags/{TAG}.tar.gz"]
        # The download was still there while the update was applied.
        assert update.archive_while_applied == [True]
        assert list(update.temp.iterdir()) == []

    async def test_failed_checksum_check(self, update):
        with pytest.raises(updater_module.UpdateError, match="Checksum verification failed"):
            await update.updater.install_update(_release(checksum="0" * 64))

        progress = update.updater.get_progress()
        assert (progress.status, progress.message) == (UpdateStatus.FAILED, "Update failed")
        assert len(update.downloads) == 1
        assert update.commands == []
        assert list(update.temp.iterdir()) == []

    async def test_exception_before_the_update_is_applied(self, update, monkeypatch):
        monkeypatch.setattr(update.updater, "_verify_checksum",
                            AsyncMock(side_effect=OSError(errno.EIO, "Input/output error")))

        with pytest.raises(OSError, match="Input/output error"):
            await update.updater.install_update(_release())

        assert update.updater.get_progress().status == UpdateStatus.FAILED
        assert update.commands == []
        assert list(update.temp.iterdir()) == []

    async def test_cancelled_download(self, update):
        stalled = _Stalled()
        update.response = lambda: httpx.Response(
            200, headers={"Content-Length": str(4 * 8192)}, stream=stalled)

        task = asyncio.create_task(update.updater.install_update(_release()))
        await asyncio.wait_for(stalled.written.wait(), 15)
        assert len(list(update.temp.iterdir())) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert update.commands == []
        assert list(update.temp.iterdir()) == []

    # Failures that already removed it.

    async def test_failed_download(self, update):
        update.response = lambda: httpx.Response(404)

        with pytest.raises(updater_module.UpdateError, match="Download failed"):
            await update.updater.install_update(_release())

        assert update.commands == []
        assert list(update.temp.iterdir()) == []

    async def test_failed_update(self, update, monkeypatch):
        commands = update.commands

        def run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[0] == "tar":
                (Path(cmd[4]) / f"unos-{TAG[1:]}").mkdir()
            failed = cmd[:2] == ["git", "checkout"]
            return subprocess.CompletedProcess(cmd, 1 if failed else 0, "", "no such tag" if failed else "")

        monkeypatch.setattr(subprocess, "run", run)

        with pytest.raises(updater_module.UpdateError, match="Failed to checkout release: no such tag"):
            await update.updater.install_update(_release())

        assert ["git", "checkout", TAG] in commands
        assert list(update.temp.iterdir()) == []


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
                tempfile.NamedTemporaryFile()
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
