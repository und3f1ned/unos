#!/bin/bash
# The vulnerability scan of UNOS's two images, make image-scan and the Image vulnerabilities job of
# the weekly Audit workflow (ADR-0446): trivy, in its image pinned by digest, over the supervisor's
# and the web UI's image, with the policy of ADR-0446.
#
# - Without arguments it builds both images from this checkout with docker compose
#   (deploy/docker-compose.yml, project unos-image-scan) without the build cache, as a release
#   (.github/workflows/release.yml) builds them: apt-get and apk install the packages of the day.
#   Arguments name images that are built already, which it scans instead.
# - trivy downloads its vulnerability database first, in a run of its own with the network, into
#   TRIVY_CACHE (default: trivy's own ~/.cache/trivy, kept between runs). Each image is then saved
#   into a temporary folder and scanned from that archive, without the network, the Docker socket
#   or capabilities, with a read-only root, as the calling user (the cache and the archive are
#   theirs): what the image holds never runs, and trivy sees no other image.
# - The policy: known vulnerabilities (--scanners vuln) of the OS packages and the libraries
#   (--pkg-types os,library), HIGH and CRITICAL, of which a fixed version exists
#   (--ignore-unfixed), minus the entries of .trivyignore.yaml, each with its reason and expiry.
#   Such a finding fails the scan (--exit-code 3: trivy's own errors exit 1); the table names its
#   package, the installed and the fixed version.
#
# Environment: DOCKER (default docker), TRIVY_CACHE. Needs Docker, and the network for the pulls,
# the build and the database.
set -euo pipefail

TRIVY_IMAGE=ghcr.io/aquasecurity/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb
PROJECT=unos-image-scan

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
docker=${DOCKER:-docker}
cache=${TRIVY_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/trivy}

if [ $# -eq 0 ]; then
    "$docker" compose -f "$root/deploy/docker-compose.yml" --project-name "$PROJECT" build --no-cache
    set -- "$PROJECT-supervisor" "$PROJECT-webui"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$cache"
user=$(id -u):$(id -g)

"$docker" run --rm --user "$user" -e HOME=/tmp --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
    -v "$cache:/cache" \
    "$TRIVY_IMAGE" --cache-dir /cache image --download-db-only --no-progress

failed=
for image in "$@"; do
    archive=$work/image.tar
    "$docker" image save -o "$archive" "$image"
    status=0
    echo "== image-scan: $image"
    "$docker" run --rm --network none --read-only --user "$user" -e HOME=/tmp --tmpfs /tmp \
        --cap-drop ALL --security-opt no-new-privileges \
        -v "$cache:/cache" -v "$archive:/scan/image.tar:ro" \
        -v "$root/.trivyignore.yaml:/scan/trivyignore.yaml:ro" \
        "$TRIVY_IMAGE" --cache-dir /cache image --input /scan/image.tar \
        --skip-db-update --skip-java-db-update --offline-scan --cache-backend memory \
        --scanners vuln --pkg-types os,library --severity HIGH,CRITICAL --ignore-unfixed \
        --ignorefile /scan/trivyignore.yaml --exit-code 3 --format table --no-progress || status=$?
    rm -f "$archive"
    if [ "$status" -ne 0 ]; then
        failed="$failed $image"
        [ "$status" -eq 3 ] || echo "image-scan: trivy failed for $image (exit $status)" >&2
    fi
done
if [ -n "$failed" ]; then
    echo "image-scan: fixable HIGH or CRITICAL vulnerabilities, or a failed scan, in:$failed" >&2
    echo "image-scan: a fresh build, a newer digest of the base image or a newer package fixes it" \
        "(ADR-0446); an entry of .trivyignore.yaml needs its package, reason and expiry" >&2
    exit 1
fi
echo "image-scan: no fixable HIGH or CRITICAL vulnerability in: $*"
