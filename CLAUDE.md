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
┌─────────────────────────────────────────────────────┐
│  unos-webui (SvelteKit + Caddy)                     │
│  Port: 80/443                                       │
└──────────────────────┬──────────────────────────────┘
                       │ /api/*, /ws/*
┌──────────────────────▼──────────────────────────────┐
│  unos-supervisor (Python FastAPI)                   │
│  Port: 8080 (internal)                              │
│  SQLite: /data/unos.db                              │
└──────────────────────┬──────────────────────────────┘
                       │ Docker socket, privileged
┌──────────────────────▼──────────────────────────────┐
│  Host OS (Debian/Ubuntu)                            │
│  btrfs, samba, smartmontools, docker                │
└─────────────────────────────────────────────────────┘
```

## Directory Structure

```
unos/
├── supervisor/           # Python FastAPI backend
│   ├── src/
│   │   ├── main.py      # App entry, router registration
│   │   ├── config.py    # Settings (env vars)
│   │   ├── database.py  # SQLite async setup
│   │   ├── auth/        # JWT auth, user model
│   │   ├── system/      # Stats, info, logs
│   │   ├── storage/     # Disks, pools (btrfs)
│   │   ├── shares/      # SMB management
│   │   ├── containers/  # Docker API wrapper
│   │   └── websocket/   # Real-time events
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── webui/                # SvelteKit frontend
│   ├── src/
│   │   ├── lib/
│   │   │   ├── api/     # API client modules
│   │   │   ├── stores/  # Svelte stores
│   │   │   ├── components/
│   │   │   └── utils/
│   │   └── routes/      # Pages
│   ├── tests/
│   ├── Dockerfile
│   └── package.json
├── deploy/
│   ├── docker-compose.yml
│   ├── Caddyfile
│   ├── install.sh
│   ├── update.sh
│   └── uninstall.sh
└── docs/
```

## Tech Stack

### Supervisor (Backend)
- Python 3.11+, FastAPI, Uvicorn
- SQLAlchemy + aiosqlite (async SQLite)
- python-jose (JWT), passlib (bcrypt)
- docker-py, psutil, Pydantic v2

### WebUI (Frontend)
- SvelteKit 2.x (static adapter)
- TypeScript, TailwindCSS
- Lucide icons, Caddy (reverse proxy)

### Host Dependencies
- Docker Engine, btrfs-progs, smartmontools, lm-sensors, samba

## API Endpoints

```
POST   /api/v1/auth/login
GET    /api/v1/auth/me

GET    /api/v1/system/info
GET    /api/v1/system/stats

GET    /api/v1/storage/disks
GET    /api/v1/storage/disks/{name}/smart
POST   /api/v1/storage/pools
GET    /api/v1/storage/pools
DELETE /api/v1/storage/pools/{name}

GET    /api/v1/shares/smb
POST   /api/v1/shares/smb
DELETE /api/v1/shares/smb/{name}

GET    /api/v1/containers
POST   /api/v1/containers/{id}/start
POST   /api/v1/containers/{id}/stop
GET    /api/v1/containers/{id}/logs

WS     /ws/v1/events              # Real-time stats (use this, NOT HTTP polling!)
WS     /ws/v1/logs/{container_id} # Container log streaming
```

**Note:** For real-time data (system stats, network rates, processes), always use WebSocket.
HTTP endpoints like `/api/v1/system/stats` are for initial load only, not polling.

## Coding Standards

### Python (Supervisor)
- Use async/await for all I/O
- Pydantic models for request/response schemas
- Type hints required
- `subprocess.run()` with `capture_output=True`
- Parse JSON from CLI tools (lsblk -J, smartctl -j)
- Handle errors with `HTTPException(status_code, detail="message")`

```python
@router.get("/disks", response_model=list[DiskInfo])
async def list_disks(_: dict = Depends(verify_token)):
    result = subprocess.run(["lsblk", "-J", "-o", "NAME,SIZE,MODEL"], capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(500, detail="lsblk failed")
    return [DiskInfo(**d) for d in json.loads(result.stdout)["blockdevices"]]
```

### TypeScript (WebUI)
- Strict TypeScript
- API client wraps fetch, handles JWT
- Svelte stores for shared state
- TailwindCSS, no custom CSS unless necessary
- Components in `$lib/components/`, API in `$lib/api/`

```typescript
export async function getDisks(): Promise<Disk[]> {
  return api.get<Disk[]>('/storage/disks');
}
```

### Svelte Components
- `<script lang="ts">`, props via `export let`
- Tailwind classes directly on elements

## Key Implementation Notes

### Storage Operations
- `lsblk -J -b -o NAME,PATH,MODEL,SERIAL,SIZE,ROTA,MOUNTPOINT,TYPE`
- `smartctl -j -a /dev/sdX` for SMART data
- `mkfs.btrfs -L {name} -d {profile} -m {profile} {devices}`
- Mount at `/mnt/pools/{name}`, add to `/etc/fstab` with `LABEL={name}`

### SMB Management
- Generate `/etc/samba/smb.conf` from internal state
- Reload with `smbcontrol all reload-config`

### Docker Container Management
- Use docker-py via `/var/run/docker.sock`

### WebSocket Architecture (Primary Data Channel)

**IMPORTANT: All real-time data MUST use WebSocket, NOT HTTP polling.**

The WebSocket at `/ws/v1/events` is the primary channel for all real-time system data. Components should subscribe to the Svelte store `$lib/stores/stats.ts` instead of polling HTTP endpoints.

#### WebSocket Message Structure

```typescript
// Message type
{ type: "stats", data: WebSocketStats }

// WebSocketStats contains:
interface WebSocketStats {
  // Basic stats (backward compatible)
  cpu_percent: number;
  memory_total/used/percent: number;
  disk_total/used/percent: number;
  network_sent/recv: number;
  temperatures: Record<string, number>;

  // Extended stats (use these!)
  cpu: {
    percent, per_core[], load_1/5/15, cores, threads
  };
  memory: {
    total, used, free, available, percent,
    swap_total, swap_used, swap_percent
  };
  network: {
    bytes_sent/recv, bytes_sent_rate/recv_rate,
    packets_sent/recv,
    interfaces: [{
      name, ipv4, speed, is_up,
      bytes_sent/recv, bytes_sent_rate/recv_rate
    }]
  };
  disk_io: {
    read_bytes, write_bytes, read_rate, write_rate,
    read_count, write_count,
    disks: [{ name, read_bytes, write_bytes, ... }]
  };
  processes: ProcessInfo[];
  history: { cpu[], memory[], network[], disk_io[] };
  timestamp: string;
}
```

#### Frontend Usage

```typescript
// In Svelte components - subscribe to store
import { stats, wsConnected, formatBytes, formatBytesPerSecond } from '$lib/stores/stats';

// Use reactive statements
$: cpuPercent = $stats?.cpu?.percent ?? 0;
$: networkRate = $stats?.network?.bytes_recv_rate ?? 0;
$: interfaces = $stats?.network?.interfaces ?? [];

// Show connection status
{#if !$wsConnected}
  <span>WebSocket disconnected</span>
{/if}
```

#### DO NOT
- Poll `/api/v1/system/stats` with setInterval
- Poll `/api/v1/monitor/*` endpoints
- Calculate network rates manually from cumulative counters

#### DO
- Subscribe to `$lib/stores/stats.ts`
- Use derived stores: `cpuStats`, `memoryStats`, `networkStats`, `diskIOStats`, `processes`
- Use `formatBytes()` and `formatBytesPerSecond()` from the store

#### WebSocket Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/ws/v1/events` | System stats broadcast (2s interval) |
| `/ws/v1/logs/{container_id}` | Container log streaming |

#### Backend Implementation

Stats are broadcast from `supervisor/src/websocket/events.py`. The `get_system_stats()` function collects all data including rate calculations.

### Authentication
- JWT tokens, 24h expiry, Bearer header
- Initial admin from `/etc/unos/init-user.json`

## Environment Variables

```bash
UNOS_DB_PATH=/data/unos.db
UNOS_JWT_SECRET=your-secret-key
UNOS_JWT_ALGORITHM=HS256
UNOS_JWT_EXPIRE_MINUTES=1440
UNOS_SMB_CONF_PATH=/etc/samba/smb.conf
UNOS_POOLS_BASE_PATH=/mnt/pools
```

## Security

- JWT secret must be changed in production
- Supervisor runs privileged (required for storage ops)
- Validate all device paths before operations
- Sanitize share names (alphanumeric + underscore only)
- Rate limit login attempts

## Development

All development happens on the ArmSoM device via SSH. Define shorthand:

```bash
SSH="sshpass -p 'ugreen' ssh root@10.1.1.103"
SCP="sshpass -p 'ugreen' scp"
```

### Making Changes

```bash
# Write file
$SSH 'cat > /opt/unos/path/to/file << "EOF"
content
EOF'

# Or use scp for complex files
$SCP /tmp/file root@10.1.1.103:/opt/unos/path/
```

### Deploying Changes

Changes require container rebuild — editing files is not enough.

```bash
# Frontend
$SSH 'cd /opt/unos/deploy && docker compose build webui && docker compose up -d webui'

# Backend
$SSH 'cd /opt/unos/deploy && docker compose build supervisor && docker compose up -d supervisor'

# Both
$SSH 'cd /opt/unos/deploy && docker compose build && docker compose up -d'
```

### Verification Checklist

- [ ] `docker ps | grep unos` shows recent creation time
- [ ] Hard refresh browser (Ctrl+Shift+R)
- [ ] Check logs: `docker logs unos-supervisor --tail 50`
- [ ] Use `docker compose build --no-cache` if changes don't appear

### Git Workflow

```bash
$SSH 'cd /opt/unos && git status'
$SSH 'cd /opt/unos && git add -A && git commit -m "message"'
$SSH 'cd /opt/unos && git push origin main'
```

## Testing

### Running Tests

```bash
cd /Users/xedin/Downloads/unos-tests
npx playwright test                    # all tests
npx playwright test api.spec.ts        # specific file
npx playwright test --headed           # with UI
```

### Test Structure

```
webui/tests/
├── fixtures.ts          # Shared auth setup
├── api.spec.ts          # API tests
├── auth.spec.ts         # Auth tests
├── dashboard.spec.ts    # Dashboard tests
├── storage.spec.ts      # Storage tests
├── shares.spec.ts       # Shares tests
├── containers.spec.ts   # Container tests
└── ...
```

### Requirements
Every feature needs:
1. Backend API tests in `supervisor/tests/`
2. Frontend E2E tests in `webui/tests/`
3. Tests must pass before moving on

## End User Deployment

### Install
```bash
curl -fsSL https://raw.githubusercontent.com/und3f1ned/unos/main/deploy/install.sh | sudo bash
```

### Update
```bash
sudo /opt/unos/deploy/update.sh
```

### Uninstall
```bash
sudo /opt/unos/deploy/uninstall.sh
```

## Documentation

| File | Purpose |
|------|---------|
| `README.md` | Project overview, features |
| `docs/INSTALL.md` | Installation guide |
| `docs/USER_GUIDE.md` | User documentation |
| `CLAUDE.md` | Developer instructions |
