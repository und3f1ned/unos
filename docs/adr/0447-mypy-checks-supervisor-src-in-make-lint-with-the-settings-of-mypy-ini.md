---
status: accepted
date: 2026-11-21
area: ci
decided-by: agent
---

# ADR-0447: Mypy checks supervisor/src in make lint, with the settings of mypy.ini, against the supervisor's environment

## Context

The supervisor has type hints and Pydantic models (ADR-0012), but nothing checked them: `make lint`
ran ruff, `bash -n` and the JSON check only (ADR-0425). The feature-list entry
`ci.python-type-check` asks for mypy or pyright on `supervisor/src` in `make lint`, with its settings
in the repository, and for CI's failure on a type error. The owner left the packages ("do it
yourself") and the open choices ("decide yourself") to the agent.

## Decision

- mypy, pinned in `.github/lint-requirements.in` (2.4.1) and with every package it depends on in
  `.github/lint-requirements.txt`, with their hashes (ADR-0423): `make setup` installs it into
  `.venv-lint` with ruff and pip-audit. mypy, not pyright: a Python wheel among the hashed lint
  tools, without Node.js in the Lint job.
- `make lint` runs `lint-mypy`, `$(MYPY) --python-executable $(PYTHON) supervisor/src` (`MYPY`
  defaults to `.venv-lint/bin/mypy`), beside ruff, `bash -n` and the JSON check, each also when
  another one fails (ADR-0190). The interpreter is the supervisor's environment, `supervisor/.venv`
  from `make setup`: mypy reads the types of FastAPI, Pydantic, SQLAlchemy and the others there. The
  runtime dependencies without type information get their stubs pinned in
  `supervisor/requirements-dev.txt`: `types-croniter`, `types-docker`, `types-psutil`.
- The CI workflow's Lint job installs `supervisor/requirements-dev.txt`, which has no hashes, into a
  venv of its own, `supervisor/.venv`, after the hashed tools and after the audit, so that none of
  its packages runs in the tools' Python (ADR-0423); `make lint` takes that venv as `PYTHON`. CI is
  off on GitHub (ADR-0230): that a type error
  fails the job is shown by `make lint` on the workstation and by the tests that keep the job and
  the Makefile in step, not by a run on GitHub.
- The settings are in `mypy.ini` at the root of the repository: Python 3.11 on Linux (the
  supervisor's container: `os.O_PATH` and `psutil.sensors_temperatures` exist), the package `src`
  below `supervisor/` without an `__init__.py` (`mypy_path`, `explicit_package_bases`),
  `check_untyped_defs` (the bodies of functions without annotations are checked too),
  `strict_equality`, `warn_redundant_casts`, `warn_unused_ignores` and the error code
  `ignore-without-code`. Not `strict`: `disallow_untyped_defs` and its kin would ask for
  annotations on about 780 functions, work of its own. No mypy plugin: the Pydantic plugin would
  need Pydantic in `.venv-lint`; the models declare their fields' defaults as `Field(default=...)`,
  which the standard `dataclass_transform` of Pydantic understands.
- The code meets them after its type errors were fixed (about 3,700 at first, 414 with the
  settings above once the models were declared typed), without a change of behaviour: the 491
  `Field(<value>, ...)` became `Field(default=<value>, ...)`; the
  SQLAlchemy models declare `Mapped[...]` with `mapped_column`, with the nullability that `Column`
  gave each column, so `create_all` makes the same tables (ADR-0319); a `Result`'s row count goes
  through `database.rowcount`; the validators of names and uids are `TypeGuard`s; an invariant
  that mypy cannot see is an `assert` with its reason (ruff's S101 is off for that, ADR-0425), but
  in `shares/accounts.py`, which has none (`tests/test_smb_accounts.py`); reused loop variables of
  other types got names of their own. Two paths changed where the old code would have failed on a
  value that its callers never give: `_keep_caller_session` of the backup router returns without
  a database file instead of handing sqlite3 a None, and `_image_label` of
  `containers/docker.py` reads a container's image once, not twice.
- No module is exempted. A line that keeps an error has `# type: ignore[<code>]` and its reason:
  the stubs of typeshed or a library that are narrower than the code's use (tarfile's private
  `_proc_member`, `mtime=None` and stream mode, Docker's restart policies, Starlette's exception
  handlers, mypy's `@computed_field` above `@property`), the view of the audit log whose
  timestamp is a time where the line's is a text, and the balance commands' answers. An exemption
  of a module later goes into `mypy.ini` as its own section, with its reason.
- `test_mypy_checks_the_supervisor_with_the_settings_of_mypy_ini` of
  `supervisor/tests/test_repo_tooling.py` runs the pinned mypy with `mypy.ini` over one error of
  each kind that the settings find (an Optional used as a value, an untyped body, a comparison
  that cannot hold, a redundant cast, an unused ignore, an ignore without its code) and over code
  that needs the settings (the package without `__init__.py`, Pydantic from the given Python,
  Linux's `os`), and checks `make lint`'s command; `test_deploy_scripts.py` checks the Lint job's
  installs and the pins. The Stop hook skips its run, with a warning, when mypy is missing, as for
  ruff (ADR-0216), and a `MYPY` or `MYPYPATH` from the environment records no green run (ADR-0383).

## Consequences

- A type error in `supervisor/src` fails `make check`, `make check-fast`, the pre-commit hook and
  the Stop hook, and the Lint job once the workflow runs again.
- New code types its models with `Mapped[...]` and `Field(default=...)`; a column added to an
  existing model keeps the nullability of the table (ADR-0319).
- A new mypy can find errors that the old one did not: its update fixes them in the same change
  (docs/TESTING.md, "Updating the lint tools"). A runtime dependency without type information gets
  its stubs in `supervisor/requirements-dev.txt`, which Dependabot moves with it.
- `make lint` needs `supervisor/.venv` (or `PYTHON=` with the supervisor's packages) besides
  `.venv-lint`; mypy keeps its cache in `.mypy_cache/` at the root, git-ignored.
- The tests, `scripts/` and `.claude/hooks/` stay outside the check: they run on the workstation's
  Python 3.9 or later, and the tests' fixtures are untyped by design.

## Considered options

- pyright: it needs Node.js in the Lint job and an npm package outside the hashed lint tools.
- mypy in `supervisor/.venv` from `requirements-dev.txt`: the tools of `make lint` come from
  `.venv-lint` with their hashes (ADR-0423).
- Exempting the modules with errors: it would have left most of `shares/`, `auth/` and `backup/`
  unchecked, where the errors were.
