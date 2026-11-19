"""The supervisor's data directory follows one setting, UNOS_DATA_DIR (src/paths.py, ADR-0419).

* src.paths reads UNOS_DATA_DIR once, when it is first imported: unset or empty, /data (the volume of
  the supervisor's container); otherwise an absolute path of an existing directory, or the supervisor
  refuses to start.
* Started with UNOS_DATA_DIR=<dir> and no other setting of the data, the supervisor keeps its
  database, its JWT secret, its state files, certificates and backups under <dir>, with the names they
  have under /data, and the health check reads the database, the RAID status and the UPS settings
  there. The paths of the host (/etc/fstab, smb.conf, the pools' folder and the like) keep settings of
  their own, which default to the host's files, and /run/samba stays fixed: two starts with two data
  directories differ only in the paths under each. The starts' host settings name folders of the test,
  so that they touch none of this machine's.
* The compose files set no UNOS_DATA_DIR: on a NAS the data directory stays the volume at /data.
* The value is read once per process, so a test never changes UNOS_DATA_DIR in its own process: it
  would see the paths of the start. conftest.py sets it before anything imports the supervisor; a
  test moves a module's path with monkeypatch, and a child process gets the variable in its
  environment.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from src import paths

SUPERVISOR = Path(__file__).resolve().parents[1]
REPO = SUPERVISOR.parent
TESTS = SUPERVISOR / "tests"

# Every data path of the supervisor and where it lies in the data directory: the names it has under
# /data on the device, which this change keeps.
DATA_PATHS = {
    "settings.db_path": "unos.db",
    "settings.app_catalog_path": "app-catalog.json",
    "src.audit.router.AUDIT_FILE": "audit/audit.jsonl",
    "src.backup.router.BACKUP_DIR": "backups",
    "src.health.router.RAID_STATUS_FILE": "raid_status.json",
    "src.health.router.SMART_HISTORY_FILE": "smart_history.json",
    "src.notifications.router.CONFIG_FILE": "notifications.json",
    "src.notifications.router.LOGS_FILE": "notification_logs.json",
    "src.offsite.service.DATA_DIR": ".",
    "src.offsite.service.SETTINGS_FILE": "offsite.json",
    "src.offsite.service.SECRETS_FILE": "offsite_secrets.json",
    "src.offsite.service.STATUS_FILE": "offsite_status.json",
    "src.shares.iscsi.SECRETS_FILE": "iscsi_secrets.json",
    "src.power.router.CONFIG_FILE": "power_config.json",
    "src.power.router.UPS_CONFIG_FILE": "ups_config.json",
    "src.power.router.WOL_FILE": "wol_config.json",
    "src.scheduler.router.TASKS_FILE": "scheduled_tasks.json",
    "src.scheduler.router.RUNS_FILE": "task_runs.json",
    "src.scheduler.router.SCHEDULED_BACKUP_DIR": "backups",
    "src.scheduler.router.DB_FILE": "unos.db",
    "src.scheduler.router.BACKUP_KEYS_FILE": "scheduled_backup_keys.json",
    "src.security.router.CERTS_DIR": "certs",
    "src.storage.volume_usage.HISTORY_FILE": "volume_usage.json",
    "src.updates.updater.DB_FILE": "unos.db",
    "src.updates.updater.UPDATE_STATE_FILE": "update_state.json",
    "src.updates.updater.UPDATE_HISTORY_FILE": "update_history.json",
    "src.updates.updater.UPDATE_CONFIG_FILE": "update_config.json",
}

# The settings of the host paths, and of the container's paths outside its data directory, and their
# defaults: the host's files, which UNOS_DATA_DIR does not move (src/config.py).
HOST_DEFAULTS = {
    "smb_conf_path": "/etc/samba/smb.conf",
    "nfs_exports_path": "/etc/exports",
    "pools_base_path": "/mnt/pools",
    "fstab_path": "/etc/fstab",
    "mounts_base_path": "/mnt",
    "served_certificate_dir": "/served-certificate",
    "update_backup_dir": "/var/lib/unos/backups",
    "docker_socket": "/var/run/docker.sock",
    "webui_tls_host": "webui",
    "webui_tls_port": 443,
}
# What the two starts set them to, in a folder of the test (the same in both), and the modules' paths
# that follow each.
HOST_SETTINGS = {
    "UNOS_SMB_CONF_PATH": "smb.conf",
    "UNOS_NFS_EXPORTS_PATH": "exports",
    "UNOS_POOLS_BASE_PATH": "pools",
    "UNOS_FSTAB_PATH": "fstab",
    "UNOS_MOUNTS_BASE_PATH": "mnt",
    "UNOS_SERVED_CERTIFICATE_DIR": "served-certificate",
    "UNOS_UPDATE_BACKUP_DIR": "update-backups",
    "UNOS_INSTALL_DIR": "no-install",
}
HOST_PATHS = {
    "settings.smb_conf_path": "smb.conf",
    "settings.nfs_exports_path": "exports",
    "settings.pools_base_path": "pools",
    "settings.fstab_path": "fstab",
    "settings.mounts_base_path": "mnt",
    "settings.served_certificate_dir": "served-certificate",
    "settings.update_backup_dir": "update-backups",
    "src.storage.pools.FSTAB_PATH": "fstab",
    "src.storage.pools.FSTAB_BACKUP_PATH": "fstab.unos-backup",
    "src.files.router.BASE_PATH": "pools",
    "src.offsite.service.MOUNTS_BASE": "mnt",
    "src.security.serving.SERVED_DIR": "served-certificate",
    "src.updates.updater.BACKUP_DIR": "update-backups",
    "src.updates.updater.INSTALL_DIR": "no-install",
}
# Host paths without a setting, the same wherever the data directory is.
FIXED_PATHS = {
    "src.shares.smb.CACHE_DIRECTORY": "/run/samba",
    "src.files.router.MOUNTINFO": "/proc/self/mountinfo",
    "src.offsite.restic.HOSTNAME_FILE": "/proc/sys/kernel/hostname",
}

# Imports every module of the supervisor, lists every absolute path in their globals and in the
# settings, then starts the app (its lifespan, without the background work), creates the
# administrator and reads the full health check. argv: the file to write the report to.
CHILD = r"""
import importlib, json, os, pkgutil, sys
from pathlib import PurePath

import src
for info in pkgutil.walk_packages(src.__path__, "src."):
    importlib.import_module(info.name)
from src.config import get_settings

found = {}

def visit(where, value, depth=0):
    if isinstance(value, (str, PurePath)):
        if str(value).startswith("/"):
            found[where] = str(value)
    elif depth < 3 and isinstance(value, dict):
        for key, item in value.items():
            visit(f"{where}[{key!r}]", key, depth + 1)
            visit(f"{where}[{key!r}]", item, depth + 1)
    elif depth < 3 and isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            visit(f"{where}[{index}]", item, depth + 1)
    elif depth < 3 and isinstance(value, (set, frozenset)):
        for item in value:
            visit(f"{where}[{item!r}]", item, depth + 1)

for name, module in sorted(sys.modules.items()):
    if name == "src" or name.startswith("src."):
        for attr, value in list(vars(module).items()):
            visit(f"{name}.{attr}", value)
settings = get_settings()
for field in type(settings).model_fields:
    visit(f"settings.{field}", getattr(settings, field))

from fastapi.testclient import TestClient
from src.main import app

with TestClient(app) as client:
    account = {"username": "owner", "password": "Owner-pass-2026"}
    setup = client.post("/api/v1/auth/setup", json=account)
    login = client.post("/api/v1/auth/login", json=account)
    token = login.json().get("access_token", "")
    health = client.get("/api/v1/health/full", headers={"Authorization": f"Bearer {token}"})

with open(sys.argv[1], "w") as report:
    json.dump({"paths": found, "setup": setup.status_code, "login": login.status_code,
               "health": health.status_code, "components": health.json().get("components", {})}, report)
"""


def _clean_environment(root: Path) -> dict[str, str]:
    """This process's environment without the supervisor's settings and Docker's, and with nothing
    of this machine within reach: the host paths in ROOT/host, a Docker socket that does not exist,
    the web UI's Caddy at a closed port, host commands run here from a PATH without programs, the
    temporary files in ROOT."""
    env = {name: value for name, value in os.environ.items() if not name.startswith(("UNOS_", "DOCKER_"))}
    (root / "no-programs").mkdir(exist_ok=True)
    (root / "tmp").mkdir(exist_ok=True)
    (root / "host" / "pools").mkdir(parents=True, exist_ok=True)
    env.update({name: str(root / "host" / path) for name, path in HOST_SETTINGS.items()})
    env.update({
        "UNOS_WEBUI_TLS_HOST": "127.0.0.1",
        "UNOS_WEBUI_TLS_PORT": "1",
        "UNOS_JWT_SECRET": "",
        "UNOS_DISABLE_BACKGROUND_TASKS": "1",
        "UNOS_HOST_EXEC": "local",
        "UNOS_DOCKER_SOCKET": str(root / "no-docker.sock"),
        "DOCKER_HOST": f"unix://{root / 'no-docker.sock'}",
        "PATH": str(root / "no-programs"),
        "TMPDIR": str(root / "tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def _inside(path: str, folder: Path) -> bool:
    return Path(path).is_relative_to(folder)


@pytest.fixture(scope="module")
def two_starts(tmp_path_factory):
    """The supervisor started twice, each time with UNOS_DATA_DIR alone naming a data directory of its
    own, which holds a RAID status and UPS settings: the reports of both starts, with their folders."""
    root = tmp_path_factory.mktemp("data-dir")
    env = _clean_environment(root)
    starts = []
    for name in ("first", "second"):
        assert not (root / name).exists()
        data = root / name / "data"
        data.mkdir(parents=True)
        (data / "raid_status.json").write_text(json.dumps({"tank": {"status": "degraded"}}))
        (data / "ups_config.json").write_text(json.dumps({"enabled": True}))
        report = root / name / "report.json"
        result = subprocess.run([sys.executable, "-c", CHILD, str(report)], cwd=SUPERVISOR,
                                env={**env, "UNOS_DATA_DIR": str(data)}, capture_output=True, text=True,
                                timeout=180)
        assert result.returncode == 0, result.stderr[-4000:]
        starts.append((data, json.loads(report.read_text())))
    return root, starts


class TestTheDataDirectory:
    def test_every_data_path_lies_in_the_data_directory_with_its_name_of_slash_data(self, two_starts):
        """DATA_PATHS are all the paths of the modules and settings in the data directory (but those
        of src.paths itself), each with the name it has under /data on a NAS."""
        for data, report in two_starts[1]:
            found = {name: path for name, path in report["paths"].items()
                     if _inside(path, data) and not name.startswith("src.paths.")}
            assert {name: os.path.relpath(path, data) for name, path in found.items()} == DATA_PATHS

    def test_only_the_data_paths_follow_it(self, two_starts):
        """What differs between two starts is what lies in each one's data directory; nothing is left
        at /data."""
        (first, a), (second, b) = two_starts[1]
        differ = {name for name in a["paths"].keys() | b["paths"].keys()
                  if a["paths"].get(name) != b["paths"].get(name)}
        in_first = {name for name, path in a["paths"].items() if _inside(path, first)}
        in_second = {name for name, path in b["paths"].items() if _inside(path, second)}
        assert differ == in_first == in_second
        left = {name: path for name, path in {**a["paths"], **b["paths"]}.items()
                if re.match(r"^/data(/|$)", path) and name != "src.paths.DEFAULT_DATA_DIR"}
        assert left == {}

    def test_host_paths_keep_their_own_settings(self, two_starts):
        root, starts = two_starts
        expected = {name: str(root / "host" / path) for name, path in HOST_PATHS.items()} | FIXED_PATHS
        for _data, report in starts:
            assert {name: report["paths"].get(name) for name in expected} == expected

    def test_host_paths_default_to_the_hosts_own(self):
        from src.config import Settings

        assert {name: Settings.model_fields[name].default for name in HOST_DEFAULTS} == HOST_DEFAULTS

    def test_the_supervisor_keeps_its_database_secret_and_audit_log_there(self, two_starts):
        for data, report in two_starts[1]:
            assert (report["setup"], report["login"], report["health"]) == (200, 200, 200)
            assert (data / "unos.db").is_file()
            secret = data / "jwt_secret"
            assert secret.is_file() and stat.S_IMODE(secret.stat().st_mode) == 0o600
            assert "owner" in (data / "audit" / "audit.jsonl").read_text()

    def test_the_health_check_reads_the_database_raid_status_and_ups_settings_there(self, two_starts):
        for _data, report in two_starts[1]:
            components = report["components"]
            assert components["database"]["status"] == "ok", components["database"]
            assert components["raid"] == {"status": "degraded", "pools": ["tank"]}
            assert components["ups"] == {"status": "configured", "enabled": True}


class TestTheSetting:
    def test_an_absolute_existing_directory_is_taken(self, tmp_path):
        assert paths.data_dir_from(str(tmp_path)) == tmp_path
        assert paths.data_dir_from(f"{tmp_path}/") == tmp_path

    @pytest.mark.parametrize("value", [None, ""])
    def test_unset_or_empty_is_slash_data(self, value):
        if os.path.isdir("/data"):
            assert paths.data_dir_from(value) == Path("/data")
        else:
            with pytest.raises(paths.DataDirError, match="UNOS_DATA_DIR=/data does not exist"):
                paths.data_dir_from(value)

    def test_a_relative_path_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        with pytest.raises(paths.DataDirError, match="not an absolute path"):
            paths.data_dir_from("data")

    def test_a_missing_directory_is_refused(self, tmp_path):
        with pytest.raises(paths.DataDirError, match="does not exist"):
            paths.data_dir_from(str(tmp_path / "missing"))

    def test_a_file_is_refused(self, tmp_path):
        (tmp_path / "file").write_text("")
        with pytest.raises(paths.DataDirError, match="not a directory"):
            paths.data_dir_from(str(tmp_path / "file"))

    @pytest.mark.parametrize("value, reason", [("data", "not an absolute path"), ("/nonexistent/unos-data", "does not exist")])
    def test_the_supervisor_refuses_to_start_with_a_bad_one(self, tmp_path, value, reason):
        env = {**_clean_environment(tmp_path), "UNOS_DATA_DIR": value}
        result = subprocess.run([sys.executable, "-c", "import src.main"], cwd=SUPERVISOR, env=env,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode != 0
        assert "DataDirError" in result.stderr and reason in result.stderr, result.stderr[-2000:]

    @pytest.mark.parametrize("field", ["fstab_path", "mounts_base_path", "served_certificate_dir", "update_backup_dir"])
    def test_a_host_path_setting_must_be_absolute(self, field):
        from pydantic import ValidationError

        from src.config import Settings

        with pytest.raises(ValidationError, match="must be an absolute path"):
            Settings(**{field: "relative/path"})

    def test_the_compose_files_keep_the_volume_at_slash_data(self):
        """On a NAS, and on the test stack, the data directory is the volume at /data: no compose file
        sets UNOS_DATA_DIR."""
        for name in ("docker-compose.yml", "docker-compose.test.yml"):
            supervisor = yaml.safe_load((REPO / "deploy" / name).read_text())["services"]["supervisor"]
            assert not [entry for entry in supervisor["environment"] if entry.split("=", 1)[0] == "UNOS_DATA_DIR"]
            assert [volume for volume in supervisor["volumes"]
                    if isinstance(volume, str) and volume.endswith(":/data")], name

    def test_this_run_keeps_its_data_in_a_folder_of_its_own(self):
        """conftest.py: no test reaches /data; each pytest process has its own folder (ADR-0383), which
        no test writes to: a test that did not move a data path fails to write it, as at /data."""
        folder = Path(os.environ["UNOS_SMB_CONF_PATH"]).parent
        assert os.environ[paths.DATA_DIR_VARIABLE] == str(paths.DATA_DIR)
        assert paths.DATA_DIR == folder / "data" and paths.DATA_DIR.is_dir()
        assert not os.access(paths.DATA_DIR, os.W_OK) or os.geteuid() == 0

    def test_no_test_changes_the_variable_in_its_own_process(self):
        """The supervisor reads it once, at its first import: a test that set it would see the paths of
        the start. Only conftest.py sets it, before that import; this file and test_standin.py give it to
        child processes."""
        allowed = {"conftest.py", "test_data_dir.py", "test_standin.py"}
        named = sorted(path.name for path in TESTS.rglob("*.py")
                       if path.name not in allowed
                       and re.search(r"UNOS_DATA_DIR|DATA_DIR_VARIABLE", path.read_text(encoding="utf-8")))
        assert named == [], "move a module's path with monkeypatch.setattr instead"
