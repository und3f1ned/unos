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
│   │   │   │   ├── ui/  # Reusable UI components
│   │   │   │   └── widgets/  # Dashboard widgets
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
- TypeScript, TailwindCSS (with custom design tokens)
- Lucide icons, Caddy (reverse proxy)

### Host Dependencies
- Docker Engine, btrfs-progs, smartmontools, lm-sensors, samba

---

## Design System

**IMPORTANT: All UI components MUST follow this design system. Consistency is mandatory.**

### Color Tokens (tailwind.config.js)

Use semantic color tokens instead of raw Tailwind colors:

| Token | Usage | Raw Value |
|-------|-------|-----------|
| `surface` | Main page background | slate-900 |
| `surface-elevated` | Cards, modals, sidebar | slate-800 |
| `surface-overlay` | Hover states, inputs | slate-700 |
| `surface-muted` | Subtle nested backgrounds | slate-700/50 |
| `border` | Card/section borders | slate-700 |
| `content` | Primary text | slate-100 |
| `content-secondary` | Secondary text, labels | slate-400 |
| `content-muted` | Disabled/hint text | slate-500 |

**Status Colors:**
| Token | Usage |
|-------|-------|
| `text-success` / `status-success` | Running, healthy, online |
| `text-warning` / `status-warning` | Degraded, warning states |
| `text-error` / `status-error` | Failed, errors, offline |
| `text-info` / `status-info` | Primary actions, links |

**Icon Accent Colors:**
| Class | Usage |
|-------|-------|
| `icon-cpu` | CPU metrics (blue) |
| `icon-memory` | Memory metrics (purple) |
| `icon-network` | Network metrics (green) |
| `icon-storage` | Storage metrics (blue) |
| `icon-container` | Container icons (cyan) |
| `icon-temperature` | Temperature/upload (orange) |

### UI Components ($lib/components/ui/)

**ALWAYS use these components instead of raw HTML:**

```typescript
import { Button, Card, Badge, Input, Select, Modal, Alert, ProgressBar, EmptyState, StatusDot } from '$lib/components/ui';
```

#### Button

```svelte
<!-- Variants: primary, secondary, danger, warning, success, ghost -->
<!-- Sizes: sm, md, lg, icon, icon-sm -->
<Button variant="primary" size="md" icon={Plus} loading={false}>
  Create Pool
</Button>
<Button variant="danger" size="sm">Delete</Button>
<Button variant="ghost" size="icon"><Trash2 class="w-4 h-4" /></Button>
```

#### Card

```svelte
<Card>
  <h2 class="section-title mb-4">
    <HardDrive class="w-5 h-5" />
    Physical Disks
  </h2>
  <!-- Content -->
</Card>

<!-- Nested card for items inside a card -->
<div class="card-nested">...</div>
```

#### Badge

```svelte
<!-- Variants: success, warning, error, info, neutral, purple, cyan, orange -->
<Badge variant="success">Running</Badge>
<Badge variant="warning">HDD</Badge>
<Badge variant="error">Failed</Badge>
```

#### Modal

```svelte
<Modal
  bind:open={showModal}
  title="Create Pool"
  size="lg"  <!-- sm, md, lg, xl -->
  danger={false}
  on:close={handleClose}
>
  <!-- Body content -->

  <svelte:fragment slot="footer">
    <Button variant="secondary" class="flex-1" on:click={close}>Cancel</Button>
    <Button variant="primary" class="flex-1" on:click={submit}>Create</Button>
  </svelte:fragment>
</Modal>
```

#### Alert

```svelte
<!-- Variants: error, warning, success, info -->
<Alert variant="error" title="Warning!">{message}</Alert>
<Alert variant="success">Operation completed</Alert>
```

#### Form Elements

```svelte
<Input
  label="Pool Name"
  bind:value={name}
  placeholder="data"
  hint="Optional hint text"
  error="Error message"
  required
/>

<Select label="RAID Level" bind:value={level} options={[
  { value: 'single', label: 'Single (No RAID)' },
  { value: 'raid1', label: 'RAID 1 (Mirroring)' }
]} />
```

#### ProgressBar

```svelte
<!-- variant: auto (changes color by value), success, warning, error, info -->
<ProgressBar value={75} variant="auto" size="sm" />
```

#### StatusDot

```svelte
<StatusDot status="success" pulse={false} />
```

### CSS Utility Classes (app.css)

**Layout:**
```html
<div class="page-container">  <!-- Main page wrapper with space-y-6 -->
<div class="page-header">     <!-- Flex between title and action -->
<h1 class="page-title">       <!-- 2xl bold -->
<h2 class="section-title">    <!-- lg semibold with icon gap -->
```

**Cards:**
```html
<div class="card">           <!-- bg-surface-elevated rounded-lg p-4 -->
<div class="card-header">    <!-- Flex between with mb-4 -->
<div class="card-title">     <!-- font-semibold with icon gap -->
<div class="card-nested">    <!-- bg-surface-muted for nested items -->
```

**Tables:**
```html
<table class="table">
<thead class="table-header">
<tr class="table-row">
```

**Navigation:**
```html
<a class="nav-item nav-item-active">  <!-- Active state -->
<a class="nav-item nav-item-inactive"> <!-- Inactive state -->
```

**Text:**
```html
<span class="text-primary">     <!-- Main text color -->
<span class="text-secondary">   <!-- Secondary/label text -->
<span class="text-muted">       <!-- Disabled/hint text -->
<span class="text-success">     <!-- Green status text -->
<span class="text-warning">     <!-- Yellow status text -->
<span class="text-error">       <!-- Red status text -->
<span class="text-info">        <!-- Blue info text -->
<span class="mono">             <!-- Monospace sm -->
<span class="mono-xs">          <!-- Monospace xs -->
```

**Loading/Empty States:**
```html
<p class="loading-text">Loading...</p>
<div class="empty-state">
  <Icon class="empty-state-icon" />
  <h3 class="empty-state-title">No data</h3>
  <p class="empty-state-description">Description text</p>
</div>
```

### Spacing Guidelines

| Element | Class |
|---------|-------|
| Page padding | `p-6` (handled by layout) |
| Card padding | `p-4` (default) |
| Section spacing | `space-y-6` |
| Item spacing in cards | `space-y-3` or `space-y-4` |
| Button gaps | `gap-2` |
| Grid gaps | `gap-4` |
| Modal footer | `flex gap-3` |

### Component Patterns

**Page Structure:**
```svelte
<div class="page-container">
  <div class="page-header">
    <h1 class="page-title">Storage</h1>
    <Button icon={Plus}>Create Pool</Button>
  </div>

  {#if error}
    <Alert variant="error">{error}</Alert>
  {/if}

  {#if loading}
    <p class="loading-text">Loading...</p>
  {:else}
    <Card>
      <h2 class="section-title mb-4">
        <HardDrive class="w-5 h-5" />
        Physical Disks
      </h2>
      <!-- Content -->
    </Card>
  {/if}
</div>
```

**Card with Actions:**
```svelte
<Card>
  <div class="card-header">
    <h3 class="card-title">
      <Container class="w-5 h-5 icon-container" />
      Containers
    </h3>
    <a href="/containers" class="text-sm text-info hover:text-blue-300">Manage →</a>
  </div>
  <!-- Content -->
</Card>
```

**Nested Items:**
```svelte
<div class="card-nested">
  <div class="flex items-center justify-between mb-2">
    <h3 class="font-semibold">{pool.name}</h3>
    <div class="flex items-center gap-2">
      <Button variant="secondary" size="sm">Action</Button>
    </div>
  </div>
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
    <div>
      <span class="text-secondary">Label:</span>
      <span class="ml-2">{value}</span>
    </div>
  </div>
</div>
```

### DO NOT

- Use raw `bg-slate-*`, `text-slate-*` colors directly. Use semantic tokens.
- Create inline modal/alert markup. Use `<Modal>` and `<Alert>` components.
- Use `<button>` with custom classes. Use `<Button>` component.
- Mix different button padding styles (`px-4 py-2` vs `px-3 py-1.5`).
- Create new styling patterns without updating this guide.

### DO

- Import from `$lib/components/ui` for all UI elements.
- Use CSS utility classes from `app.css` for consistent styling.
- Use semantic color classes (`text-success`, `icon-cpu`, `text-secondary`).
- Use `scrollbar-thin` class for scrollable areas.
- Follow the page/card/nested structure pattern.

---

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
- Use UI components from `$lib/components/ui`
- Components in `$lib/components/`, API in `$lib/api/`

```typescript
export async function getDisks(): Promise<Disk[]> {
  return api.get<Disk[]>('/storage/disks');
}
```

### Svelte Components
- `<script lang="ts">`, props via `export let`
- Use design system components and CSS classes
- Follow page structure patterns from Design System section

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
