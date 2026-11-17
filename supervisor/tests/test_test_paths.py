"""Each pytest process of the backend tests, a run's or a parallel run's worker, keeps the paths that
the supervisor reads from its settings (smb.conf, the NFS export file, the pools' folder) in a
temporary folder of its own and removes it at its end (conftest.py, ADR-0383): runs in several
worktrees, and the workers of one run, go at once without sharing a path."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.config import get_settings

SUPERVISOR = Path(__file__).resolve().parents[1]
NAMES = ("UNOS_SMB_CONF_PATH", "UNOS_NFS_EXPORTS_PATH", "UNOS_POOLS_BASE_PATH")
PROBE = '''
import os


def test_probe():
    paths = [os.environ[name] for name in {names!r}]
    with open(os.environ["PROBE_REPORT"], "w") as report:
        report.write("\\n".join(paths + [str(os.path.isdir(os.path.dirname(paths[0])))]) + "\\n")
'''


def this_process_folder() -> Path:
    return Path(os.environ["UNOS_SMB_CONF_PATH"]).parent


def test_the_settings_of_this_process_point_into_its_folder():
    settings = get_settings()
    folder = this_process_folder()

    assert folder.is_dir() and folder.name.startswith("unos-tests-")
    assert [Path(path).parent for path in (settings.smb_conf_path, settings.nfs_exports_path,
                                           settings.pools_base_path)] == [folder] * 3


def test_two_runs_at_once_have_folders_of_their_own_and_remove_them(tmp_path):
    (tmp_path / "test_probe.py").write_text(PROBE.format(names=NAMES))
    runs = [subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p", "tests.conftest", "-p", "no:cacheprovider",
                              f"--rootdir={tmp_path}", str(tmp_path / "test_probe.py")],
                             cwd=SUPERVISOR, env=dict(os.environ, PROBE_REPORT=str(tmp_path / f"report-{n}")),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for n in range(2)]
    outputs = [run.communicate(timeout=120)[0] for run in runs]
    reports = [(tmp_path / f"report-{n}").read_text().split() for n in range(2)]

    assert [run.returncode for run in runs] == [0, 0], outputs
    folders = []
    for *paths, existed in reports:
        folder = Path(paths[0]).parent
        assert [Path(path).parent for path in paths] == [folder] * 3
        assert folder.name.startswith("unos-tests-") and existed == "True"
        assert not folder.exists()  # removed at the end of its run
        folders.append(folder)
    assert len({*folders, this_process_folder()}) == 3
