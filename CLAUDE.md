# UNOS — Claude Code Instructions

## Quick Reference

| Item | Value |
|------|-------|
| Repository | https://github.com/und3f1ned/unos |
| License | GPL-3.0 |
| Device | ArmSoM Sige7 @ `10.1.1.103` |
| SSH | `sshpass -p 'ugreen' ssh root@10.1.1.103` |
| Project Path | `/opt/unos/` |
| Test Credentials | `admin` / `admin123` |

## Architecture

```
  unos-webui (SvelteKit + Caddy) :80/443
       ↓  /api/*, /ws/*
  unos-supervisor (FastAPI) :8080  →  SQLite: /data/unos.db
       ↓  Docker socket, privileged
  Host OS (Debian/Ubuntu) — btrfs, samba, smartmontools, docker
```

## Directory Structure

```
supervisor/src/         — Python FastAPI backend
  main.py, config.py, database.py, host.py (commands/files on the host)
  auth/ system/ storage/ shares/ containers/ websocket/
webui/src/              — SvelteKit frontend
  lib/api/  lib/stores/  lib/components/ui/  lib/components/widgets/
  routes/
deploy/                 — docker-compose.yml, Caddyfile, install/update/uninstall.sh
```

## Tech Stack

**Backend:** Python 3.11+, FastAPI, Uvicorn, SQLAlchemy+aiosqlite, PyJWT (JWT), bcrypt, docker-py, psutil, Pydantic v2
**Frontend:** SvelteKit 2.x (static adapter), TypeScript, TailwindCSS, Lucide icons, Caddy
**Host:** Docker Engine, btrfs-progs, smartmontools, lm-sensors, samba

## Design System

**All UI must use `$lib/components/ui` — never raw HTML elements:**
```typescript
import { Button, Card, Badge, Input, Select, Modal, Alert, ProgressBar, EmptyState, StatusDot } from '$lib/components/ui';
```

**Color tokens (use these, not raw `bg-slate-*` / `text-slate-*`):**
- Surfaces: `surface`, `surface-elevated`, `surface-overlay`, `surface-muted`
- Text: `content`, `content-secondary`, `content-muted`
- Status: `text-success/warning/error/info`, `status-success/warning/error/info`
- Icons: `icon-cpu` (blue), `icon-memory` (purple), `icon-network` (green), `icon-storage` (blue), `icon-container` (cyan), `icon-temperature` (orange)

**CSS classes** (defined in `app.css`): `page-container`, `page-header`, `page-title`, `section-title`, `card`, `card-header`, `card-title`, `card-nested`, `table`, `table-header`, `table-row`, `nav-item`, `text-primary/secondary/muted`, `mono`, `loading-text`, `empty-state`

**Rules:**
- Use `<Button>` not `<button>`, `<Modal>` not custom markup, `<Alert>` not inline alerts
- Use semantic tokens, not raw Tailwind colors
- Follow page/card/nested structure (see existing pages for patterns)

## API Endpoints

```
POST   /api/v1/auth/login          GET  /api/v1/auth/me
GET    /api/v1/system/info         GET  /api/v1/system/stats
GET    /api/v1/storage/disks       GET  /api/v1/storage/disks/{name}/smart
POST   /api/v1/storage/pools       GET  /api/v1/storage/pools
DELETE /api/v1/storage/pools/{name}
GET    /api/v1/shares/smb          POST /api/v1/shares/smb
DELETE /api/v1/shares/smb/{name}
GET    /api/v1/containers          POST /api/v1/containers/{id}/start|stop
GET    /api/v1/containers/{id}/logs
WS     /ws/v1/events               # Real-time stats (2s interval)
WS     /ws/v1/logs/{container_id}  # Container log streaming
```

## WebSocket — Primary Data Channel

**All real-time data MUST use WebSocket, NOT HTTP polling.**

```typescript
import { stats, wsConnected, formatBytes, formatBytesPerSecond } from '$lib/stores/stats';
// Derived stores: cpuStats, memoryStats, networkStats, diskIOStats, processes
$: cpuPercent = $stats?.cpu?.percent ?? 0;
```

Do NOT poll `/api/v1/system/stats` with setInterval. Do NOT calculate rates manually.
Backend: `supervisor/src/websocket/events.py` → `get_system_stats()`.

## Coding Standards

**Python:** async/await for I/O, Pydantic models, type hints, `subprocess.run(capture_output=True)`, parse JSON from CLI tools (`lsblk -J`, `smartctl -j`), errors via `HTTPException`. Anything that must change the host (mounts, `/etc/fstab`, Samba reloads, ...) goes through `src/host.py` (`run_on_host`, nsenter into PID 1's namespaces); a plain `subprocess.run` only affects the container. Tests mock it.

**TypeScript:** strict mode, API client wraps fetch with JWT, Svelte stores for shared state, components in `$lib/components/`, API in `$lib/api/`.

**Svelte:** `<script lang="ts">`, props via `export let`, use design system components.

## Key Implementation Notes

- **Storage:** `lsblk -J -b -o NAME,PATH,MODEL,SERIAL,SIZE,ROTA,MOUNTPOINT,TYPE`, `smartctl -j -a /dev/sdX`, `mkfs.btrfs -L {name} -d {profile} -m {profile} {devices}`, mount on the host at `/mnt/pools/{name}` by `UUID=`, with a managed entry in the host's `/etc/fstab` (`# unos-pool:{name} (managed by UNOS)` marker line)
- **SMB:** generate `/etc/samba/smb.conf` from state, reload with `smbcontrol all reload-config`
- **Docker:** docker-py via `/var/run/docker.sock`
- **Auth:** JWT 24h expiry, Bearer header; secret generated into `/data/jwt_secret` unless `UNOS_JWT_SECRET` is set (published placeholders are refused); first admin created on the `/setup` page (`POST /api/v1/auth/setup`, only while no user exists); WebSocket clients authenticate with their first message `{"type": "auth", "token": ...}`, never `?token=`; routers are admin-only as a whole (`require_admin` in `main.py`) except auth, system, security, audit and the WebSockets, which check roles themselves
- **Security:** validate device paths, sanitize share names (alphanumeric + underscore), rate limit logins

## Environment Variables

```
UNOS_DB_PATH=/data/unos.db  UNOS_JWT_SECRET=  UNOS_JWT_ALGORITHM=HS256
UNOS_JWT_EXPIRE_MINUTES=1440  UNOS_SMB_CONF_PATH=/etc/samba/smb.conf
UNOS_POOLS_BASE_PATH=/mnt/pools
```

## Development & Deployment

```bash
SSH="sshpass -p 'ugreen' ssh root@10.1.1.103"
SCP="sshpass -p 'ugreen' scp"

# Deploy (changes require container rebuild!)
$SSH 'cd /opt/unos/deploy && docker compose build webui && docker compose up -d webui'       # frontend
$SSH 'cd /opt/unos/deploy && docker compose build supervisor && docker compose up -d supervisor' # backend
$SSH 'cd /opt/unos/deploy && docker compose build && docker compose up -d'                    # both
# After a change to the networks section (e.g. the fixed subnet 172.31.254.0/24), recreate the network once:
$SSH 'cd /opt/unos/deploy && docker compose build && docker compose down && docker compose up -d'
```

The UI is HTTPS-only (`https://10.1.1.103`; plain HTTP redirects, except `/api/v1/health`).

**Verify:** `docker ps | grep unos` (recent creation time), hard refresh (Ctrl+Shift+R), `docker logs unos-supervisor --tail 50`, use `--no-cache` if changes don't appear.

## Testing

```bash
npx playwright test                    # all tests
npx playwright test api.spec.ts        # specific file
```

Every feature needs: backend tests in `supervisor/tests/`, frontend E2E in `webui/tests/`.
