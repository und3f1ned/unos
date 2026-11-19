#!/usr/bin/env bash
# Bootstrap this checkout in one idempotent command (ADR-0217): make setup runs it.
#
#   scripts/setup.sh           check the prerequisites, then set up what is missing or outdated
#   scripts/setup.sh --check   only report what is missing or outdated: changes nothing, no network
#   scripts/setup.sh --help    this text
#
# Steps, in this order:
#   1. Prerequisites: git, make, Python 3.11 (python3.11 on PATH, or PYTHON311=/path/to/python),
#      Node.js with npm in the version of webui/package.json (engines). When one is missing, the
#      script says so and stops before it changes anything.
#   2. supervisor/.venv: Python 3.11 with supervisor/requirements-dev.txt (make test, make e2e-local).
#   3. .venv-lint: ruff and pip-audit from .github/lint-requirements.txt, with their hashes (make lint,
#      make audit).
#   4. webui/node_modules: npm ci from webui/package-lock.json. A symbolic link is left as it is.
#   5. Chromium for Playwright, the version that webui/package-lock.json pins (make e2e-local).
#   6. git hooks: git config core.hooksPath scripts/git-hooks (pre-commit runs make check-fast,
#      commit-msg runs scripts/check-commit-msg.py).
#   7. dev.env: a copy of dev.env.example with mode 600 when it is missing, except in a linked git
#      worktree whose main checkout has one, which the scripts in scripts/dev then use (ADR-0210).
#      An existing dev.env is never replaced, read or shown; only its mode is set to 600.
#   8. The baseline: make check-fast, then make smoke (the app starts and serves health, the
#      first-run setup and a login). It says baseline green or red.
# Steps 2-5 need the network. A step that is up to date is skipped: each environment remembers the
# SHA-256 of the files it was installed from in .unos-setup.sha256, so a rerun installs only what
# changed. Exit codes: 0 done (--check: nothing missing), 1 something is missing or failed, 2 usage.
set -uo pipefail

usage() {
    sed -n '2,/^set -uo pipefail$/p' "$0" | sed '$d' | sed -E 's/^# ?//'
}

mode=setup
case ${1:-} in
    "") ;;
    --check) mode=check ;;
    -h | --help) usage; exit 0 ;;
    *) echo "setup.sh: unknown argument '$1' (see scripts/setup.sh --help)" >&2; exit 2 ;;
esac

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P) || exit 1
cd "$repo" || exit 1

problems=0
ok() { printf '  ok       %s\n' "$*"; }
missing() { printf '  missing  %s\n' "$*"; problems=$((problems + 1)); }
optional() { printf '  optional %s\n' "$*"; }
doing() { printf '  ...      %s\n' "$*"; }
failed() { printf 'setup.sh: %s\n' "$*" >&2; exit 1; }

sha256_of() {
    if command -v sha256sum > /dev/null 2>&1; then
        cat "$@" | sha256sum | cut -d ' ' -f 1
    else
        cat "$@" | shasum -a 256 | cut -d ' ' -f 1
    fi
}

# The stamp of an environment: the SHA-256 of the files it was installed from.
stamp_matches() { [ -f "$1/.unos-setup.sha256" ] && [ "$(cat "$1/.unos-setup.sha256")" = "$2" ]; }
python_version() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2> /dev/null; }
file_mode() { stat -c %a "$1" 2> /dev/null || stat -f %Lp "$1"; }
file_owner() { stat -c %u "$1" 2> /dev/null || stat -f %u "$1"; }

# --- 1. prerequisites ---------------------------------------------------------------------

echo "setup.sh: prerequisites"
if command -v git > /dev/null 2>&1; then ok "$(git --version)"; else missing "git: install it (macOS: xcode-select --install; Debian/Ubuntu: apt install git)"; fi
if command -v make > /dev/null 2>&1; then ok "$(make --version | head -n 1)"; else missing "make: install it (macOS: xcode-select --install; Debian/Ubuntu: apt install make)"; fi

python311=
for candidate in "${PYTHON311:-}" python3.11 python3; do
    [ -n "$candidate" ] && command -v "$candidate" > /dev/null 2>&1 || continue
    if [ "$(python_version "$candidate")" = "3.11" ]; then
        python311=$(command -v "$candidate")
        break
    fi
done
if [ -n "$python311" ]; then
    ok "Python 3.11 ($python311)"
else
    missing "Python 3.11: install it (macOS: brew install python@3.11; Debian/Ubuntu: apt install python3.11 python3.11-venv), or set PYTHON311=/path/to/python3.11"
fi

node_major=$(sed -n 's/.*"node": *">= *\([0-9][0-9]*\).*/\1/p' webui/package.json | head -n 1)
node_major=${node_major:-22}
if command -v node > /dev/null 2>&1 && [ "$(node --version | sed -E 's/^v([0-9]+).*/\1/')" -ge "$node_major" ] 2> /dev/null; then
    ok "Node.js $(node --version) ($node_major or later, webui/package.json)"
else
    missing "Node.js $node_major or later: install it from https://nodejs.org or with your package manager"
fi
if command -v npm > /dev/null 2>&1; then ok "npm $(npm --version)"; else missing "npm: it comes with Node.js"; fi
if command -v openssl > /dev/null 2>&1; then ok "openssl (the stand-in's TLS certificate, make e2e-local)"; else optional "openssl: make e2e-local needs it for the stand-in's certificate"; fi
if command -v docker > /dev/null 2>&1; then ok "docker (only for the compose test stack and scripts/test-cycle.sh)"; else optional "docker: only for the compose test stack and scripts/test-cycle.sh (docs/TESTING.md)"; fi

if [ "$mode" = setup ] && [ "$problems" -gt 0 ]; then
    failed "$problems prerequisites are missing; nothing was changed. Install them, then run make setup again."
fi

# --- 2. and 3. Python environments ---------------------------------------------------------

# python_env <dir> <what> "<files>" <pip arguments...>: the venv in <dir>, installed with the pip
# arguments; <files> (separated by spaces) are the requirement files it depends on, those that the
# -r files include as well. Wheels only (ADR-0422), so that pip runs the build code of no package;
# the supervisor's venv lets nut2, which publishes no wheel, come as its sdist (--no-binary=nut2,
# after --only-binary=:all:, which would clear it).
python_env() {
    local dir=$1 what=$2 files=$3
    shift 3
    local want
    # shellcheck disable=SC2086 # the file names have no spaces
    want=$(sha256_of $files) || failed "cannot read $files"
    local version=
    [ -x "$dir/bin/python" ] && version=$(python_version "$dir/bin/python")
    if [ "$version" = 3.11 ] && stamp_matches "$dir" "$want"; then
        ok "$dir ($what)"
        return
    fi
    if [ "$mode" = check ]; then
        if [ -z "$version" ]; then
            missing "$dir ($what): make setup creates it"
        elif [ "$version" != 3.11 ]; then
            missing "$dir has Python $version, not 3.11: make setup recreates it"
        else
            missing "$dir is not up to date with $files: make setup installs the changes"
        fi
        return
    fi
    if [ "$version" != 3.11 ]; then
        doing "creating $dir with $python311"
        "$python311" -m venv --clear "$dir" || failed "creating $dir failed (Debian/Ubuntu: apt install python3.11-venv)"
    fi
    doing "$dir: pip install --only-binary=:all: $*"
    "$dir/bin/python" -m pip install --disable-pip-version-check -q --only-binary=:all: "$@" \
        || failed "pip install for $dir failed (it needs the network, and a wheel of every package for this machine); see the output above"
    printf '%s\n' "$want" > "$dir/.unos-setup.sha256"
    ok "$dir ($what)"
}

echo "setup.sh: this checkout ($repo)"
python_env supervisor/.venv "Python 3.11 with supervisor/requirements-dev.txt" \
    "supervisor/requirements.txt supervisor/requirements-dev.txt" --no-binary=nut2 \
    -r supervisor/requirements-dev.txt
python_env .venv-lint "ruff and pip-audit with their hashes" \
    ".github/lint-requirements.txt" --require-hashes -r .github/lint-requirements.txt

# --- 4. and 5. the web UI's packages and Chromium ------------------------------------------

modules=webui/node_modules
if [ -L "$modules" ]; then
    ok "$modules (a symbolic link: left as it is)"
else
    want=$(sha256_of webui/package-lock.json) || failed "cannot read webui/package-lock.json"
    if [ -d "$modules" ] && stamp_matches "$modules" "$want"; then
        ok "$modules (npm ci of webui/package-lock.json)"
    elif [ "$mode" = check ]; then
        missing "$modules is missing or older than webui/package-lock.json: make setup runs npm ci"
    else
        doing "npm ci in webui"
        (cd webui && npm ci --no-audit --no-fund) || failed "npm ci failed (it needs the network); see the output above"
        printf '%s\n' "$want" > "$modules/.unos-setup.sha256"
        ok "$modules (npm ci of webui/package-lock.json)"
    fi
fi

playwright=webui/node_modules/.bin/playwright
chromium_installed() {
    local locations
    locations=$(cd webui && node_modules/.bin/playwright install --dry-run chromium 2> /dev/null \
        | sed -n 's/^ *Install location: *//p')
    [ -n "$locations" ] || return 1
    while IFS= read -r location; do
        [ -d "$location" ] || return 1
    done <<< "$locations"
}
if [ ! -x "$playwright" ]; then
    missing "Chromium for Playwright: install the web UI's packages first (make setup)"
elif chromium_installed; then
    ok "Chromium for Playwright (webui/package-lock.json's version)"
elif [ "$mode" = check ]; then
    missing "Chromium for Playwright: make setup installs it (playwright install chromium)"
else
    doing "installing Chromium for Playwright"
    (cd webui && node_modules/.bin/playwright install chromium) || failed "installing Chromium failed (it needs the network)"
    ok "Chromium for Playwright"
    if [ "$(uname -s)" = Linux ]; then
        echo "  note     if Chromium does not start, install its system libraries once:"
        echo "           sudo webui/node_modules/.bin/playwright install-deps chromium"
    fi
fi

# --- 6. git hooks ---------------------------------------------------------------------------

if git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    hooks=$(git config --get core.hooksPath || true)
    if [ "$hooks" = scripts/git-hooks ]; then
        ok "git hooks (core.hooksPath scripts/git-hooks)"
    elif [ "$mode" = check ]; then
        missing "git hooks: core.hooksPath is '${hooks:-unset}', not scripts/git-hooks: make setup sets it"
    else
        git config core.hooksPath scripts/git-hooks || failed "git config core.hooksPath failed"
        ok "git hooks (core.hooksPath scripts/git-hooks${hooks:+, was $hooks})"
    fi
else
    missing "git hooks: $repo is not a git work tree"
fi

# --- 7. dev.env -----------------------------------------------------------------------------

# A linked git worktree uses the main checkout's dev.env (scripts/dev/env.sh finds it, ADR-0210):
# a copy here would win over it and hold no settings.
main_env=
if common=$(git rev-parse --path-format=absolute --git-common-dir 2> /dev/null) &&
    gitdir=$(git rev-parse --path-format=absolute --git-dir 2> /dev/null) && [ "$common" != "$gitdir" ]; then
    main_env="$(dirname "$common")/dev.env"
fi

if [ -e dev.env ]; then
    if [ "$(file_owner dev.env)" != "$(id -u)" ]; then
        missing "dev.env belongs to another user: the scripts in scripts/dev refuse it (the owner fixes this)"
    elif [ "$(file_mode dev.env)" = 600 ]; then
        ok "dev.env (mode 600; its content is never read here)"
    elif [ "$mode" = check ]; then
        missing "dev.env has mode $(file_mode dev.env), not 600: make setup sets 600"
    else
        chmod 600 dev.env || failed "chmod 600 dev.env failed"
        ok "dev.env (mode set to 600; its content is never read here)"
    fi
elif [ -n "$main_env" ] && [ -e "$main_env" ]; then
    ok "dev.env: this linked worktree uses the main checkout's (scripts/dev read it there)"
elif [ "$mode" = check ]; then
    missing "dev.env: make setup copies dev.env.example (mode 600); then fill in the device's settings"
else
    (umask 077 && cp dev.env.example dev.env) || failed "cannot create dev.env from dev.env.example"
    chmod 600 dev.env
    ok "dev.env created from dev.env.example (mode 600): fill in the device's settings"
fi

# --- 8. the baseline -----------------------------------------------------------------------

if [ "$mode" = check ]; then
    if [ "$problems" -gt 0 ]; then
        echo "setup.sh --check: $problems missing or outdated; run make setup (it needs the network)."
        exit 1
    fi
    echo "setup.sh --check: this checkout is set up."
    exit 0
fi
if [ "$problems" -gt 0 ]; then
    failed "$problems items are still missing (see above)."
fi
echo "setup.sh: baseline: make check-fast, then make smoke"
for target in check-fast smoke; do
    if ! make "$target"; then
        failed "baseline red: make $target failed before any change (see above). Report it to the owner before you start."
    fi
done
echo "setup.sh: baseline green. make help lists the targets."
