"""The secret scan of the git history (ADR-0439): the Secret Scan job of the CI workflow and make
secret-scan run scripts/secret-scan.sh, which runs gitleaks, pinned by the digest of its image,
over every commit with the rules and the allowlist of .gitleaks.toml, and
scripts/published-passwords.py, which finds the passwords that CLAUDE.md once published by their
SHA-256.

The job and the allowlists are read as files; the script runs in throwaway repositories with a
fake docker that records its arguments and answers as gitleaks does, and, where the pinned image
is already pulled (make secret-scan pulls it), with the real gitleaks. No test pulls an image or
needs the network, and none holds a secret-shaped line: the shapes are put together at run time,
as in test_repo_tooling.py.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "secret-scan.sh"
PUBLISHED_SCRIPT = REPO_ROOT / "scripts" / "published-passwords.py"
CONFIG = REPO_ROOT / ".gitleaks.toml"
TEMPLATE = REPO_ROOT / "scripts" / "secret-scan.tmpl"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
SCAN_FILES = (SCRIPT, PUBLISHED_SCRIPT, TEMPLATE, CONFIG)
# A release of gitleaks by its tag and the digest of its multi-platform index.
PINNED_IMAGE = re.compile(r"ghcr\.io/gitleaks/gitleaks:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}")
IMAGE = re.search(r"^GITLEAKS_IMAGE=(\S+)$", SCRIPT.read_text(), re.M)[1]
COMMIT = re.compile(r"[0-9a-f]{40}")
# The commit of the old CLAUDE.md that published the dev device's root password and the UNOS
# administrator's, both rotated (ADR-0226).
PUBLISHED_IN = "da193b8b0c12fc431a2675234ede1c2ebf7611e3"


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PUBLISHED = _module("published_passwords", PUBLISHED_SCRIPT)


def _git(*args, cwd=REPO_ROOT, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120, env=env)


def _with_history():
    """Skips a test that reads this repository's history where there is none of it."""
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() != "false":
        pytest.skip("needs the repository's whole history (the CI checkouts have fetch-depth: 0)")


def _config():
    return tomllib.loads(CONFIG.read_text())


def _rule(rule_id):
    return next(rule for rule in _config()["rules"] if rule["id"] == rule_id)


def _finds(rule_id, line):
    """The secret that a rule of .gitleaks.toml takes from a line, or None: its regex and its
    allowlists as gitleaks applies them (Python's re reads these patterns as RE2 does)."""
    rule = _rule(rule_id)
    match = re.search(rule["regex"], line)
    if not match:
        return None
    for allowlist in rule.get("allowlists", []):
        target = match.group(0) if allowlist.get("regexTarget") == "match" else match.group(rule["secretGroup"])
        if any(re.search(pattern, target) for pattern in allowlist["regexes"]):
            return None
    return match.group(rule["secretGroup"])


def _isolated_env(home):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="t",
               GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return env


def _repository(tmp_path):
    """A repository with the scan's files in their places, committed."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for path in SCAN_FILES:
        shutil.copy2(path, repo / path.relative_to(REPO_ROOT))
    env = _isolated_env(tmp_path)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "base"]):
        assert _git(*args, cwd=repo, env=env).returncode == 0, args
    return repo, env


def _commit(repo, env, message, files=None, *git_steps):
    for name, text in (files or {}).items():
        path = repo / name
        if text is None:
            path.unlink()
        elif isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text)
    for args in (*git_steps, ["add", "-A"], ["commit", "-q", "--allow-empty", "-m", message]):
        result = _git(*args, cwd=repo, env=env)
        assert result.returncode == 0, (args, result.stderr)
    return _git("rev-parse", "HEAD", cwd=repo, env=env).stdout.strip()


def _merge_with_resolution(repo, env, resolution):
    """A merge whose conflict was resolved with `resolution` in conflict.txt: what the merge itself adds."""
    _commit(repo, env, "main side", {"conflict.txt": "main\n"})
    assert _git("checkout", "-qb", "side", "HEAD~1", cwd=repo, env=env).returncode == 0
    _commit(repo, env, "other side", {"conflict.txt": "side\n"})
    assert _git("checkout", "-q", "main", cwd=repo, env=env).returncode == 0
    assert _git("merge", "-q", "side", cwd=repo, env=env).returncode != 0  # the conflict
    return _commit(repo, env, "merge", {"conflict.txt": resolution})


class TestTheJob:
    def test_the_ci_workflow_scans_the_whole_history_with_make_secret_scan(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        job = workflow["jobs"]["secrets"]
        checkout, *steps = job["steps"]

        assert job["name"] == "Secret Scan"
        assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] <= 15
        # Every commit: a shallow checkout would hold only the last one, and the script refuses it.
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
        assert [step["run"] for step in steps] == ["make secret-scan"]
        assert "if" not in job and "continue-on-error" not in job
        # On the pushes to main and the pull requests of the workflow, as the other jobs.
        assert set(workflow[True]) == {"push", "pull_request"}

    def test_make_secret_scan_runs_the_script(self):
        env = {key: value for key, value in os.environ.items() if key not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
        dry_run = subprocess.run(["make", "--no-print-directory", "-n", "secret-scan"], cwd=REPO_ROOT,
                                 capture_output=True, text=True, timeout=60, check=True, env=env).stdout

        assert dry_run.splitlines() == ["scripts/secret-scan.sh"]
        assert os.access(SCRIPT, os.X_OK)

    def test_gitleaks_is_pinned_by_the_digest_of_a_release(self):
        text = SCRIPT.read_text()

        assert re.findall(r"^GITLEAKS_IMAGE=(\S+)$", text, re.M) == [IMAGE]
        assert PINNED_IMAGE.fullmatch(IMAGE), IMAGE
        assert text.count("gitleaks/gitleaks") == 1
        assert '"$GITLEAKS_IMAGE" git ' in text

    def test_the_findings_show_no_value(self):
        # A finding is its rule, file, commit and line: never .Secret, .Match or .Line.
        assert TEMPLATE.read_text() == "{{range .}}{{.RuleID}}\t{{.File}}\t{{.Commit}}\t{{.StartLine}}\n{{end}}"


class TestTheRules:
    def test_the_default_rules_and_two_of_the_repositorys_own(self):
        config = _config()

        assert config["extend"] == {"useDefault": True}
        assert [rule["id"] for rule in config["rules"]] == ["sshpass-password", "secret-assignment"]

    @pytest.mark.parametrize("line", [
        "SSH=\"ssh" + "pass -p 'Kq7-w9Pz' ssh root@nas\"",
        "ssh" + "pass -p \"Kq7-w9Pz\" scp a b",
        "ssh" + "pass -p Kq7-w9Pz ssh root@nas",
    ])
    def test_a_password_on_sshpasss_command_line_is_found(self, line):
        assert _finds("sshpass-password", line) is not None

    @pytest.mark.parametrize("line", [
        "ssh" + "pass -p \"$UNOS_DEV_SSH_PASSWORD\" ssh root@nas",
        "ssh" + "pass -p <password> ssh root@nas",
        "`ssh" + "pass -p` with a value",
        "ssh" + "pass -e ssh root@nas",
    ])
    def test_a_variable_or_a_placeholder_after_sshpass_is_not(self, line):
        assert _finds("sshpass-password", line) is None

    @pytest.mark.parametrize("line", [
        "UNOS_DEV_ROOT_PASS" + "WORD=Kq7-w9Pz",
        "export TRANSMISSION_PA" + "SS='Kq7 w9Pz'",
        "JWT_SEC" + "RET=\"Kq7-w9Pz\"",
        "TS_AUTH" + "KEY=tskey-auth-Kq7w9Pz",
        # The names of the guard's rule, bare ones too (_is_secret_name of .claude/hooks/guard.py).
        "PASS" + "WORD=Kq7-w9Pz",
        "SSHPA" + "SS='Kq7-w9Pz' ssh -e root@nas",
        "TOK" + "EN=Kq7-w9Pz",
        "SEC" + "RET=Kq7-w9Pz",
        "BACKUP_PASS" + "PHRASE=Kq7-w9Pz",
        "OPENAI_API" + "KEY=Kq7-w9Pz",
        "AWS_SECRET_ACCESS_" + "KEY=Kq7-w9Pz",
        "SMB_AUTH_" + "KEY=Kq7-w9Pz",
    ])
    def test_a_literal_assigned_to_a_secrets_name_is_found(self, line):
        assert _finds("secret-assignment", line) is not None

    @pytest.mark.parametrize("line", [
        "UNOS_DEV_ROOT_PASS" + "WORD=",
        "UNOS_DEV_ROOT_PASS" + "WORD=$(cat file)",
        "UNOS_DEV_ROOT_PASS" + "WORD=\"${VALUE}\"",
        "UNOS_DEV_ROOT_PASS" + "WORD=<the password>",
        "PASSWORD_LENGTH=16",
        "SSH_ASKPASS=/usr/bin/true",
        "CACHE_BYPASS=always",
    ])
    def test_a_variable_an_empty_value_or_another_name_is_not(self, line):
        assert _finds("secret-assignment", line) is None


class TestTheAllowlists:
    def test_gitleaks_allows_commits_and_paths_with_a_reason_never_values(self):
        config = _config()
        blocks = config["allowlists"]

        # No allowlist of the old form, and in the rules only the placeholders and the PASS words.
        assert "allowlist" not in config
        assert [sorted(allowlist) for rule in config["rules"] for allowlist in rule.get("allowlists", [])] == [
            ["description", "regexes"], ["description", "regexes"], ["description", "regexTarget", "regexes"]]
        assert blocks
        for block in blocks:
            # Both must match (AND): one commit, and the paths of its findings, each a whole path.
            assert set(block) == {"description", "condition", "commits", "paths"}, block
            assert block["condition"] == "AND", block
            assert len(block["commits"]) == 1 and COMMIT.fullmatch(block["commits"][0]), block
            assert block["paths"] and all(path.startswith("^") and path.endswith("$") for path in block["paths"]), block
            assert len(block["description"]) >= 20, block
        commits = [block["commits"][0] for block in blocks]
        assert len(commits) == len(set(commits))

    def test_every_allowed_commit_is_in_the_history_and_changes_its_paths(self):
        _with_history()
        allowed = [(block["commits"][0], block["paths"]) for block in _config()["allowlists"]]
        allowed += [(commit, [f"^{re.escape(path)}$" for path in paths]) for commit, paths in PUBLISHED.ALLOWED.items()]
        for commit, paths in allowed:
            assert _git("merge-base", "--is-ancestor", commit, "HEAD").returncode == 0, commit
            changed = _git("show", "--no-renames", "--name-only", "--format=", commit).stdout.splitlines()
            assert [path for path in paths if not any(re.search(path, name) for name in changed)] == [], commit

    def test_the_rotated_passwords_are_allowed_where_claude_md_published_them(self):
        blocks = [block for block in _config()["allowlists"] if PUBLISHED_IN in block["commits"]]

        assert len(blocks) == 1
        assert blocks[0]["paths"] == [r"^CLAUDE\.md$"]
        assert "ADR-0226" in blocks[0]["description"] and "ADR-0208" in blocks[0]["description"]
        assert not [block for block in _config()["allowlists"]
                    if any(re.search(path, "CLAUDE.md") for path in block["paths"]) and block not in blocks]
        assert PUBLISHED.ALLOWED[PUBLISHED_IN] == ("CLAUDE.md",)


class TestPublishedPasswords:
    """scripts/published-passwords.py, with a made-up word in place of the published passwords."""

    WORD = "Wq7Published"
    DIGESTS = {hashlib.sha256(WORD.encode()).hexdigest()}

    def test_it_keeps_the_two_digests_only(self):
        assert len(PUBLISHED.DIGESTS) == 2
        assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in PUBLISHED.DIGESTS)

    def test_a_commit_that_adds_one_is_found_by_commit_path_and_line(self, tmp_path):
        repo, env = _repository(tmp_path)
        commit = _commit(repo, env, "add", {"notes.md": f"one\nthe login is admin / {self.WORD}\n"})
        _commit(repo, env, "remove", {"notes.md": "one\n"})

        commits, findings = PUBLISHED.history(str(repo), self.DIGESTS, {})

        assert commits == 3
        assert findings == [f"{commit} notes.md:2"]
        assert self.WORD not in " ".join(findings)

    def test_an_allowed_commit_and_path_pass_and_the_same_commit_elsewhere_does_not(self, tmp_path):
        repo, env = _repository(tmp_path)
        commit = _commit(repo, env, "add", {"a.md": f"{self.WORD}\n", "b.md": f"{self.WORD}\n"})

        assert PUBLISHED.history(str(repo), self.DIGESTS, {commit: ("a.md",)})[1] == [f"{commit} b.md:1"]
        assert PUBLISHED.history(str(repo), self.DIGESTS, {commit: ("a.md", "b.md")})[1] == []

    def test_a_merge_that_adds_one_in_its_resolution_is_found_without_writing_the_repository(self, tmp_path):
        repo, env = _repository(tmp_path)
        merge = _merge_with_resolution(repo, env, f"both\n{self.WORD}\n")
        objects = sorted(path.name for path in (repo / ".git" / "objects").iterdir())

        assert PUBLISHED.history(str(repo), self.DIGESTS, {})[1] == [f"{merge} conflict.txt:2"]
        assert sorted(path.name for path in (repo / ".git" / "objects").iterdir()) == objects

    def test_the_script_fails_for_a_finding_and_names_no_value(self, tmp_path):
        repo, env = _repository(tmp_path)
        script = tmp_path / "published.py"
        script.write_text(PUBLISHED_SCRIPT.read_text().replace(sorted(PUBLISHED.DIGESTS)[0], *self.DIGESTS))
        commit = _commit(repo, env, "add", {"notes.md": f"{self.WORD}\n"})

        result = subprocess.run([sys.executable, "-I", str(script), str(repo)], capture_output=True, text=True,
                                timeout=60, env=env)

        assert result.returncode == 1
        assert f"  {commit} notes.md:1\n" in result.stderr
        assert self.WORD not in result.stdout + result.stderr

    def test_the_working_tree_scan_uses_the_same_digests(self):
        tooling = (REPO_ROOT / "supervisor" / "tests" / "test_repo_tooling.py").read_text()

        assert "PUBLISHED_PASSWORD_DIGESTS = PUBLISHED.DIGESTS" in tooling
        assert not re.search(r"\b[0-9a-f]{64}\b", tooling.split("PUBLISHED_PASSWORD_DIGESTS =", 1)[1][:200])


FAKE_DOCKER = """\
#!/bin/bash
# gitleaks as the scan runs it: the arguments into $ARGS, its log on stderr, findings on stdout.
printf '%s\\n' "$@" > "$ARGS"
printf '%b' "${LOG:-}" >&2
echo "1:02PM INF ${SCANNED} commits scanned." >&2
printf '%b' "${FINDINGS:-}"
exit "${STATUS:-0}"
"""
FAKE_PYTHON = """\
#!/bin/bash
printf '%s\\n' "$@" > "$PYTHON_ARGS"
exit "${PUBLISHED_STATUS:-0}"
"""


class TestTheScript:
    @pytest.fixture
    def repo(self, tmp_path):
        repo, env = _repository(tmp_path)
        for name, body in (("docker", FAKE_DOCKER), ("python", FAKE_PYTHON)):
            (tmp_path / name).write_text(body)
            (tmp_path / name).chmod(0o755)
        return repo, env, tmp_path

    def _run(self, repo, scanned=3, status=0, findings="", log="", published=0):
        repo, env, fakes = repo
        args, python_args = fakes / "args", fakes / "python-args"
        result = subprocess.run(
            ["scripts/secret-scan.sh"], cwd=repo, capture_output=True, text=True, timeout=60,
            env={**env, "DOCKER": str(fakes / "docker"), "PYTHON": str(fakes / "python"), "ARGS": str(args),
                 "PYTHON_ARGS": str(python_args), "SCANNED": str(scanned), "STATUS": str(status),
                 "FINDINGS": findings, "LOG": log, "PUBLISHED_STATUS": str(published)})
        read = lambda path: path.read_text().splitlines() if path.exists() else []  # noqa: E731
        return result, read(args), read(python_args)

    def test_it_runs_gitleaks_over_every_commit_without_the_network_and_redacted(self, repo):
        result, args, python_args = self._run(repo)
        root = repo[0].resolve()
        head = _git("rev-parse", "HEAD", cwd=repo[0], env=repo[1]).stdout.strip()

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"secret-scan: gitleaks found no secret in the 3 commits of {head}\n" in result.stdout
        assert args[:10] == ["run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                             "--security-opt", "no-new-privileges", "--tmpfs"]
        mounts = [args[index + 1] for index, arg in enumerate(args) if arg == "-v"]
        assert mounts == [f"{root}/.git:/repo.git:ro", f"{root}/.gitleaks.toml:/scan/gitleaks.toml:ro",
                          f"{root}/scripts/secret-scan.tmpl:/scan/findings.tmpl:ro"]
        # The objects of the merges that remerge-diff makes go to the tmpfs, not into the repository.
        environment = [args[index + 1] for index, arg in enumerate(args) if arg == "-e"]
        assert environment[:2] == ["GIT_OBJECT_DIRECTORY=/tmp", "GIT_ALTERNATE_OBJECT_DIRECTORIES=/repo.git/objects"]
        assert args[args.index("--tmpfs") + 1] == "/tmp"
        assert args[args.index(IMAGE) + 1:] == [
            "git", "--config", "/scan/gitleaks.toml", f"--log-opts=--remerge-diff {head}", "--redact",
            "--ignore-gitleaks-allow", "--no-banner", "--no-color", "--report-format", "template",
            "--report-template", "/scan/findings.tmpl", "--report-path", "-", "/repo.git"]
        assert python_args == ["-I", f"{root}/scripts/published-passwords.py", str(root)]

    @pytest.mark.parametrize("scanned, log", [
        # gitleaks answers no leaks for a repository it cannot read, and logs git's error.
        (0, "1:02PM ERR [git] fatal: not a git repository\\n"),
        (0, ""),
        (3, "1:02PM ERR [git] error: unable to create temporary object directory\\n"),
    ])
    def test_a_git_error_or_no_commit_scanned_fails_it(self, repo, scanned, log):
        result, _, _ = self._run(repo, scanned=scanned, log=log)

        assert result.returncode == 2
        assert "gitleaks failed" in result.stderr

    def test_a_finding_fails_it_and_names_its_rule_file_and_commit(self, repo):
        commit = "a" * 40
        result, _, _ = self._run(repo, status=1, findings=f"sshpass-password\\tnotes.sh\\t{commit}\\t3\\n")

        assert result.returncode == 1
        assert f"  sshpass-password  notes.sh  {commit}:3\n" in result.stderr
        assert "dev.env" in result.stderr and ".gitleaks.toml" in result.stderr

    @pytest.mark.parametrize("status", [2, 126])
    def test_gitleaks_failing_fails_it(self, repo, status):
        assert self._run(repo, status=status)[0].returncode == 2

    @pytest.mark.parametrize("published, expected", [(1, 1), (2, 2)])
    def test_a_published_password_fails_it_after_a_clean_gitleaks_run(self, repo, published, expected):
        result, args, python_args = self._run(repo, published=published)

        assert result.returncode == expected
        assert args and python_args

    def test_both_checks_run_when_gitleaks_fails(self, repo):
        result, _, python_args = self._run(repo, scanned=0)

        assert result.returncode == 2 and python_args

    def test_a_shallow_clone_is_refused(self, repo, tmp_path):
        shallow = tmp_path / "shallow"
        _commit(repo[0], repo[1], "second", {"second.txt": "2\n"})
        cloned = _git("clone", "-q", "--depth", "1", f"file://{repo[0]}", str(shallow), cwd=tmp_path, env=repo[1])
        assert cloned.returncode == 0, cloned.stderr

        result, args, python_args = self._run((shallow, repo[1], repo[2]))

        assert result.returncode == 2 and "shallow" in result.stderr
        assert args == [] and python_args == []


def _pinned_image_present():
    docker = shutil.which("docker")
    return bool(docker) and subprocess.run([docker, "image", "inspect", IMAGE], capture_output=True,
                                           timeout=60).returncode == 0


@pytest.mark.skipif(not _pinned_image_present(), reason="the pinned gitleaks image is not pulled (make secret-scan pulls it)")
class TestWithTheRealGitleaks:
    """The script with the pinned gitleaks and .gitleaks.toml, on made-up values (step 2 of the entry)."""

    VALUES = {"dev.env.copy": "UNOS_DEV_ROOT_PASS" + "WORD=Xk3Rt7Wq9Zp2  # gitleaks:allow\n",
              "notes.sh": "ssh" + "pass -p 'Zt5nRq8wYc' ssh root@nas\n"}

    def _scan(self, repo, env, config=None):
        if config is not None:
            (repo / ".gitleaks.toml").write_text(config)
        return subprocess.run(["scripts/secret-scan.sh"], cwd=repo, capture_output=True, text=True, timeout=300,
                              env={**env, "PYTHON": sys.executable})

    def test_a_commit_that_adds_a_credential_fails_it_and_an_allowed_one_passes(self, tmp_path):
        repo, env = _repository(tmp_path)
        # Commits that gitleaks reads but finds nothing to scan in: a deletion, a rename, a mode, a binary.
        _commit(repo, env, "files", {"gone.txt": "x\n", "moved.txt": "y\n"})
        _commit(repo, env, "delete", {"gone.txt": None})
        _commit(repo, env, "rename", None, ["mv", "moved.txt", "renamed.txt"])
        (repo / "renamed.txt").chmod(0o755)
        _commit(repo, env, "mode")
        _commit(repo, env, "binary", {"blob.bin": b"\x00\x01\x02\x03"})
        clean = self._scan(repo, env)
        assert clean.returncode == 0, clean.stdout + clean.stderr

        commit = _commit(repo, env, "leak", self.VALUES)
        leaked = self._scan(repo, env)
        output = leaked.stdout + leaked.stderr

        assert leaked.returncode == 1, output
        # The inline gitleaks:allow does not silence it, and no value is printed.
        assert f"  secret-assignment  dev.env.copy  {commit}:1\n" in leaked.stderr
        assert f"  sshpass-password  notes.sh  {commit}:1\n" in leaked.stderr
        assert "Xk3Rt7Wq9Zp2" not in output and "Zt5nRq8wYc" not in output

        # A block of the allowlist lets only its commit's findings in its paths pass.
        block = f'\n[[allowlists]]\ndescription = "made up"\ncondition = "AND"\ncommits = ["{commit}"]\n'
        partly = self._scan(repo, env, CONFIG.read_text() + block + "paths = ['''^notes\\.sh$''']\n")
        assert partly.returncode == 1 and "dev.env.copy" in partly.stderr and "notes.sh" not in partly.stderr
        allowed = self._scan(repo, env, CONFIG.read_text() + block +
                             "paths = ['''^notes\\.sh$''', '''^dev\\.env\\.copy$''']\n")
        assert allowed.returncode == 0, allowed.stdout + allowed.stderr

    def test_a_credential_added_in_a_merges_resolution_fails_it(self, tmp_path):
        repo, env = _repository(tmp_path)
        merge = _merge_with_resolution(repo, env, "both\n" + "SSHPA" + "SS='Zt5nRq8wYc'\n")

        result = self._scan(repo, env)

        assert result.returncode == 1, result.stdout + result.stderr
        # gitleaks keeps git's b/ before a path of remerge-diff that has a conflict line in its header.
        assert f"  secret-assignment  b/conflict.txt  {merge}:2\n" in result.stderr
        assert "Zt5nRq8wYc" not in result.stdout + result.stderr


def test_testing_md_describes_the_scan():
    testing = (REPO_ROOT / "docs" / "TESTING.md").read_text()

    assert "make secret-scan" in testing and ".gitleaks.toml" in testing and "ADR-0439" in testing
    assert "scripts/published-passwords.py" in testing
