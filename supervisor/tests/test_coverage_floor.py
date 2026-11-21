"""make test prints the line coverage of supervisor/src and fails below its floor (ADR-0439).

The command of make test, as make -n prints it after its `cd supervisor`, runs here through the
shell with two workers of pytest-xdist (ADR-0383) in a throwaway project: a package src of seven
statements, four run at its import and one by each of two tests, which wait for each other, so that
each runs in a worker of its own and only the sum of both workers' data makes 85.71%. The project
reads the repository's supervisor/.coveragerc with its fail_under replaced, so that the Makefile's
command and the file's settings are the real ones. The Makefile's wiring, the place of the floor
and the rule that it only rises are in tests/test_repo_tooling.py, which make test-fast runs; these
runs take seconds and stay in make test.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .test_repo_tooling import COVERAGE, COVERAGERC, MAKE_ENV, REPO_ROOT

pytest.importorskip("pytest_cov", reason="make setup installs pytest-cov (the device's test bundle has none)")
pytest.importorskip("xdist", reason="make setup installs pytest-xdist")
if shutil.which("make") is None:
    pytest.skip("make is not installed", allow_module_level=True)

# Four statements run at the import, first() and second() one each, missed() never: 6 of 7, 85.71%.
PART = """\
def first():
    return 1


def second():
    return 2


def missed():
    return 3


VALUE = 4
"""
# Each test waits until the other has started: one worker cannot run both, so the total is the
# sum of the two workers' data (a worker alone has 5 of 7, 71.43%).
TESTS = """\
import os
import time
from pathlib import Path

from src import part

MARKS = Path(__file__).parent


def meet(mine, other):
    assert os.environ["PYTEST_XDIST_WORKER"].startswith("gw")
    (MARKS / mine).touch()
    deadline = time.monotonic() + 120
    while not (MARKS / other).exists():
        assert time.monotonic() < deadline, "the other test did not run in another worker"
        time.sleep(0.05)


def test_first():
    meet("first", "second")
    assert part.first() == 1


def test_second():
    meet("second", "first")
    assert part.second() == 2
"""


def make_test_command(workers: int) -> str:
    """make test's shell command after its `cd supervisor`, with ``workers`` workers."""
    dry_run = subprocess.run(["make", "--no-print-directory", "-n", "test", f"PYTHON={sys.executable}",
                              f"TEST_WORKERS={workers}"], cwd=REPO_ROOT, capture_output=True, text=True,
                             timeout=120, check=True, env=MAKE_ENV).stdout
    [line] = [line for line in dry_run.splitlines() if " -m pytest " in line]
    where, command = line.split(" && ", 1)

    assert where == "cd supervisor"
    assert f"{os.path.abspath(sys.executable)} -m pytest tests/ " in command and COVERAGE in command
    return command


def other_runs_data(project: Path) -> Path:
    """A worker's data file of another run in the same folder, which has every line of part.py."""
    import coverage

    data = coverage.CoverageData(basename=str(project / ".coverage.run1.host.4711.XkqaPmzx"))
    data.add_lines({str(project / "src" / "part.py"): list(range(1, PART.count("\n") + 1))})
    data.write()
    return Path(data.data_filename())


def run_with_floor(project: Path, floor: int) -> subprocess.CompletedProcess:
    rc, replaced = re.subn(r"^fail_under\s*=.*$", f"fail_under = {floor}", COVERAGERC.read_text(), flags=re.M)
    assert replaced == 1
    (project / "src").mkdir(parents=True)
    (project / "src" / "__init__.py").write_text("")
    (project / "src" / "part.py").write_text(PART)
    (project / "tests").mkdir()
    (project / "tests" / "test_part.py").write_text(TESTS)
    (project / ".coveragerc").write_text(rc)
    (project / "pytest.ini").write_text("[pytest]\nasyncio_default_fixture_loop_scope = function\n")
    # Neither this run's workers nor its own coverage reach the inner run.
    env = {key: value for key, value in MAKE_ENV.items() if not key.startswith(("PYTEST_", "COV_", "COVERAGE_"))}
    return subprocess.run(["sh", "-c", make_test_command(2)], cwd=project, capture_output=True, text=True,
                          timeout=300, env=env)


@pytest.mark.parametrize("floor, code, line", [
    (85, 0, "Required test coverage of 85.0% reached. Total coverage: 85.71%"),
    # Two decimals: rounded to a whole percent, 85.71 would reach a floor of 86.
    (86, 1, "FAIL Required test coverage of 86.0% not reached. Total coverage: 85.71%"),
])
def test_make_test_prints_the_coverage_and_fails_below_the_floor(tmp_path, floor, code, line):
    other = other_runs_data(tmp_path)

    result = run_with_floor(tmp_path, floor)

    assert result.returncode == code, result.stdout + result.stderr
    assert line in result.stdout.splitlines()
    assert "2 passed" in result.stdout  # the floor alone fails the run
    # The run kept its data as .coverage and left another run's data alone: not erased, not added.
    assert sorted(path.name for path in tmp_path.glob(".coverage*")) == sorted([".coverage", ".coveragerc", other.name])
