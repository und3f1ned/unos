"""The vulnerability scan of UNOS's two images (ADR-0446): the Image vulnerabilities job of the weekly
Audit workflow and make image-scan run scripts/image-scan.sh, which builds the supervisor's and the
web UI's image and scans each with trivy, pinned by the digest of its image, with the policy of
ADR-0446 and the ignore file .trivyignore.yaml.

The job, the policy and the ignore file are read as files; the script runs with a fake docker that
records its calls. No test here needs Docker or the network.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "image-scan.sh"
IGNORE_FILE = REPO_ROOT / ".trivyignore.yaml"
AUDIT = REPO_ROOT / ".github" / "workflows" / "audit.yml"
# A release of trivy by its version and the digest of its multi-platform index.
PINNED_IMAGE = re.compile(r"ghcr\.io/aquasecurity/trivy:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}")
IMAGE = re.search(r"^TRIVY_IMAGE=(\S+)$", SCRIPT.read_text(), re.M)[1]
# The policy of ADR-0446, as the scan of each image passes it to trivy.
POLICY = ["--scanners", "vuln", "--pkg-types", "os,library", "--severity", "HIGH,CRITICAL", "--ignore-unfixed",
          "--ignorefile", "/scan/trivyignore.yaml", "--exit-code", "3", "--format", "table"]


class TestTheJob:
    def test_the_weekly_audit_workflow_builds_and_scans_both_images(self):
        workflow = yaml.safe_load(AUDIT.read_text())
        job = workflow["jobs"]["images"]
        checkout, *steps = job["steps"]

        assert job["name"] == "Image vulnerabilities"
        assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] <= 30
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"] == {"persist-credentials": False}
        assert [step["run"] for step in steps] == ["make image-scan"]
        assert "if" not in job and "continue-on-error" not in job
        # Every Monday and on demand, as the audits.
        assert "workflow_dispatch" in workflow[True] and workflow[True]["schedule"]

    def test_make_image_scan_runs_the_script(self):
        env = {key: value for key, value in os.environ.items() if key not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
        dry_run = subprocess.run(["make", "--no-print-directory", "-n", "image-scan"], cwd=REPO_ROOT,
                                 capture_output=True, text=True, timeout=60, check=True, env=env).stdout

        assert dry_run.splitlines() == ["scripts/image-scan.sh"]
        assert os.access(SCRIPT, os.X_OK)

    def test_trivy_is_pinned_by_the_digest_of_a_release(self):
        text = SCRIPT.read_text()

        assert re.findall(r"^TRIVY_IMAGE=(\S+)$", text, re.M) == [IMAGE]
        assert PINNED_IMAGE.fullmatch(IMAGE), IMAGE
        assert text.count("aquasecurity/trivy") == 1


class TestTheIgnoreFile:
    def test_every_entry_names_a_vulnerability_with_its_reason_and_expiry(self):
        ignored = yaml.safe_load(IGNORE_FILE.read_text())

        assert set(ignored) <= {"vulnerabilities"}
        for entry in ignored.get("vulnerabilities") or []:
            assert set(entry) <= {"id", "statement", "expired_at", "purls", "paths"}, entry
            assert re.fullmatch(r"(CVE-\d{4}-\d{4,}|GHSA(-[0-9a-z]{4}){3})", entry["id"]), entry
            assert len(entry.get("statement", "")) >= 20, entry
            # Only for the package it was judged for.
            assert entry.get("purls") and all(purl.startswith("pkg:") for purl in entry["purls"]), entry
            # An entry ends: trivy reports the vulnerability again after its expiry, at most a year on.
            expiry = entry["expired_at"]
            assert isinstance(expiry, datetime.date), entry
            assert expiry <= datetime.date.today() + datetime.timedelta(days=366), entry


FAKE_DOCKER = """\
#!/bin/bash
# Each call into $CALLS, one line; `image save -o FILE` writes FILE; a scan of an image named in
# $FINDINGS answers $SCAN_STATUS (3, as trivy does for a finding with --exit-code 3), and the
# database download $DB_STATUS.
echo "$*" >> "$CALLS"
if [ "$1 $2" = "image save" ]; then echo archive > "$4"; exit 0; fi
case " $* " in
    *" --download-db-only "*) exit "${DB_STATUS:-0}" ;;
    *" --input /scan/image.tar "*)
        image=$(cat "$CURRENT" 2> /dev/null || true)
        case " ${FINDINGS:-} " in *" $image "*) exit "${SCAN_STATUS:-3}" ;; esac ;;
esac
exit 0
"""


class TestTheScript:
    @pytest.fixture
    def fake(self, tmp_path):
        docker = tmp_path / "docker"
        # The scan of an image follows its save: the fake remembers which one it saved last.
        docker.write_text(FAKE_DOCKER.replace('if [ "$1 $2" = "image save" ]; then',
                                              'if [ "$1 $2" = "image save" ]; then echo "$5" > "$CURRENT";'))
        docker.chmod(0o755)
        return tmp_path

    def _run(self, fake, *images, findings="", db_status=0, scan_status=3):
        calls = fake / "calls"
        result = subprocess.run(
            [str(SCRIPT), *images], capture_output=True, text=True, timeout=60,
            env={**os.environ, "DOCKER": str(fake / "docker"), "CALLS": str(calls), "CURRENT": str(fake / "current"),
                 "TRIVY_CACHE": str(fake / "cache"), "FINDINGS": findings, "DB_STATUS": str(db_status),
                 "SCAN_STATUS": str(scan_status), "TMPDIR": str(fake)})
        return result, calls.read_text().splitlines() if calls.exists() else []

    def test_without_arguments_it_builds_both_images_and_scans_each_offline(self, fake):
        result, calls = self._run(fake)

        assert result.returncode == 0, result.stdout + result.stderr
        build, download, save, scan, save_webui, scan_webui = calls
        # Without the build cache, as a release builds them: the packages of the day.
        assert build == f"compose -f {REPO_ROOT}/deploy/docker-compose.yml --project-name unos-image-scan build --no-cache"
        # The database first, with the network, into the cache; then each image from its archive. As
        # the calling user: without capabilities, root could not write a cache or read an archive of
        # mode 0600 that the user owns on a Linux host.
        user = f"{os.getuid()}:{os.getgid()}"
        assert download == (f"run --rm --user {user} -e HOME=/tmp --tmpfs /tmp --cap-drop ALL --security-opt "
                            f"no-new-privileges -v {fake}/cache:/cache {IMAGE} --cache-dir /cache image "
                            "--download-db-only --no-progress")
        assert save.startswith("image save -o ") and save.endswith(" unos-image-scan-supervisor")
        assert save_webui.endswith(" unos-image-scan-webui")
        for line in (scan, scan_webui):
            args = line.split()
            assert args[:15] == ["run", "--rm", "--network", "none", "--read-only", "--user", user, "-e", "HOME=/tmp",
                                 "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
            assert "/var/run/docker.sock" not in line
            assert f"{REPO_ROOT}/.trivyignore.yaml:/scan/trivyignore.yaml:ro" in args
            trivy = args[args.index(IMAGE) + 1:]
            assert trivy[:5] == ["--cache-dir", "/cache", "image", "--input", "/scan/image.tar"]
            assert {"--skip-db-update", "--offline-scan"} <= set(trivy)
            assert " ".join(POLICY) in " ".join(trivy)
        assert "no fixable HIGH or CRITICAL vulnerability in: unos-image-scan-supervisor unos-image-scan-webui" \
            in result.stdout
        # The archives are removed.
        assert sorted(path.name for path in fake.iterdir()) == ["cache", "calls", "current", "docker"]

    def test_images_given_are_scanned_without_a_build(self, fake):
        result, calls = self._run(fake, "deploy-supervisor")

        assert result.returncode == 0
        assert not [call for call in calls if call.startswith("compose ")]
        assert [call.split()[-1] for call in calls if call.startswith("image save")] == ["deploy-supervisor"]

    def test_a_finding_fails_it_after_every_image_was_scanned(self, fake):
        result, calls = self._run(fake, findings="unos-image-scan-supervisor")

        assert result.returncode == 1
        assert len([call for call in calls if "--input /scan/image.tar" in call]) == 2
        assert "in: unos-image-scan-supervisor\n" in result.stderr
        assert "trivy failed" not in result.stderr

    def test_a_failed_scan_fails_it_and_says_so(self, fake):
        # trivy's own errors exit 1, which is why a finding exits 3.
        result, _ = self._run(fake, findings="unos-image-scan-webui", scan_status=1)

        assert result.returncode == 1
        assert "trivy failed for unos-image-scan-webui (exit 1)" in result.stderr

    def test_without_the_database_nothing_is_scanned(self, fake):
        result, calls = self._run(fake, db_status=1)

        assert result.returncode != 0
        assert not [call for call in calls if "--input" in call]


def test_testing_md_describes_the_scan_and_its_policy():
    testing = (REPO_ROOT / "docs" / "TESTING.md").read_text()

    assert "make image-scan" in testing and ".trivyignore.yaml" in testing and "ADR-0446" in testing
    assert "--ignore-unfixed" in testing and "HIGH" in testing
