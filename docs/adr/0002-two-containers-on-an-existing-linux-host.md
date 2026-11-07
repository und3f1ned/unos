---
status: accepted
date: 2026-01-23
area: architecture
decided-by: owner
---

# ADR-0002: UNOS runs on an existing Linux host as a web UI and a privileged supervisor container

## Context

UNOS is meant to turn an ordinary Debian or Ubuntu machine, such as an ARM board, into a NAS with a web interface, without replacing or reflashing its operating system. Storage, mount and Samba work has to change the host itself, while the web interface and the API should be easy to install and update.

## Decision

- UNOS is a management layer installed on an existing host as two Docker containers, defined in `deploy/docker-compose.yml`: `unos-webui` (the SvelteKit app as static files served by Caddy on ports 80 and 443, which proxies `/api` and `/ws` to the supervisor) and `unos-supervisor` (the FastAPI backend with SQLite at `/data/unos.db`, port 8080, not published).
- The supervisor runs privileged, with `pid: host`, the Docker socket `/var/run/docker.sock` and bind mounts of `/etc/samba` and `/mnt` (rshared), because storage, mount and Samba operations, and `nsenter` into the host's namespaces (ADR-0020), need them.
- The host provides btrfs-progs, Samba, smartmontools, lm-sensors and Docker; `deploy/install.sh` installs them.
- The owner's initial commit (2026-01-23) set up this layout, with a privileged supervisor, the Docker socket and a separate web UI container; `pid: host` came the next day. On 2026-02-09 the project was renamed from Universal NAS Operating System to Universal NAS Open Software, because UNOS is a management layer on top of an existing Linux, not an operating system.

## Consequences

- Whoever controls the supervisor controls the host. In 1.0.0 every account could reach privileged APIs and so become root on the host; authorization (ADR-0030), input validation and the single host command layer (ADR-0020) now carry the security, and with them in place since Phase 0 the remaining risk is accepted for now.
- UNOS shares the host with the owner's own configuration, so it changes only what it marks as its own, for example its fstab entries (ADR-0034) and its SMB host accounts (ADR-0108).
- A command run with a plain `subprocess.run` changes only the container; host changes go through `supervisor/src/host.py`.

## Considered options

- An unprivileged web API plus a narrow privileged host agent (a systemd service with a small RPC), the audit's long-term option: still open, not chosen yet.
