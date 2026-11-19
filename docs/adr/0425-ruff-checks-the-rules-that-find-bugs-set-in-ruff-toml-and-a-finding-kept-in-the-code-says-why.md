---
status: accepted
date: 2026-11-19
area: ci
decided-by: agent
---

# ADR-0425: Ruff checks the rules that find bugs, set in ruff.toml, and a finding kept in the code says why

## Context

`make lint`, and the CI workflow's Lint job through it, ran `ruff check --select F,E9` (ADR-0190,
ADR-0216): pyflakes findings and syntax errors only. ruff 0.17.3 (`.github/lint-requirements.txt`)
has rules that find more defects, such as closures that bind a loop variable late, invalid escape
sequences, tar extraction without a filter or a debugger call left behind, and many more of style.
The feature-list entry `ci.wider-ruff-rules` asked for a wider set of rules that catch bugs, not
style, with the findings fixed first.

## Decision

- The rules live in `ruff.toml` at the root of the repository, where editors and a bare `ruff check`
  find them too. `make lint` runs `ruff check supervisor scripts .claude/hooks` without `--select`,
  and the CI workflow's Lint job runs `make lint` (off on GitHub, ADR-0230). This replaces the
  `--select F,E9` of ADR-0190 and ADR-0216; their other rules stand.
- `target-version = "py311"`, the supervisor's Python, and `per-file-target-version` gives
  `scripts/**` and `.claude/hooks/**` py39, since the workstation's python3 runs them (Python 3.9 or
  later): no rule asks them for a form that Python 3.9 cannot run, and FA102 finds an annotation
  that it cannot evaluate. `line-length = 120`, the width of most of the code: no rule checks it
  (E501 is not selected), it only keeps ruff from offering a fix whose line would be wider.
- Selected, each family but the rules listed after it:
  - F and E9, as before; E4 and E7 (imports, comparisons to None, True and False, bare `except`,
    ambiguous names, a lambda assigned to a name, statements on one line) and W605 (an invalid
    escape sequence, a SyntaxWarning since Python 3.12).
  - B (bugbear), but B904 (`raise ... from` in an `except`: the wording of the traceback) and B905
    (`zip()` without `strict=`: the zips here pair neighbours or sequences of equal or checked
    lengths, and `strict=True` would change what a mismatch does). FastAPI's parameter markers
    (`fastapi.Depends`, `Query`, `Body` and the others) are immutable calls for B008.
  - UP (pyupgrade), for each file's version, but UP031 (printf-style formatting, without an
    automatic fix) and UP042 (`StrEnum` changes `str()` and `format()` of the members). UP finds no
    bugs: it is selected because the entry names it, its fixes are automatic and safe, and with it
    the code keeps one form of each construct of its Python (`X | None`, `datetime.UTC`,
    `TimeoutError`), while the py39 target keeps the tooling runnable.
  - S (bandit), but S101 (`assert`: it narrows types, and Python never runs here with `-O`), S105 to
    S107 (names and messages that contain "password" or "secret"), S108 (temporary paths: `/tmp`,
    the containers' tmpfs, ADR-0171, and the device's `/dev/shm`, ADR-0016), S110 and S112
    (`try`-`except`-`pass` and `continue`: each handler's own choice, not an attack surface), S603
    and S607 (every command is an argv list, ADR-0293). `supervisor/tests/**` is left out of S:
    tests write fixed passwords, modes and SQL of their own.
  - PLE and PLW of pylint, but PLW0108 (a lambda that could be its function: style), PLW0603
    (`global`: the modules' state), PLW1510 (`subprocess.run` without `check=`: the callers read
    `returncode`, but for five commands of `supervisor/src/updates/updater.py`, among them the
    `docker compose build` and `up` of its rollback, which then reports success; checking them
    changes behaviour and is work of its own) and PLW2901 (a loop variable reassigned: `line =
    line.strip()`).
  - RUF, but the rules of style that find something here, RUF005, RUF007, RUF010, RUF015, RUF022,
    RUF023, RUF036 and RUF059, RUF001 to RUF003 (ambiguous Unicode: the Russian texts, ADR-0287),
    RUF012 (mutable class attributes: constants of test classes and `__table_args__`) and RUF043 (a
    dot in `pytest.raises(match=)` that stands for itself). RUF100 reports a `noqa` that suppresses
    nothing, and PGH004 a `noqa` without codes.
  - Single rules: SIM112 (an environment variable in lowercase), SIM115 (a file opened without a
    context manager), SIM220 to SIM223 (`a and not a`, `a or not a`, `... or True`, `... and
    False`), T10 (a debugger call), LOG (the logging module used wrongly, ADR-0398), FA102, PIE794
    (a class field defined twice) and PIE796 (an enum with duplicate values).
- Not selected: the rest of pycodestyle, E501 included, and of SIM and PIE; flake8-comprehensions,
  PERF, PT, RET, TRY, PLC and PLR (style and complexity); BLE (151 `except Exception` and
  `BaseException`, each a choice of its own); A and N (names); DTZ (the database keeps naive UTC
  times by design, `src/times.py`); ASYNC (51 findings in async functions, most of them blocking
  calls, others busy waits and timeout parameters: moving the calls off the event loop, ADR-0021,
  changes behaviour and is work of its own).
- A finding that this change keeps in the code carries `# noqa: <code>` and its reason on the line
  (the older `noqa` of F401, F811 and E402 keep their form), or a per-file ignore with its reason in
  `ruff.toml`: E402 in `supervisor/src/main.py`, which pins `tempfile` and configures logging before
  the other imports (ADR-0171, ADR-0398).
- The findings were fixed first, without a change of behaviour: ruff's safe fixes (670 findings in
  102 files), then the other 64 by hand (unused loop variables, the name `l`, `global` statements of
  names only read, implicit `Optional`s, the `noqa` codes of rules that are not selected, whose
  reasons stay as comments) or kept with `noqa` and the reason (closures called in the iteration
  that made them, URLs that the scripts build, SQL names of the code's own, SQLAlchemy's `== False`,
  systemd's lowercase `container`).
- `test_ruff_finds_bugs_beyond_pyflakes` of `supervisor/tests/test_repo_tooling.py` runs ruff with
  `ruff.toml` over one defect of each kind (B006, B023, W605, E402, E711, UP017, S202, S602,
  PLW0127, RUF006, RUF100, SIM115, T100, LOG015), none of S in `supervisor/tests/` nor of E402 in
  `supervisor/src/main.py`, FA102 in `scripts/` and `.claude/hooks/` only, and checks that `make
  lint` passes no `--select`; it skips without ruff or with another version than the pin; it and
  `supervisor/tests/test_deploy_scripts.py` check make lint's command.

## Consequences

- A finding of these rules fails `make check`, the pre-commit hook and the Stop hook, and CI's Lint
  job once the workflow runs again. `ruff check --fix supervisor scripts .claude/hooks` applies the
  safe fixes; the rest is fixed by hand or kept with a reason.
- A raise of `target-version` makes UP rewrite the code again, in one commit of safe fixes.
- A ruff update can bring new rules into a selected family: their findings fail make lint until they
  are fixed or left out here, with the reason.
- A `noqa` of a rule that is not selected fails (RUF100): it suppresses nothing and only looks like
  a reason.
- ruff takes, for each file, the nearest `ruff.toml` or `pyproject.toml` with a `[tool.ruff]` table
  from the file's folder up; the repository has no other, and one added below the root would replace
  these rules for its folder.
- Not seen on GitHub: the workflow stays off (ADR-0230), so the Lint job's failure on a finding is
  proven only by `make lint` on the workstation.

## Considered options

- The selection on make's command line, as before: the CI job and editors would need it repeated,
  and per-file settings cannot be given there.
- `pyproject.toml`: the repository has no Python project at its root.
