#!/bin/bash
# The Python part of make audit and of the CI audits (ADR-0190, ADR-0218): the known
# vulnerabilities of the supervisor's runtime dependencies, supervisor/requirements.txt with every
# package that it depends on.
#
# pip-audit -r resolved the requirements in a temporary virtual environment of its own, into which
# it first installed the latest pip, setuptools and wheel, unpinned. Here the environment is this
# script's (ADR-0422):
#   1. a virtual environment of PYTHON, Python 3.11 as in the supervisor's image, in a temporary
#      folder that is removed at the end;
#   2. pip, setuptools and wheel (and packaging, which wheel needs) from
#      .github/audit-env-requirements.txt, pinned with their hashes, wheels only;
#   3. that pip resolves requirements.txt without installing it (--dry-run --report), as pip-audit
#      did, with wheels only, as the images install them (requirements.txt names nut2, which has
#      none), and every package listed (--ignore-installed); nut2's sdist is built by the pinned
#      setuptools (--no-build-isolation), not by one that pip would fetch from the index;
#   4. pip-audit checks the versions it resolved, without pip (--disable-pip --no-deps), and fails
#      for a package that it cannot check (--strict).
#
# Environment: PYTHON (default python3), PIP_AUDIT (default pip-audit). Needs the network.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python=${PYTHON:-python3}
pip_audit=${PIP_AUDIT:-pip-audit}

# Another Python resolves other versions and wheels: the audit would check what the image does not
# install.
version=$("$python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if [ "$version" != 3.11 ]; then
    echo "audit-python.sh: $python is Python $version; the supervisor's image runs 3.11 (PYTHON=...)" >&2
    exit 1
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/unos-audit.XXXXXX")
trap 'rm -rf "$work"' EXIT

"$python" -m venv "$work/env"
env_python=$work/env/bin/python
quiet=(--disable-pip-version-check --quiet --no-input)
"$env_python" -m pip install "${quiet[@]}" --require-hashes --only-binary=:all: \
    -r "$root/.github/audit-env-requirements.txt"
"$env_python" -m pip install "${quiet[@]}" --dry-run --ignore-installed --no-build-isolation \
    --only-binary=:all: --report "$work/report.json" -r "$root/supervisor/requirements.txt"
"$env_python" -c '
import json, sys
packages = [item["metadata"] for item in json.load(open(sys.argv[1]))["install"]]
if not packages:
    sys.exit("audit-python.sh: pip resolved no package from supervisor/requirements.txt")
for package in packages:
    print(package["name"] + "==" + package["version"])
' "$work/report.json" > "$work/resolved.txt"
"$pip_audit" --disable-pip --no-deps --strict -r "$work/resolved.txt"
