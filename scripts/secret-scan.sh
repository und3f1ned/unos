#!/bin/bash
# The secret scan of the git history, make secret-scan and the Secret Scan job of the CI workflow
# (ADR-0439). Two checks over every commit that HEAD reaches; both run, and either fails the scan:
#
# 1. gitleaks, in its image pinned by digest, with gitleaks' default rules and the rules and
#    allowlist of .gitleaks.toml.
#    - The container gets the repository's git folder read-only (a linked worktree's common one)
#      and the two files it reads; no network, no capabilities, a read-only root. It reads the
#      commits only: files of the working tree that are not committed are not scanned.
#    - A merge is read as git's remerge-diff shows it: what its resolution changed against the
#      merge that git would have made. git writes the objects of that merge into a tmpfs
#      (GIT_OBJECT_DIRECTORY), and reads the repository's as alternates.
#    - Secrets never reach the output: gitleaks redacts them (--redact), and a finding is printed
#      as its rule, file, commit and line only (scripts/secret-scan.tmpl).
#    - Inline gitleaks:allow comments do not silence a finding (--ignore-gitleaks-allow): the
#      allowlist of .gitleaks.toml, by commit and path with its reason, is the only exception.
#    - gitleaks logs git's errors and still answers "no leaks", with 0 commits scanned for a
#      repository it cannot read: an ERR line of its log, or no commit scanned, fails the scan.
# 2. scripts/published-passwords.py: no commit adds one of the two passwords that CLAUDE.md once
#    published (by their SHA-256), but where that script's ALLOWED names it.
#
# A shallow clone is refused: it holds part of the history only.
# Environment: DOCKER (default docker), PYTHON (default python3). Needs Docker, and the network for
# the first pull of the image.
set -euo pipefail

GITLEAKS_IMAGE=ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
docker=${DOCKER:-docker}
python=${PYTHON:-python3}

shallow=$(git -C "$root" rev-parse --is-shallow-repository)
if [ "$shallow" != false ]; then
    echo "secret-scan: a shallow clone holds only part of the history; fetch all of it (fetch-depth: 0)" >&2
    exit 2
fi
git_dir=$(cd "$root" && cd "$(git rev-parse --git-common-dir)" && pwd -P)
head=$(git -C "$root" rev-parse --verify 'HEAD^{commit}')

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failed=0
status=0
"$docker" run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp \
    -v "$git_dir:/repo.git:ro" \
    -v "$root/.gitleaks.toml:/scan/gitleaks.toml:ro" \
    -v "$root/scripts/secret-scan.tmpl:/scan/findings.tmpl:ro" \
    -e GIT_OBJECT_DIRECTORY=/tmp -e GIT_ALTERNATE_OBJECT_DIRECTORIES=/repo.git/objects \
    -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
    "$GITLEAKS_IMAGE" git --config /scan/gitleaks.toml --log-opts="--remerge-diff $head" \
    --redact --ignore-gitleaks-allow --no-banner --no-color \
    --report-format template --report-template /scan/findings.tmpl --report-path - /repo.git \
    > "$work/findings" 2> "$work/log" || status=$?
cat "$work/log" >&2

scanned=$(sed -n 's/.* \([0-9][0-9]*\) commits scanned\..*/\1/p' "$work/log" | tail -n 1)
if [ "$status" -gt 1 ] || grep -q ' ERR ' "$work/log" || [ "${scanned:-0}" -eq 0 ]; then
    echo "secret-scan: gitleaks failed (exit $status, ${scanned:-no} commits scanned of $head)" >&2
    failed=2
elif [ "$status" -ne 0 ] || [ -s "$work/findings" ]; then
    echo "secret-scan: gitleaks' findings (rule, file, commit:line) in the $scanned commits of $head:" >&2
    awk -F '\t' 'NF { printf "  %s  %s  %s:%s\n", $1, $2, $3, $4 }' "$work/findings" >&2
    echo "secret-scan: move a secret into dev.env and rotate it (ADR-0212); a test value or a" \
        "placeholder gets another value, or a block of .gitleaks.toml by commit and path with" \
        "its reason (ADR-0439)" >&2
    failed=1
else
    echo "secret-scan: gitleaks found no secret in the $scanned commits of $head"
fi

published=0
"$python" -I "$root/scripts/published-passwords.py" "$root" || published=$?
if [ "$published" -gt "$failed" ]; then
    failed=$published
fi
exit "$failed"
