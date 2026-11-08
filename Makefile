# UNOS developer commands. Run them from the repository root; `make help` lists the targets.
# make check is the quality gate (ADR-0216): CI's Unit Tests, Lint and Frontend jobs run the same
# targets (.github/workflows/test.yml). make setup bootstraps a checkout (ADR-0217).
# docs/TESTING.md explains the targets and the three verification levels.

# Tools. The defaults are what make setup installs; override them on the command line, e.g.
# make test PYTHON=/path/to/python3.11.
PYTHON ?= $(if $(wildcard supervisor/.venv/bin/python),supervisor/.venv/bin/python,python3)
RUFF ?= $(if $(wildcard .venv-lint/bin/ruff),.venv-lint/bin/ruff,ruff)
PIP_AUDIT ?= $(if $(wildcard .venv-lint/bin/pip-audit),.venv-lint/bin/pip-audit,pip-audit)
NPM ?= npm

# What make test runs (paths relative to supervisor/), and extra pytest arguments:
# make test TESTS=tests/test_smbconf.py PYTEST_ARGS='-x -k render'.
TESTS ?= tests/
PYTEST_ARGS ?=
# The repository-convention tests: make test-fast and the pre-commit hook. test_repo_tooling.py
# also scans the repository's text files for secrets.
TEST_FAST = tests/test_adrs.py tests/test_feature_list.py tests/test_agent_guard.py \
	tests/test_repo_tooling.py tests/test_dev_scripts.py tests/test_standin.py
# make smoke: existing backend tests that start the app and serve requests through it, in a few
# seconds: the startup and /api/v1/health, the first-run setup, a login through the proxy layer
# and the refusal of anonymous requests. The baseline of make setup and of a session.
SMOKE = tests/test_lifespan.py tests/test_auth.py::TestFirstRunSetup \
	tests/test_authz.py::TestRealClientAddress \
	tests/test_authz.py::TestRouteMatrix::test_anonymous_requests_are_rejected
# Arguments for scripts/standin/e2e.sh: make e2e-local E2E_ARGS='tests/auth.spec.ts'.
E2E_ARGS ?=

# The backend tests use fixed paths under /tmp (supervisor/tests/conftest.py), so only one run
# per machine may use them at a time, also across worktrees: the test targets wait for this
# lock (flock on Linux, lockf on macOS). TEST_LOCK= runs without it.
TEST_LOCK ?= /tmp/unos-backend-tests.lock
LOCK = $(if $(TEST_LOCK),$(shell if command -v flock > /dev/null 2>&1; then echo "flock -w 1800 $(TEST_LOCK)"; \
	elif [ -x /usr/bin/lockf ]; then echo "/usr/bin/lockf -k -t 1800 $(TEST_LOCK)"; fi))

SHELL_SCRIPT_DIRS = deploy scripts .claude/hooks
# The Python that make lint checks with ruff: the supervisor and the repository's own tooling
# (the guard, scripts/dev, the stand-in, the commit checker).
PYTHON_DIRS = supervisor scripts .claude/hooks
JSON_FILES = feature-list.json .claude/settings.json

# A tool given as a path keeps working after `cd supervisor` or `cd webui`.
absolute = $(if $(findstring /,$(1)),$(abspath $(1)),$(1))
PY := $(call absolute,$(PYTHON))
NPM_BIN := $(call absolute,$(NPM))

# Runs every target of $(1) even when one fails, then fails when any did, so that one run reports
# every problem (ADR-0190). Each part starts with the line "== make <target>: <part>", which
# .claude/hooks/stop-check.sh uses to show the output of the part that failed.
run_all = failed=; for target in $(1); do echo "== make $@: $$target"; \
	$(MAKE) --no-print-directory $$target || failed="$$failed $$target"; \
	done; if [ -n "$$failed" ]; then echo "make $@ failed:$$failed" >&2; exit 1; fi; echo "make $@ passed: $(1)"

.DEFAULT_GOAL := help
.PHONY: help setup check check-fast lint lint-ruff lint-shell lint-json test test-fast smoke
.PHONY: frontend frontend-check frontend-i18n frontend-build e2e-local audit

help: ## List the targets and the variables
	@echo "UNOS: make <target> [VARIABLE=value ...], from the repository root"
	@echo
	@awk 'BEGIN { FS = ":.*## " } /^[a-z][a-z0-9-]*:.*## / { printf "  %-11s %s\n", $$1, $$2 }' $(firstword $(MAKEFILE_LIST))
	@echo
	@echo "Variables: PYTHON=$(PYTHON) RUFF=$(RUFF) PIP_AUDIT=$(PIP_AUDIT) NPM=$(NPM)"
	@echo "           TESTS=$(TESTS) PYTEST_ARGS=$(PYTEST_ARGS) E2E_ARGS=$(E2E_ARGS) TEST_LOCK=$(TEST_LOCK)"

setup: ## Bootstrap this checkout: venvs, npm ci, Chromium, git hooks, dev.env (needs the network)
	scripts/setup.sh

check: ## The quality gate: lint, test and frontend, as CI runs them, without the audits
	+@$(call run_all,lint test frontend)

check-fast: ## lint and test-fast: what the pre-commit hook and the Stop hook run
	+@$(call run_all,lint test-fast)

lint: ## ruff (pyflakes, syntax errors), bash -n on the shell scripts, JSON syntax; all three run
	+@$(call run_all,lint-ruff lint-shell lint-json)

lint-ruff:
	$(RUFF) check --select F,E9 $(PYTHON_DIRS)

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

test: ## All backend tests, supervisor/tests (TESTS=..., PYTEST_ARGS=...)
	cd supervisor && $(LOCK) $(PY) -m pytest $(TESTS) -q --tb=short --ignore=tests/mocks --ignore=tests/fixtures $(PYTEST_ARGS)

test-fast: ## The convention tests: ADRs, ledger, guard, repo tooling and secrets, dev and stand-in scripts
	cd supervisor && $(LOCK) $(PY) -m pytest $(TEST_FAST) -q --tb=short $(PYTEST_ARGS)

smoke: ## A few seconds: the app starts and serves health, first-run setup and login (backend tests)
	cd supervisor && $(LOCK) $(PY) -m pytest $(SMOKE) -q --tb=short $(PYTEST_ARGS)

frontend: ## svelte-check, the check of the web UI's texts (ADR-0273) and the production build; all run
	+@$(call run_all,frontend-check frontend-i18n frontend-build)

frontend-check:
	cd webui && $(NPM_BIN) run check

frontend-i18n:
	cd webui && $(NPM_BIN) run i18n

frontend-build:
	cd webui && $(NPM_BIN) run build

e2e-local: ## Playwright E2E against the local stand-in, never the device (ADR-0215; E2E_ARGS=...)
	PYTHON="$(PY)" NPM="$(NPM_BIN)" scripts/standin/e2e.sh $(E2E_ARGS)

audit: ## pip-audit and npm audit of the runtime dependencies (needs the network)
	$(PIP_AUDIT) -r supervisor/requirements.txt
	cd webui && $(NPM_BIN) audit --omit=dev --audit-level=high
