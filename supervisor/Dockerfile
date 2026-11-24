# The base by tag and digest (ADR-0305): the digest decides what the build starts from, and
# Dependabot follows the tag when it proposes a newer digest. A new digest whose linux/arm64
# image changed makes the dev device pull the base and run apt-get and pip again, hundreds of
# MB on its system disk (ADR-0214). supervisor/Dockerfile.test starts from the same base.
#
# The stages (deploy.armv7-supervisor-build): system, then the Python packages of the target's
# architecture, which the last stage starts from (FROM packages-${TARGETARCH}): packages on amd64,
# arm64 and riscv64, and on 32-bit ARM packages-arm, which installs the wheels that the build stage
# arm-wheels compiles. On amd64, arm64 and riscv64 the image is made by the same instructions in the
# same order as before the stages, so that a device keeps its cached layers; no build tool of
# arm-wheels reaches an image.
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535 AS system

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    btrfs-progs \
    smartmontools \
    gcc \
    python3-dev \
    iproute2 \
    ethtool \
    procps \
    systemd \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

FROM system AS packages
# pip 26.3 first: the base's pip 24.0 takes no wheel built for linux/riscv64 (pip 24.1 added the
# platform's manylinux tags). On riscv64, cryptography, bcrypt, cffi, psutil and uvloop publish no
# wheel, so this layer also installs what compiles them (Rust, the headers of OpenSSL and libffi,
# make), builds them and removes it again: the image keeps no Rust toolchain. Elsewhere nothing
# is compiled (ADR-0370). setuptools 84.2.1 replaces the base's 79.0.1, whose vendored jaraco.context
# 5.3.0 and wheel 0.45.1 have fixed HIGH vulnerabilities that the image scan fails for (ADR-0446).
RUN pip install --no-cache-dir pip==26.3 setuptools==84.2.1 \
    && if [ "$(dpkg --print-architecture)" != riscv64 ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    else \
        apt-get update \
        && apt-get install -y --no-install-recommends cargo rustc pkg-config libssl-dev libffi-dev make \
        && pip install --no-cache-dir -r requirements.txt \
        && apt-get purge -y --auto-remove cargo rustc pkg-config libssl-dev libffi-dev make \
        && rm -rf /var/lib/apt/lists/* /root/.cargo; \
    fi

FROM packages AS packages-amd64
FROM packages AS packages-arm64
FROM packages AS packages-riscv64

# 32-bit ARM (linux/arm/v7), whose supervisor only its host builds (deploy.armv7-supervisor-build):
# cffi, psutil, PyYAML, uvloop, greenlet and httptools publish no armv7l wheel for Python 3.11, so
# this build stage compiles a wheel of every package that packages-arm installs, the pip and
# setuptools of the pip layer included: cffi with libffi's headers, greenlet with a C++ compiler,
# uvloop's libuv with make; the base has Python's headers. No image starts from it, so its compilers
# stay out of every image.
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535 AS arm-wheels
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ libffi-dev make \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir pip==26.3 \
    && pip wheel --no-cache-dir --wheel-dir /wheels pip==26.3 setuptools==84.2.1 -r requirements.txt

# The pip layer of 32-bit ARM: the wheels of arm-wheels, mounted for this one RUN, without the index.
FROM system AS packages-arm
RUN --mount=type=bind,from=arm-wheels,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index --find-links /wheels pip==26.3 setuptools==84.2.1 \
    && pip install --no-cache-dir --no-index --find-links /wheels -r requirements.txt

FROM packages-${TARGETARCH}

# restic for the off-device copy (ADR-0337), Debian's package, in a layer of its own after pip's:
# the apt-get and pip layers above stay cached, so a device that builds this image downloads and
# writes only restic.
RUN apt-get update && apt-get install -y --no-install-recommends restic && rm -rf /var/lib/apt/lists/*

# The code, with the migrations of unos.db in src/migrations, which the start applies (ADR-0444).
COPY src/ ./src/

# No .pyc files: the container's writable layer is on the system disk (flash on small boards).
ENV PYTHONDONTWRITEBYTECODE=1

# The version System > Updates shows: the release's tag, which
# .github/workflows/release.yml passes, or `git describe --tags --always` of
# the checkout where the image is built here, which deploy/install.sh and
# deploy/update.sh pass on 32-bit ARM through deploy/docker-compose.yml
# (ADR-0234). UNOS_RELEASE is the tag too in a release's image and empty in one
# built here: the in-app updater installs only over a release (ADR-0050).
# After the code and the packages, so that a new version rebuilds only these
# layers.
ARG UNOS_VERSION=unknown
ENV UNOS_VERSION=${UNOS_VERSION}
ARG UNOS_RELEASE=
ENV UNOS_RELEASE=${UNOS_RELEASE}

EXPOSE 8080

# Every request reaches the supervisor through Caddy (the webui container), so
# without proxy headers all clients share Caddy's address: one login
# rate-limit bucket for everybody and useless IPs in the login history and
# audit log. Uvicorn takes the client address from X-Forwarded-For only on
# connections from the addresses in FORWARDED_ALLOW_IPS (127.0.0.1 and ::1
# when unset); deploy/docker-compose.yml sets it to the webui container's
# fixed address. Do not trust every peer with "--forwarded-allow-ips *": port
# 8080 is not published, but processes in the host's network namespace
# (including host-network apps) can still connect to it directly. Caddy
# overwrites a client-supplied X-Forwarded-For with the real peer address (no
# trusted_proxies configured), so the value cannot be spoofed through the
# proxy.
#
# --ws-max-size: uvicorn reads a whole WebSocket message into memory before
# the application gets it, also the first message of a socket, which is read
# before the client is authenticated (src/websocket/events.py). Uvicorn's
# default allows 16 MiB per message; the only message clients send is
# {"type": "auth", "token": ...}, well under 1 KiB. A larger message closes
# the socket with 1009.
# --ws-per-message-deflate false: uvicorn inflates and queues every message of
# one read from the socket before the application reads the first, so
# compressed messages (86 bytes on the wire for 65,000) would still let one
# unauthenticated socket make the supervisor hold hundreds of MiB.
# --limit-concurrency 128: while 128 connections are open, uvicorn answers 503 to
# a new request instead of letting idle or slow clients hold every file
# descriptor and the memory of the supervisor; it serves again once they are
# gone. Room for every tab's live-data socket and Caddy's pooled connections
# (ADR-0333). Caddy limits the header block to 16 KB (webui/Caddyfile).
# --timeout-graceful-shutdown 3: at a stop, uvicorn waits for the open requests before the
# lifespan's shutdown, which stops a running install or repair and writes their audit entries;
# Docker kills the container 10 s after its SIGTERM. A GET that waits for an install's end (up to
# an hour, ADR-0384) is ended after 3 s, which leaves the shutdown its 5 s of waits.
# No --log-config or --log-level: src/main.py sets up every logger, uvicorn's included, as one JSON
# line each at UNOS_LOG_LEVEL (src/logging_setup.py, ADR-0398), after uvicorn has configured its own
# and before it writes its first line.
# Keep these options in supervisor/Dockerfile.test the same.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--ws-max-size", "65536", "--ws-per-message-deflate", "false", "--limit-concurrency", "128", "--timeout-graceful-shutdown", "3"]
