#!/bin/bash
#
# The Dockerfile of a release's linux/riscv64 supervisor image (.github/workflows/release.yml,
# deploy.riscv64-release-images), on standard output:
#
#   scripts/riscv64-release-dockerfile.sh supervisor/Dockerfile > Dockerfile.riscv64
#
# supervisor/Dockerfile with its pip layer, which on riscv64 compiles the packages that publish no
# riscv64 wheel, replaced by an install of the same pip and setuptools and of requirements.txt from
# the wheels of the build context riscv64-wheels (supervisor/Dockerfile.riscv64-wheels), without the
# index, so that the release builds nothing from source there. Every other line stays as it is:
# supervisor/Dockerfile, which scripts/dev/deploy.sh builds on the dev devices, keeps its layers.

set -euo pipefail

dockerfile=${1:?usage: $0 <supervisor/Dockerfile>}

awk '
    # The continuation lines of the pip layer.
    skip { skip = /\\$/; next }
    /^RUN pip install --no-cache-dir pip==[0-9.]+ setuptools==[0-9.]+ / {
        layers++
        match($0, /pip==[0-9.]+ setuptools==[0-9.]+/)
        pins = substr($0, RSTART, RLENGTH)
        print "RUN --mount=type=bind,from=riscv64-wheels,source=/wheels,target=/wheels \\"
        print "    pip install --no-cache-dir --no-index --find-links /wheels " pins " \\"
        print "    && pip install --no-cache-dir --no-index --find-links /wheels -r requirements.txt"
        skip = /\\$/
        next
    }
    { print }
    END {
        if (layers != 1) {
            printf "%s has %d pip layers (RUN pip install --no-cache-dir pip==<x> setuptools==<y> ...), not one\n", \
                FILENAME, layers > "/dev/stderr"
            exit 1
        }
    }
' "$dockerfile"
