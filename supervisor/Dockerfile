# The base by tag and digest (ADR-0305): the digest decides what the build starts from, and
# Dependabot follows the tag when it proposes a newer digest. A new digest whose linux/arm64
# image changed makes the dev device pull the base and run apt-get and pip again, hundreds of
# MB on its SD card (ADR-0214). supervisor/Dockerfile.test starts from the same base.
FROM python:3.11-slim@sha256:65bd3d9f1d7fa268dd1909973ba7eb4bdf41f6dcf2cfbe76ba9528b170f3fd79

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
# pip 26.3 first: the base's pip 24.0 takes no wheel built for linux/riscv64 (pip 24.1 added the
# platform's manylinux tags). On riscv64, cryptography, bcrypt, cffi, psutil and uvloop publish no
# wheel, so this layer also installs what compiles them (Rust, the headers of OpenSSL and libffi,
# make), builds them and removes it again: the image keeps no Rust toolchain. Elsewhere nothing
# is compiled (ADR-0370).
RUN pip install --no-cache-dir pip==26.3 \
    && if [ "$(dpkg --print-architecture)" != riscv64 ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    else \
        apt-get update \
        && apt-get install -y --no-install-recommends cargo rustc pkg-config libssl-dev libffi-dev make \
        && pip install --no-cache-dir -r requirements.txt \
        && apt-get purge -y --auto-remove cargo rustc pkg-config libssl-dev libffi-dev make \
        && rm -rf /var/lib/apt/lists/* /root/.cargo; \
    fi

# restic for the off-device copy (ADR-0337), Debian's package, in a layer of its own after pip's:
# the apt-get and pip layers above stay cached, so a device that builds this image downloads and
# writes only restic.
RUN apt-get update && apt-get install -y --no-install-recommends restic && rm -rf /var/lib/apt/lists/*

COPY src/ ./src/

# No .pyc files: the container's writable layer is on the system disk (SD card).
ENV PYTHONDONTWRITEBYTECODE=1

# The version System > Updates and General show: `git describe --tags --always`
# of the checkout, which deploy/install.sh and deploy/update.sh pass through
# deploy/docker-compose.yml (ADR-0234). After the code and the packages, so
# that a new version rebuilds only these layers.
ARG UNOS_VERSION=unknown
ENV UNOS_VERSION=${UNOS_VERSION}

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
# an hour, ADR-0383) is ended after 3 s, which leaves the shutdown its 5 s of waits.
# Keep these options in supervisor/Dockerfile.test the same.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--ws-max-size", "65536", "--ws-per-message-deflate", "false", "--limit-concurrency", "128", "--timeout-graceful-shutdown", "3"]
