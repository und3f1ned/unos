# UNOS developer commands. Run them from the repository root; `make help` lists the targets.
# make check is the quality gate (ADR-0216): CI's Unit Tests, Lint and Frontend jobs run the same
# targets (.github/workflows/test.yml). make setup bootstraps a checkout (ADR-0217).
# docs/TESTING.md explains the targets and the three verification levels.

# Tools. The defaults are what make setup installs; override them on the command line, e.g.
# make test PYTHON=/path/to/python3.11.
PYTHON ?= $(if $(wildcard supervisor/.venv/bin/python),supervisor/.venv/bin/python,python3)
RUFF ?= $(if $(wildcard .venv-lint/bin/ruff),.venv-lint/bin/ruff,ruff)
MYPY ?= $(if $(wildcard .venv-lint/bin/mypy),.venv-lint/bin/mypy,mypy)
PIP_AUDIT ?= $(if $(wildcard .venv-lint/bin/pip-audit),.venv-lint/bin/pip-audit,pip-audit)
NPM ?= npm

# What make test runs (paths relative to supervisor/), and extra pytest arguments:
# make test TESTS=tests/test_smbconf.py PYTEST_ARGS='-x -k render'.
TESTS ?= tests/
PYTEST_ARGS ?=
# make test and make test-fast run the tests in TEST_WORKERS processes of pytest-xdist (auto: one
# per CPU core; 0: in pytest's own process), each with temporary paths of its own
# (supervisor/tests/conftest.py, ADR-0383). A Python without pytest-xdist runs them in one process,
# with a line that says so: make setup installs it. TEST_WORKERS goes into pytest's command line, so
# make refuses any other value than auto or a number.
TEST_WORKERS ?= auto
without = $(if $(2),$(call without,$(subst $(firstword $(2)),,$(1)),$(wordlist 2,10,$(2))),$(1))
workers_valid = $(and $(filter 1,$(words $(TEST_WORKERS))),$(or $(filter auto,$(TEST_WORKERS)),$(if \
	$(call without,$(TEST_WORKERS),0 1 2 3 4 5 6 7 8 9),,yes)))
$(if $(workers_valid),,$(error TEST_WORKERS is auto or a number of workers))
# The repository-convention tests: make test-fast and the pre-commit hook. test_repo_tooling.py
# also scans the repository's text files for secrets.
TEST_FAST = tests/test_adrs.py tests/test_feature_list.py tests/test_agent_guard.py \
	tests/test_repo_tooling.py tests/test_dev_scripts.py tests/test_dev_scenarios.py tests/test_standin.py
# make smoke: existing backend tests that start the app and serve requests through it, in a few
# seconds: the startup and /api/v1/health, the first-run setup, a login through the proxy layer
# and the refusal of anonymous requests. The baseline of make setup and of a session.
SMOKE = tests/test_lifespan.py tests/test_auth.py::TestFirstRunSetup \
	tests/test_authz.py::TestRealClientAddress \
	tests/test_authz.py::TestRouteMatrix::test_anonymous_requests_are_rejected
# Arguments for scripts/standin/e2e.sh: make e2e-local E2E_ARGS='tests/auth.spec.ts'.
E2E_ARGS ?=

SHELL_SCRIPT_DIRS = deploy scripts .claude/hooks
# The Python that make lint checks with ruff, with the rules of ruff.toml (ADR-0425): the supervisor
# and the repository's own tooling (the guard, scripts/dev, the stand-in, the commit checker).
PYTHON_DIRS = supervisor scripts .claude/hooks
# The Python that make lint type-checks with mypy, with the settings of mypy.ini, against the
# packages of PYTHON, the supervisor's environment (ADR-0447).
TYPED_DIRS = supervisor/src
JSON_FILES = feature-list.json .claude/settings.json

# A tool given as a path keeps working after `cd supervisor` or `cd webui`.
absolute = $(if $(findstring /,$(1)),$(abspath $(1)),$(1))
PY := $(call absolute,$(PYTHON))
NPM_BIN := $(call absolute,$(NPM))

# pytest's option for TEST_WORKERS, when the Python has pytest-xdist (ADR-0383).
xdist = $(shell $(PY) -c 'import xdist' > /dev/null 2>&1 && echo "-n $(TEST_WORKERS)")
workers = $(or $(xdist),$(info make $@: pytest-xdist is not installed for $(PY) (make setup installs it): one process))
# make test, when TESTS names every test (tests/, as make check runs it), measures the line coverage of
# supervisor/src with pytest-cov in every worker, prints the total and fails below the floor of
# supervisor/.coveragerc, its one place, which only rises (ADR-0442). PYTEST_ARGS do not turn it off:
# a -k over every test fails the floor, so name a part in TESTS. A part measures nothing, with a line
# that says so; make test-fast and make smoke never measure. Each run keeps its data in a file of its
# own (.coverage.run<pid> and its workers' .coverage.run<pid>.*), so that runs in one checkout neither
# erase nor add up each other's, and leaves it as supervisor/.coverage.
test_paths = $(patsubst %/,%,$(patsubst ./%,%,$(TESTS)))
every_test = $(if $(filter-out tests,$(test_paths)),,$(filter tests,$(test_paths)))
coverage = $(if $(every_test),--cov=src --cov-report=,$(info make $@: no coverage for a part of the tests (TESTS)))
coverage_file = $(if $(every_test),COVERAGE_FILE=.coverage.run$$$$ )
coverage_kept = $(if $(every_test),; status=$$?; mv -f .coverage.run$$$$ .coverage 2> /dev/null; exit $$status)

# make check and make check-fast record the state of the working tree when they pass, and forget
# the record when they fail, for the Stop hook and the pre-commit hook (scripts/green-run.py,
# ADR-0383). A run that may have checked less, or with other code than the tree's and make setup's,
# records nothing: a dry run or one that ignores errors (make -n, -t, -q, -i, -e, also through
# MAKEFLAGS), a variable on the command line other than TEST_WORKERS, a tool or TESTS or
# PYTEST_ARGS from the environment, a makefile besides this one (-f, MAKEFILES, a GNUmakefile), a
# MAKE from the environment, and the environment of Python, pytest and mypy that changes what runs
# (PYTEST_ADDOPTS, PYTEST_PLUGINS, PYTHONPATH, PYTHONHOME, MYPYPATH). GNU make 3.81 writes the
# single-letter options first in MAKEFLAGS ("n") or after its long ones ("--no-print-directory -n"),
# and the variables of the command line after them.
make_options = $(subst -,,$(foreach word,$(filter-out --%,$(MAKEFLAGS)),$(if $(findstring =,$(word)),,$(word))))
command_line = $(foreach v,$(.VARIABLES),$(if $(filter command line,$(origin $(v))),$(v)))
narrowed = $(filter-out TEST_WORKERS,$(command_line)) $(filter-out Makefile,$(MAKEFILE_LIST)) $(MAKEFILES) \
	$(filter environment,$(foreach v,PYTHON RUFF MYPY NPM TESTS PYTEST_ARGS,$(origin $(v)))) \
	$(filter-out default,$(origin MAKE)) $(PYTEST_ADDOPTS) $(PYTEST_PLUGINS) $(PYTHONPATH) $(PYTHONHOME) $(MYPYPATH) \
	$(foreach o,n t q i e,$(findstring $(o),$(make_options)))
records_green = $(if $(strip $(narrowed)),,yes)
green_begin = $(if $(records_green),state=$$($(PY) scripts/green-run.py state 2> /dev/null);)
green_run = $(green_begin)if ( $(call run_all,$(1)) ); then \
	$(if $(records_green),[ -z "$$state" ] || $(PY) scripts/green-run.py record "$$state" || true,:); \
	else $(PY) scripts/green-run.py forget > /dev/null 2>&1; exit 1; fi

# Runs every target of $(1) even when one fails, then fails when any did, so that one run reports
# every problem (ADR-0190). Each part starts with the line "== make <target>: <part>", which
# .claude/hooks/stop-check.sh uses to show the output of the part that failed.
run_all = failed=; for target in $(1); do echo "== make $@: $$target"; \
	$(MAKE) --no-print-directory $$target || failed="$$failed $$target"; \
	done; if [ -n "$$failed" ]; then echo "make $@ failed:$$failed" >&2; exit 1; fi; echo "make $@ passed: $(1)"

.DEFAULT_GOAL := help
.PHONY: help setup check check-fast lint lint-ruff lint-mypy lint-shell lint-json test test-fast smoke
.PHONY: frontend frontend-check frontend-runes frontend-i18n frontend-design frontend-build frontend-lint frontend-format e2e-local audit catalog-platforms catalog-tags
.PHONY: secret-scan image-scan

help: ## List the targets and the variables
	@echo "UNOS: make <target> [VARIABLE=value ...], from the repository root"
	@echo
	@awk 'BEGIN { FS = ":.*## " } /^[a-z][a-z0-9-]*:.*## / { printf "  %-11s %s\n", $$1, $$2 }' $(firstword $(MAKEFILE_LIST))
	@echo
	@echo "Variables: PYTHON=$(PYTHON) RUFF=$(RUFF) MYPY=$(MYPY) PIP_AUDIT=$(PIP_AUDIT) NPM=$(NPM)"
	@echo "           TESTS=$(TESTS) PYTEST_ARGS=$(PYTEST_ARGS) TEST_WORKERS=$(TEST_WORKERS) E2E_ARGS=$(E2E_ARGS)"

setup: ## Bootstrap this checkout: venvs, npm ci, Chromium, git hooks, dev.env (needs the network)
	scripts/setup.sh

check: ## The quality gate: lint, test and frontend, as CI runs them, without the audits
	+@$(call green_run,lint test frontend)

check-fast: ## lint and test-fast: what the pre-commit hook and the Stop hook run
	+@$(call green_run,lint test-fast)

lint: ## ruff (the rules of ruff.toml), mypy (mypy.ini), bash -n on the shell scripts, JSON syntax; all run
	+@$(call run_all,lint-ruff lint-mypy lint-shell lint-json)

lint-ruff:
	$(RUFF) check $(PYTHON_DIRS)

lint-mypy:
	$(MYPY) --python-executable $(PY) $(TYPED_DIRS)

lint-shell:
	@n=0; bad=; for f in $$(find $(SHELL_SCRIPT_DIRS) -type f 2> /dev/null | sort); do \
		case "$$f" in *.sh) ;; *) head -n 1 "$$f" | grep -Eq '^#!.*[/ ](ba|da|k)?sh( |$$)' || continue ;; esac; \
		bash -n "$$f" || bad="$$bad $$f"; n=$$((n + 1)); \
	done; if [ -n "$$bad" ]; then echo "bash -n failed:$$bad" >&2; exit 1; fi; \
	echo "bash -n: $$n shell scripts in $(SHELL_SCRIPT_DIRS)"

lint-json:
	@bad=; for f in $(JSON_FILES); do \
		$(PY) -m json.tool "$$f" > /dev/null || bad="$$bad $$f"; \
	done; if [ -n "$$bad" ]; then echo "lint: not valid JSON:$$bad" >&2; exit 1; fi; echo "JSON: $(JSON_FILES)"

test: ## All backend tests, supervisor/tests, and the coverage of supervisor/src against its floor (TESTS=..., PYTEST_ARGS=...)
	cd supervisor && $(coverage_file)$(PY) -m pytest $(TESTS) -q --tb=short --ignore=tests/fixtures $(workers) $(coverage) $(PYTEST_ARGS)$(coverage_kept)

test-fast: ## The convention tests: ADRs, ledger, guard, repo tooling and secrets, dev and stand-in scripts
	cd supervisor && $(PY) -m pytest $(TEST_FAST) -q --tb=short $(workers) $(PYTEST_ARGS)

smoke: ## A few seconds: the app starts and serves health, first-run setup and login (backend tests)
	cd supervisor && $(PY) -m pytest $(SMOKE) -q --tb=short $(PYTEST_ARGS)

frontend: ## svelte-check, the runes check (ADR-0427), the checks of the web UI's texts (ADR-0287) and design system (ADR-0400), the production build, ESLint and Prettier's check (ADR-0448); all run
	+@$(call run_all,frontend-check frontend-runes frontend-i18n frontend-design frontend-build frontend-lint frontend-format)

frontend-check:
	cd webui && $(NPM_BIN) run check

frontend-runes:
	cd webui && $(NPM_BIN) run runes

frontend-i18n:
	cd webui && $(NPM_BIN) run i18n

frontend-design:
	cd webui && $(NPM_BIN) run design

frontend-build:
	cd webui && $(NPM_BIN) run build

# ESLint with webui/eslint.config.js and Prettier's check with webui/prettier.config.js, over
# webui/src (ADR-0448); `npm run format` in webui/ writes Prettier's format.
frontend-lint:
	cd webui && $(NPM_BIN) run lint

frontend-format:
	cd webui && $(NPM_BIN) run format-check

e2e-local: ## Playwright E2E against the local stand-in, never the device (ADR-0215; E2E_ARGS=...)
	PYTHON="$(PY)" NPM="$(NPM_BIN)" scripts/standin/e2e.sh $(E2E_ARGS)

audit: ## pip-audit and npm audit of the runtime dependencies (needs the network)
	PYTHON="$(PY)" PIP_AUDIT="$(call absolute,$(PIP_AUDIT))" scripts/audit-python.sh
	cd webui && $(NPM_BIN) audit --omit=dev --audit-level=high

secret-scan: ## gitleaks, pinned by digest, over every commit of the history (needs Docker; ADR-0445)
	scripts/secret-scan.sh

image-scan: ## trivy, pinned by digest, over both images, built here (needs Docker and the network; ADR-0446)
	scripts/image-scan.sh

catalog-platforms: ## The recorded platforms of the catalog's images are the registries' (needs the network; ADR-0403)
	$(PYTHON) scripts/catalog-platforms.py check

catalog-tags: ## The tag of every pinned image of the catalog still names its pinned index (needs the network; ADR-0410)
	$(PYTHON) scripts/catalog-platforms.py tags
