FROM python:3.11-slim

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
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

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
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
