#!/usr/bin/env bash
# Stop hook of Claude Code sessions in this repository (.claude/settings.json; ADR-0216).
#
# Before the agent reports its work as done, the fast part of the quality gate runs: make
# check-fast. When it fails, the hook exits 2 and prints the last lines of each failing part on
# stderr; Claude Code hands them to the agent, which goes back to work instead of stopping.
# The session stops (exit 0) without a run when
#   - the hook input says "stop_hook_active": true: the agent already went on because of this
#     hook once, and a second failure must not hold it in a loop;
#   - this is not a git work tree, or a tool of make check-fast is missing (make, ruff, a Python
#     with pytest; make setup installs them): a warning, as the systemMessage of a JSON answer on
#     stdout, which Claude Code shows to the user (the plain output of a Stop hook only appears in
#     its transcript view);
#   - the working tree is the one of the last green run of make check or make check-fast: the
#     SHA-256 of HEAD, git status, git diff (staged and not) and the untracked files that
#     scripts/green-run.py records in tmp/stop-check/green (ADR-0383).
# The output of the last run is in tmp/stop-check/check-fast.log (tmp/ is git-ignored). PYTHON
# and RUFF from the environment count as they do for make.
set -u

LINES_PER_PART=25

# The repository's scripts/green-run.py, found from this file: the project may be another tree.
green_run=$(cd -P "$(dirname "$0")/../.." 2> /dev/null && pwd)/scripts/green-run.py
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" 2> /dev/null || exit 0
input=$(cat)
if printf '%s' "$input" | grep -Eq '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
    exit 0
fi

skip() {
    local message
    # One line of JSON: control characters out, backslashes and quotes escaped.
    message=$(printf 'stop-check.sh: %s; make check-fast did not run.' "$*" | tr '\t\n\r' '   ' | tr -d '\000-\037')
    message=${message//\\/\\\\}
    message=${message//\"/\\\"}
    printf '{"systemMessage": "%s"}\n' "$message"
    exit 0
}

git rev-parse --is-inside-work-tree > /dev/null 2>&1 || skip "not a git work tree"
python=${PYTHON:-}
if [ -z "$python" ]; then
    if [ -x supervisor/.venv/bin/python ]; then python=supervisor/.venv/bin/python; else python=python3; fi
fi
ruff=${RUFF:-}
if [ -z "$ruff" ]; then
    if [ -x .venv-lint/bin/ruff ]; then ruff=.venv-lint/bin/ruff; else ruff=ruff; fi
fi
missing=
command -v make > /dev/null 2>&1 || missing="$missing make"
command -v "$ruff" > /dev/null 2>&1 || missing="$missing ruff"
"$python" -c 'import pytest' > /dev/null 2>&1 || missing="$missing pytest ($python)"
[ -z "$missing" ] || skip "missing:$missing (make setup installs them)"

mkdir -p tmp/stop-check 2> /dev/null || skip "cannot create tmp/stop-check"
if "$python" "$green_run" check 2> /dev/null; then
    exit 0
fi

log=tmp/stop-check/check-fast.log
run=$(mktemp tmp/stop-check/check-fast.XXXXXX) || skip "cannot create a log in tmp/stop-check"
trap 'rm -f "$run"' EXIT  # when Claude Code stops the hook at its timeout
# make check-fast records its green run when it passes, and forgets the record when it fails.
if make --no-print-directory check-fast > "$run" 2>&1; then
    mv -f "$run" "$log"
    exit 0
fi
mv -f "$run" "$log"

# run_all in the Makefile starts each part with "== make check-fast: <part>" and ends with
# "make check-fast failed: <parts>".
failed=$(sed -n 's/^make check-fast failed: *//p' "$log" | tail -n 1)
{
    echo "stop-check.sh: make check-fast failed${failed:+ in: $failed}. Fix it before you finish, or" \
        "tell the owner why it cannot pass. The whole output is in $log."
    if [ -n "$failed" ]; then
        for part in $failed; do
            echo "--- the last lines of make $part:"
            awk -v head="== make check-fast: $part" '
                $0 == head { on = 1; next }
                /^== make check-fast: / || /^make check-fast (passed|failed):/ { on = 0 }
                on' "$log" | tail -n "$LINES_PER_PART"
        done
    else
        tail -n "$LINES_PER_PART" "$log"
    fi
} >&2
exit 2
