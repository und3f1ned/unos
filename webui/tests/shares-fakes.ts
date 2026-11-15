import { expect, type Page, type WebSocketRoute } from '@playwright/test';

// Canned answers of the SMB share, NFS export, user and account APIs for UI
// tests that must never change a real share, export, user or SMB account.
// fakeSmbApi() answers every request of the page to /api/v1/shares/*,
// /api/v1/auth/users* and /api/v1/auth/me/smb*: writes are recorded and applied
// to the fake state (so the page shows their outcome), unknown requests are
// aborted, and nothing reaches the supervisor. Sign-in (/auth/login, /auth/me)
// is not faked.

/** Pools base of the stand-in the specs run against (the local one differs). */
export const POOLS = process.env.E2E_POOLS_BASE || '/mnt/pools';

// The texts the server uses, word for word (supervisor/src/shares/account_state.py
// and service.py).
// Administrators never have SMB access (ADR-0117).
export const ADMIN_REASON =
  'Administrators cannot use SMB: their password controls the whole NAS, and SMB has no ' +
  'two-factor authentication and no login rate limit. Use a regular account for files.';
// Guest access is off by default, and guest shares are read-only (ADR-0096).
export const GUEST_WARNING =
  'Anyone on your network can read this share without a password, and its name is visible to ' +
  'anyone on your network. Guests can never write. While a guest share exists, connections to ' +
  'this server are not required to be signed. Windows 11 refuses guest access by default, and ' +
  'guest connections are neither signed nor encrypted.';
// A guest share cannot require encryption, and Time Machine needs Samba's vfs modules (ADR-0377):
// service.GUEST_ENCRYPTION and service.modules_problem('time_machine', ['fruit']);
// supervisor/tests/test_share_options.py checks both against the server.
export const GUEST_ENCRYPTION =
  "A guest share cannot require encryption: guests' connections are never encrypted, so no guest could open it";
export const TIME_MACHINE_MODULES =
  'Samba on this NAS lacks the vfs module fruit that Time Machine needs: install the package samba-vfs-modules on the host';
// Why Previous Versions of a folder in no snapshot of UNOS are left out of smb.conf:
// service.no_snapshots_problem('/mnt/pools/data/media/restore_20261112_100000'), checked by
// supervisor/tests/test_share_options.py.
export const NO_SNAPSHOTS =
  '/mnt/pools/data/media/restore_20261112_100000 is a subvolume or a file system of its own: UNOS makes ' +
  "snapshots of pools and volumes only, and none of them holds this share's files, so Previous Versions " +
  'would list nothing';
// A change sent with an outdated expected_version (ADR-0135).
export const VERSION_CONFLICT = 'The share was changed meanwhile; reload';
// Why a share of a pool that UNOS does not manage, or no longer manages after an unmount, is not
// served (ADR-0148), the status problem that names a share that is not served, and the status
// problem of an smb.conf replaced after UNOS wrote it (ADR-0249);
// supervisor/tests/test_share_api.py checks all three against the server.
export const unmanagedPoolProblem = (pool: string) =>
  `Share path must be on a storage pool that UNOS manages: '${pool}' is not one`;
export const notServedProblem = (share: string, problem: string) => `Share ${share} is not served: ${problem}`;
export const SMB_CONF_REPLACED =
  'smb.conf was replaced or removed after UNOS wrote it; it is written again at the next change or restart, and SMB cannot be turned on until then';
// Why a password on the list of common passwords cannot be an SMB password (ADR-0279,
// constants.COMMON_PASSWORD_RULE); supervisor/tests/test_smb_accounts.py checks it against the server.
export const COMMON_PASSWORD_RULE =
  'SMB passwords must not be common passwords: this one is on the list of passwords that attackers try first';
// The name rule of new and renamed users (constants.LINUX_NAME_PATTERN and NAME_RULE), and the
// refusal of a rename without a password while SMB is on (ADR-0368); supervisor/tests/test_smb_accounts.py
// checks them against the server.
const USERNAME_RULE = /^[a-z][a-z0-9_-]{0,31}$/;
export const NAME_RULE_TEXT =
  "Use 1 to 32 characters: lowercase letters, digits, '_' and '-', starting with a letter";
export const renameNeedsPassword = (name: string) =>
  `A new password is needed to rename ${name}: SMB is on for ${name}, and Samba's account of the new name is made with it`;

// The answers of supervisor/src/shares/nfs.py (ADR-0292, ADR-0293), word for word;
// supervisor/tests/test_nfs.py checks them against the server.
export const NFS_SERVER_MISSING =
  'The host has no NFS server: exportfs was not found. Install the package nfs-kernel-server on the host; it applies the exports when it starts.';
export const NFS_CLIENT_SYNTAX = 'The client must be an IP address or network, such as 192.168.1.0/24 or fd00::/64';
export const NFS_POOL_ROOT =
  'Export a folder inside the pool or a volume: the top level of a pool holds app data (apps/), volumes and snapshots';
export const nfsClientNotLocal = (client: string) =>
  `${client} is not on your local network: NFS asks for no password, so UNOS exports only to private, unique-local and link-local addresses`;
export const nfsExportedAlready = (path: string) => `${path} is exported by UNOS already: delete that export first`;
export const nfsNotExported = (path: string) => `UNOS does not export ${path}`;
export const nfsForeignExport = (path: string, file: string, line: number) =>
  `The export of ${path} is in ${file}, line ${line}, which UNOS does not change: edit that file on the NAS`;
/** The refusal of an add or a delete while an export file has a line that exportfs cannot read. */
export const nfsFileUnreadable = (file: string, line: number) =>
  `${file}, line ${line} cannot be read by exportfs, which then reads no further in that file: after a change it would stop exporting what follows the line. Correct or remove the line on the NAS first`;
/** How GET /shares/nfs-status names a line that exportfs cannot read. */
export const nfsFileProblem = (file: string, line: number, message: string) =>
  `${file}, line ${line}: ${message}; exportfs reads no further in this file, and UNOS adds and deletes no export until the line is corrected`;
/** How GET /shares/nfs-status names an export of UNOS below a folder that other accounts can change. */
export const nfsUnsafeExport = (path: string, folder: string, detail: string, commands: string) =>
  `NFS export ${path}: ${folder} can be changed by other accounts (${detail}), so they can put a symbolic link in place of the exported folder or a folder above it, and exportfs would export the link's target. Run ${commands} on the NAS.`;
/** The export file of UNOS, which it alone changes; its first two lines are a comment. */
export const NFS_UNOS_FILE = '/etc/exports.d/unos.exports';

export type SmbState = 'active' | 'password_needed' | 'off' | 'unavailable';
export type Level = 'read' | 'write';

export interface FakeSmbStatus {
  state: SmbState;
  allowed: boolean;
  linux_name: string | null;
  uid: number | null;
  password_set_at: string | null;
  reason: string | null;
}

export interface FakeUser {
  id: number;
  username: string;
  is_admin: boolean;
  totp_enabled: boolean;
  created_at: string | null;
  smb: FakeSmbStatus;
}

export interface FakeAccess {
  user_id: number;
  username: string;
  level: Level;
  smb_state: SmbState;
  effective: boolean;
}

export interface FakeShare {
  name: string;
  path: string;
  comment: string;
  browseable: boolean;
  guest_ok: boolean;
  access: FakeAccess[];
  version: number;
  served: boolean;
  path_problem: string | null;
  available: boolean;
  pool: string | null;
  pool_mounted: boolean | null;
  permissions_ok: boolean | null;
  import_notes: string[];
  encryption_required: boolean;
  time_machine: boolean;
  previous_versions: boolean;
  not_rendered: Partial<Record<'guest_ok' | 'time_machine' | 'previous_versions', string>>;
}

export interface FakeServiceStatus {
  samba_tools_missing: string[] | null;
  group_ok: boolean | null;
  problems: string[];
  guest_shares: string[];
  import_report: string[];
  unmanaged_samba_users: string[];
  active_smb_users: number;
}

export interface FakeRepairResult {
  share: string;
  state: 'running' | 'done' | 'incomplete' | 'failed';
  started_at: string;
  /** null while it runs. */
  finished_at: string | null;
  visited: number;
  changed: number;
  skipped: Record<string, number>;
  skipped_paths: string[];
  errors: number;
  error_samples: string[];
  message: string;
}

export interface FakeMySmb {
  account: FakeSmbStatus;
  totp_required: boolean;
  shares: { name: string; comment: string; level: Level; guest_ok: boolean; usable: boolean }[];
}

export interface FakeNfsClient {
  client: string;
  options: string[];
  access: Level | null;
}

export interface FakeNfsExport {
  path: string;
  clients: FakeNfsClient[];
  managed: boolean;
  file: string;
  line: number;
}

export interface FakeNfsStatus {
  server_missing: boolean | null;
  problems: string[];
}

/** A line of an export file that exportfs cannot read. */
export interface FakeNfsUnreadable {
  file: string;
  line: number;
  message: string;
}

export interface SharePathInfo {
  path: string;
  /** The pool's name, or pool/volume: the page adds the kind, "tank (Pool)" (ADR-0299). */
  name: string;
  type: 'pool' | 'volume';
  /** Its pool is one that UNOS manages: only such locations can hold SMB shares. */
  managed: boolean;
}

export interface RecordedWrite {
  method: string;
  /** Below /api/v1, with the query string, e.g. "/shares/smb/docs" or "/auth/users/2?username=alice". */
  path: string;
  body: unknown;
}

export interface FakeResponse {
  status: number;
  json: unknown;
}

export interface FakeRequest {
  method: string;
  /** Below /api/v1, without the query string. */
  path: string;
  /** The query string, e.g. "?username=alice", or "". */
  search: string;
  body: unknown;
}

export interface FakeSmbApi {
  users: FakeUser[];
  shares: FakeShare[];
  status: FakeServiceStatus;
  paths: SharePathInfo[];
  /** Last finished repair per share name (lower case), as GET .../repair-permissions answers. */
  repairs: Record<string, FakeRepairResult>;
  /** Answer of POST .../repair-permissions (default: 202 with the run started, running). */
  repairAnswer: FakeResponse | null;
  /** The share whose repair runs since a POST answered 202, until fakeRepairEvents' end(). */
  repairing: string | null;
  /** Answer of DELETE .../repair-permissions (default: 202 for the running repair, else 409). */
  repairStopAnswer: FakeResponse | null;
  /** Warnings of the answers to share changes (create, PATCH, delete), e.g. a failed reload. */
  changeWarnings: string[];
  /**
   * Warnings of the answers to user changes (PUT, SMB enable and disable,
   * delete), e.g. "An SMB connection of alice could not be ended: ...".
   */
  userWarnings: string[];
  mySmb: FakeMySmb;
  /** The entries of the host's export files, as GET /shares/nfs answers them. */
  nfsExports: FakeNfsExport[];
  /** The status; GET /shares/nfs-status lists the lines of nfsUnreadable before its problems. */
  nfsStatus: FakeNfsStatus;
  /** Lines that exportfs cannot read: while there is one, adds and deletes are refused. */
  nfsUnreadable: FakeNfsUnreadable[];
  /** Warnings of the answers to export changes: what exportfs reported. */
  nfsWarnings: string[];
  /** The current password and the 2FA code POST /auth/me/smb/enable accepts. */
  myPassword: string;
  myTotpCode: string;
  /** Write requests, in order. */
  writes: RecordedWrite[];
  /** GET requests, in order (paths below /api/v1). */
  reads: string[];
  /** Answers a request before the fake does; return undefined to let the fake answer. */
  intercept: ((request: FakeRequest) => FakeResponse | undefined) | null;
}

// ---- Builders ----

export function smbStatus(state: SmbState, username: string, reason: string | null = null): FakeSmbStatus {
  const allowed = state === 'active' || state === 'password_needed';
  return {
    state,
    allowed,
    linux_name: allowed ? username : null,
    uid: state === 'active' ? 70000 : null,
    password_set_at: state === 'active' ? '2026-11-01T10:00:00' : null,
    reason: state === 'unavailable' ? reason ?? ADMIN_REASON : null,
  };
}

export function fakeUser(
  id: number,
  username: string,
  state: SmbState,
  extra: Partial<Omit<FakeUser, 'smb'>> & { reason?: string } = {},
): FakeUser {
  const { reason, ...rest } = extra;
  return {
    id,
    username,
    is_admin: false,
    totp_enabled: false,
    created_at: '2026-10-01T09:00:00',
    ...rest,
    smb: smbStatus(state, username, reason ?? null),
  };
}

export function fakeShare(name: string, extra: Partial<FakeShare> = {}): FakeShare {
  return {
    name,
    path: `${POOLS}/tank/${name}`,
    comment: '',
    browseable: true,
    guest_ok: false,
    access: [],
    version: 1,
    served: true,
    path_problem: null,
    available: false,
    pool: 'tank',
    pool_mounted: true,
    permissions_ok: true,
    import_notes: [],
    encryption_required: false,
    time_machine: false,
    previous_versions: false,
    not_rendered: {},
    ...extra,
  };
}

/** Access entries as GET /shares/smb reports them, for `users`. */
export function accessOf(
  users: FakeUser[],
  entries: { user_id: number; username: string; level: Level }[],
  served = true,
): FakeAccess[] {
  return entries
    .map((entry) => {
      const user = users.find((u) => u.id === entry.user_id && u.username === entry.username);
      const state: SmbState = user?.smb.state ?? 'off';
      return { ...entry, smb_state: state, effective: served && state === 'active' };
    })
    .sort((a, b) => a.username.localeCompare(b.username));
}

/** Names in the order of the server's sorted(..., key=str.lower). */
const byLowerCase = (a: string, b: string) => {
  const [x, y] = [a.toLowerCase(), b.toLowerCase()];
  return x < y ? -1 : x > y ? 1 : 0;
};

/**
 * As the server after an unmount or a mount on the Storage page (ADR-0248): the shares of a pool
 * that is not mounted, which UNOS then no longer manages, are not served and their access does
 * not count, and the SMB status names each of them as a problem (appended to the problems) and
 * lists none of them as a guest share; once the pool is mounted again they are served again,
 * with the access they kept.
 */
export function setPoolMounted(fake: FakeSmbApi, pool: string, mounted: boolean): void {
  const shares = fake.shares.filter((share) => share.pool === pool);
  for (const share of shares) {
    share.served = mounted;
    share.path_problem = mounted ? null : unmanagedPoolProblem(pool);
    share.pool_mounted = mounted;
    share.permissions_ok = mounted ? true : null;
    share.access = accessOf(fake.users, share.access, mounted);
    share.available = mounted && (share.guest_ok || share.access.some((entry) => entry.effective));
  }
  // New arrays: the default status's arrays are shared by every fake.
  const problems = shares.map((share) => notServedProblem(share.name, unmanagedPoolProblem(pool)));
  const others = fake.status.problems.filter((problem) => !problems.includes(problem));
  fake.status.problems = mounted ? others : [...others, ...problems];
  const names = new Set(shares.map((share) => share.name));
  const kept = fake.status.guest_shares.filter((name) => !names.has(name));
  const guests = mounted ? shares.filter((share) => share.guest_ok).map((share) => share.name) : [];
  fake.status.guest_shares = [...kept, ...guests].sort(byLowerCase);
}

// The answer to a stop of a share that no repair runs for (supervisor/src/shares/repair_router.py).
export const REPAIR_NOT_RUNNING = 'No permissions repair of this share is running';
// The message of a repair that an administrator stopped (supervisor/src/shares/repair.py).
export const REPAIR_STOPPED =
  'Stopped by an administrator; run it again to continue (already repaired entries are skipped quickly)';

/** A repair as it starts: running, nothing counted yet. */
export function repairStarted(share: string, extra: Partial<FakeRepairResult> = {}): FakeRepairResult {
  return repairResult(share, { state: 'running', finished_at: null, visited: 0, changed: 0, ...extra });
}

export function repairResult(share: string, extra: Partial<FakeRepairResult> = {}): FakeRepairResult {
  return {
    share,
    state: 'done',
    started_at: '2026-11-06T10:00:00',
    finished_at: '2026-11-06T10:00:05',
    visited: 120,
    changed: 37,
    skipped: { other_filesystem: 0, snapshots: 0, symlink: 0, special: 0, hardlink: 0, private: 0, too_deep: 0, raced: 0 },
    skipped_paths: [],
    errors: 0,
    error_samples: [],
    message: '',
    ...extra,
  };
}

/** An export that UNOS wrote, as GET /shares/nfs lists it: its line in the export file of UNOS. */
export function fakeNfsExport(path: string, client: string, access: Level, line = 3): FakeNfsExport {
  const pool = path.slice(POOLS.length + 1).split('/')[0];
  const options = [access === 'write' ? 'rw' : 'ro', 'sync', 'no_subtree_check', 'root_squash', 'secure', `mp=${POOLS}/${pool}`];
  return { path, clients: [{ client, options, access }], managed: true, file: NFS_UNOS_FILE, line };
}

/**
 * As shares/nfs.py for the clients the specs send: an address or network of the local network
 * (loopback, RFC 1918, CGNAT, link-local, IPv6 ULA). Prefix lengths are not checked.
 */
function nfsClientProblem(client: string): string | null {
  if (!/^[0-9A-Fa-f.:/]{1,64}$/.test(client) || !/[.:]/.test(client)) return NFS_CLIENT_SYNTAX;
  const address = client.split('/')[0].toLowerCase();
  const local =
    /^(10|127)\./.test(address) ||
    /^192\.168\./.test(address) ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(address) ||
    /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(address) ||
    /^169\.254\./.test(address) ||
    /^(f[cd]|fe[89ab])[0-9a-f]*:/.test(address) ||
    address === '::1';
  return local ? null : nfsClientNotLocal(client);
}

export const FAKE_PATHS: SharePathInfo[] = [
  { path: `${POOLS}/tank`, name: 'tank', type: 'pool', managed: true },
  { path: `${POOLS}/tank/media`, name: 'tank/media', type: 'volume', managed: true },
];

export interface FakeSmbOptions {
  users?: FakeUser[];
  shares?: FakeShare[];
  status?: Partial<FakeServiceStatus>;
  paths?: SharePathInfo[];
  mySmb?: FakeMySmb;
  nfsExports?: FakeNfsExport[];
  nfsStatus?: Partial<FakeNfsStatus>;
  nfsUnreadable?: FakeNfsUnreadable[];
}

const OK_STATUS: FakeServiceStatus = {
  samba_tools_missing: [],
  group_ok: true,
  problems: [],
  guest_shares: [],
  import_report: [],
  unmanaged_samba_users: [],
  active_smb_users: 1,
};

function jsonBody(raw: string | null): unknown {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function detail(status: number, message: string): FakeResponse {
  return { status, json: { detail: message } };
}

function ok(json: unknown): FakeResponse {
  return { status: 200, json };
}

/** FastAPI's 422 for a missing parameter (main.py drops "input" and "ctx"). */
function missing(where: 'query' | 'body', name?: string): FakeResponse {
  const loc = name ? [where, name] : [where];
  return { status: 422, json: { detail: [{ type: 'missing', loc, msg: 'Field required' }] } };
}

type AccessEntry = { user_id: number; username: string; level: Level };

const entryKey = (entry: AccessEntry) => `${entry.user_id}:${entry.username}:${entry.level}`;

/** The same access entries (user id, username, level), in any order. */
function sameEntries(a: AccessEntry[], b: AccessEntry[]): boolean {
  return a.map(entryKey).sort().join('|') === b.map(entryKey).sort().join('|');
}

/**
 * Stand in for the events socket /ws/v1/events (supervisor/src/websocket/events.py), which sends
 * administrators the repair of a share's permissions while it runs and when it ends (ADR-0363): the
 * fake answers the page's auth message with auth_ok; progress() sends a running repair, and end()
 * keeps a finished one as its share's last run, which GET .../repair-permissions of `fake` then
 * answers, and sends it.
 */
export async function fakeRepairEvents(
  page: Page,
  fake: FakeSmbApi,
): Promise<{ progress: (job: FakeRepairResult) => Promise<void>; end: (result: FakeRepairResult) => Promise<void> }> {
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    ws.onMessage((message) => {
      if (JSON.parse(String(message)).type !== 'auth') return;
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      socket = ws;
    });
  });
  const send = async (job: FakeRepairResult) => {
    await expect.poll(() => socket !== null, { message: 'the page signed in to the events socket' }).toBe(true);
    socket!.send(JSON.stringify({ type: 'repair', data: job }));
  };
  return {
    progress: send,
    end: async (result) => {
      fake.repairs[result.share.toLowerCase()] = result;
      fake.repairing = null;
      await send(result);
    },
  };
}

/** Serve the SMB, share and user APIs from fake state; returns the state. */
export async function fakeSmbApi(page: Page, options: FakeSmbOptions = {}): Promise<FakeSmbApi> {
  const users = options.users ?? [fakeUser(1, 'admin', 'unavailable', { is_admin: true })];
  const fake: FakeSmbApi = {
    users,
    shares: options.shares ?? [],
    status: { ...OK_STATUS, ...options.status },
    paths: options.paths ?? FAKE_PATHS,
    repairs: {},
    repairAnswer: null,
    repairing: null,
    repairStopAnswer: null,
    changeWarnings: [],
    userWarnings: [],
    // The signed-in user of the specs is the administrator, who cannot use SMB.
    mySmb: options.mySmb ?? { account: smbStatus('unavailable', 'admin'), totp_required: false, shares: [] },
    nfsExports: options.nfsExports ?? [],
    nfsStatus: { server_missing: false, problems: [], ...options.nfsStatus },
    nfsUnreadable: options.nfsUnreadable ?? [],
    nfsWarnings: [],
    myPassword: 'e2e-Current-Password-1',
    myTotpCode: '123456',
    writes: [],
    reads: [],
    intercept: null,
  };

  let repairStarts = 0;
  const findShare = (name: string) => fake.shares.find((s) => s.name.toLowerCase() === name.toLowerCase());
  const findUser = (id: number) => fake.users.find((u) => u.id === id);

  function withAvailability(share: FakeShare): FakeShare {
    share.available = share.served && (share.guest_ok || share.access.some((a) => a.effective));
    return share;
  }

  /** As service._check_access: the entries of a create or PATCH, checked against the users now. */
  function accessProblem(entries: AccessEntry[]): FakeResponse | null {
    for (const entry of entries) {
      const user = findUser(entry.user_id);
      if (!user) return detail(400, `User ${entry.user_id} does not exist`);
      if (user.username !== entry.username) {
        return detail(409, `User ${entry.user_id} is now '${user.username}'; reload the page`);
      }
      if (user.is_admin) return detail(400, 'Administrators cannot use SMB');
      if (user.smb.state === 'unavailable') return detail(400, user.smb.reason ?? 'This user cannot use SMB');
    }
    return null;
  }

  function answer(method: string, path: string, search: string, body: unknown): FakeResponse | null {
    const data = (body ?? {}) as Record<string, unknown>;

    // ---- Shares ----
    if (path === '/shares/paths' && method === 'GET') return ok(fake.paths);
    if (path === '/shares/nfs' && method === 'GET') return ok(fake.nfsExports);
    if (path === '/shares/nfs-status' && method === 'GET') {
      const unreadable = fake.nfsUnreadable.map((u) => nfsFileProblem(u.file, u.line, u.message));
      return ok({ ...fake.nfsStatus, problems: [...unreadable, ...fake.nfsStatus.problems] });
    }
    if (path === '/shares/nfs' && method === 'POST') {
      // As shares/nfs.py: the client first, then the folder, then the export files: no change while
      // one has a line that exportfs cannot read, and one export per folder in the file of UNOS.
      const exportPath = String(data.path);
      const client = String(data.client);
      const problem = nfsClientProblem(client);
      if (problem) return detail(400, problem);
      if (exportPath.slice(POOLS.length + 1).split('/').length < 2) return detail(400, NFS_POOL_ROOT);
      const [unreadable] = fake.nfsUnreadable;
      if (unreadable) return detail(409, nfsFileUnreadable(unreadable.file, unreadable.line));
      if (fake.nfsExports.some((e) => e.managed && e.path === exportPath)) {
        return detail(409, nfsExportedAlready(exportPath));
      }
      // The file of UNOS: two lines of comment, then one line per export.
      const line = 3 + fake.nfsExports.filter((e) => e.file === NFS_UNOS_FILE).length;
      const created = fakeNfsExport(exportPath, client, data.access === 'write' ? 'write' : 'read', line);
      fake.nfsExports.push(created);
      return ok({ ...created, warnings: [...fake.nfsWarnings] });
    }
    if (path === '/shares/nfs' && method === 'DELETE') {
      const target = new URLSearchParams(search).get('path');
      if (target === null) return missing('query', 'path');
      const mine = fake.nfsExports.find((e) => e.managed && e.path === target);
      if (!mine) {
        const other = fake.nfsExports.find((e) => e.path === target);
        return other
          ? detail(409, nfsForeignExport(target, other.file, other.line))
          : detail(404, nfsNotExported(target));
      }
      const [unreadable] = fake.nfsUnreadable;
      if (unreadable) return detail(409, nfsFileUnreadable(unreadable.file, unreadable.line));
      fake.nfsExports = fake.nfsExports.filter((e) => e !== mine);
      return ok({ status: 'deleted', warnings: [...fake.nfsWarnings] });
    }
    if (path === '/shares/smb-status' && method === 'GET') return ok(fake.status);
    if (path === '/shares/smb-status/import-report' && method === 'DELETE') {
      fake.status.import_report = [];
      return ok({ status: 'ok' });
    }
    if (path === '/shares/smb' && method === 'GET') return ok(fake.shares);
    if (path === '/shares/smb' && method === 'POST') {
      const name = String(data.name);
      if (findShare(name)) return detail(409, `Share ${name} already exists`);
      const entries = (data.access as AccessEntry[] | undefined) ?? [];
      const refused = accessProblem(entries);
      if (refused) return refused;
      const guest = data.guest_ok === true;
      if (guest && data.encryption_required === true) return detail(400, GUEST_ENCRYPTION);
      const share = withAvailability(
        fakeShare(name, {
          path: String(data.path),
          comment: typeof data.comment === 'string' ? data.comment : '',
          browseable: data.browseable !== false,
          guest_ok: guest,
          access: accessOf(fake.users, entries),
          encryption_required: data.encryption_required === true,
          time_machine: data.time_machine === true,
          previous_versions: data.previous_versions === true,
        }),
      );
      fake.shares.push(share);
      return ok({ ...share, warnings: [...fake.changeWarnings, ...(guest ? [GUEST_WARNING] : [])] });
    }
    const repair = path.match(/^\/shares\/smb\/([^/]+)\/repair-permissions$/);
    if (repair) {
      const name = decodeURIComponent(repair[1]);
      if (!findShare(name)) return detail(404, 'Share not found');
      if (method === 'GET') {
        const last = fake.repairs[name.toLowerCase()];
        return last ? ok(last) : detail(404, 'No repair has run for this share since UNOS started');
      }
      if (method === 'POST') {
        // Each run starts at a time of its own, as on the server.
        repairStarts += 1;
        const started_at = `2026-11-06T11:00:${String(repairStarts).padStart(2, '0')}`;
        const answer = fake.repairAnswer ?? { status: 202, json: repairStarted(findShare(name)!.name, { started_at }) };
        if (answer.status === 202) fake.repairing = (answer.json as FakeRepairResult).share;
        return answer;
      }
      if (method === 'DELETE') {
        if (fake.repairStopAnswer) return fake.repairStopAnswer;
        const running = fake.repairing?.toLowerCase() === name.toLowerCase();
        return running ? { status: 202, json: repairStarted(fake.repairing!) } : detail(409, REPAIR_NOT_RUNNING);
      }
    }
    const one = path.match(/^\/shares\/smb\/([^/]+)$/);
    if (one) {
      const share = findShare(decodeURIComponent(one[1]));
      if (!share) return detail(404, 'Share not found');
      if (method === 'GET') return ok(share);
      if (method === 'DELETE') {
        fake.shares = fake.shares.filter((s) => s !== share);
        return ok({ status: 'deleted', warnings: [...fake.changeWarnings] });
      }
      if (method === 'PATCH') {
        if (data.expected_version !== undefined && data.expected_version !== share.version) {
          return detail(409, VERSION_CONFLICT);
        }
        const entries = 'access' in data ? (data.access as AccessEntry[]) : null;
        const refused = entries ? accessProblem(entries) : null;
        if (refused) return refused;
        // As the server: a share that is not served is saved only when the
        // result grants nothing; a PATCH that only dismisses the import notes
        // grants nothing and is not checked. A new folder is taken as one that
        // can be served.
        const sent = Object.keys(data).filter((key) => key !== 'expected_version');
        const dismissOnly = sent.length === 1 && sent[0] === 'import_notes';
        const served = share.served || 'path' in data;
        const granted = entries ? entries.length > 0 : share.access.length > 0;
        const guest = 'guest_ok' in data ? data.guest_ok === true : share.guest_ok;
        if (!dismissOnly && !served && (granted || guest)) {
          return detail(400, share.path_problem ?? 'The share is not served');
        }
        const encryption = 'encryption_required' in data ? data.encryption_required === true : share.encryption_required;
        if (guest && encryption) return detail(400, GUEST_ENCRYPTION);
        let changed = false;
        for (const key of [
          'path',
          'comment',
          'browseable',
          'guest_ok',
          'encryption_required',
          'time_machine',
          'previous_versions',
        ] as const) {
          if (key in data && data[key] !== share[key]) {
            (share as unknown as Record<string, unknown>)[key] = data[key];
            changed = true;
          }
        }
        if (served && !share.served) {
          share.served = true;
          share.path_problem = null;
          share.access = accessOf(fake.users, share.access, true);
        }
        // As the server: an access list that is sent unchanged writes nothing.
        if (entries && !sameEntries(entries, share.access)) {
          share.access = accessOf(fake.users, entries, share.served);
          changed = true;
        }
        if ('import_notes' in data && share.import_notes.length > 0) {
          share.import_notes = [];
          changed = true;
        }
        if (changed) share.version += 1;
        withAvailability(share);
        return ok({ ...share, warnings: [...fake.changeWarnings] });
      }
    }

    // ---- Users (administrators) ----
    if (path === '/auth/users' && method === 'GET') return ok(fake.users);
    if (path === '/auth/users' && method === 'POST') {
      const id = Math.max(0, ...fake.users.map((u) => u.id)) + 1;
      const user = fakeUser(id, String(data.username), data.smb_enabled === true ? 'active' : 'off');
      fake.users.push(user);
      return ok({ ...user, warnings: [] });
    }
    const userPath = path.match(/^\/auth\/users\/(\d+)(\/smb\/(enable|disable))?$/);
    if (userPath) {
      // As FastAPI: a missing or extra parameter is refused (422) before the user is looked up.
      const deleting = method === 'DELETE' && !userPath[2];
      const queryName = new URLSearchParams(search).get('username');
      if (deleting && queryName === null) return missing('query', 'username');
      if (method === 'POST' && userPath[3]) {
        if (body === null) return missing('body');
        if (typeof data.username !== 'string') return missing('body', 'username');
        const allowed = userPath[3] === 'enable' ? ['username', 'password'] : ['username'];
        if (Object.keys(data).some((key) => !allowed.includes(key))) {
          return { status: 422, json: { detail: [{ type: 'extra_forbidden', msg: 'Extra inputs are not permitted' }] } };
        }
      }
      const user = findUser(Number(userPath[1]));
      if (!user) return detail(404, 'User not found');
      const stale = () => detail(409, `User ${user.id} is now '${user.username}'; reload the page`);
      if (deleting) {
        // The name the request was prepared for: the id may name someone else now.
        if (queryName !== user.username) return stale();
        fake.users = fake.users.filter((u) => u !== user);
        return ok({ status: 'ok', message: 'User deleted', warnings: [...fake.userWarnings] });
      }
      if (method === 'PUT' && !userPath[2]) {
        if (data.new_username !== undefined && data.username === undefined) return missing('body', 'username');
        if (data.username !== undefined && data.username !== user.username) return stale();
        const newName = typeof data.new_username === 'string' ? data.new_username : user.username;
        if (newName !== user.username) {
          // A rename (ADR-0368), with the server's refusals of src/auth/router.py and accounts.py.
          if (!USERNAME_RULE.test(newName)) return detail(400, `Invalid username. ${NAME_RULE_TEXT}`);
          const taken = fake.users.find((u) => u !== user && u.username.toLowerCase() === newName.toLowerCase());
          if (taken) {
            return detail(
              400,
              taken.username === newName
                ? 'Username already exists'
                : `Username already exists as '${taken.username}': names that differ only in letter case are one account for Samba`,
            );
          }
          if (data.is_admin !== undefined && data.is_admin !== user.is_admin) {
            return detail(400, 'Change the name and the role one at a time');
          }
          if (user.smb.state === 'active' && typeof data.password !== 'string') {
            return detail(400, renameNeedsPassword(user.username));
          }
          const old = user.username;
          user.username = newName;
          if (user.smb.allowed) user.smb = { ...user.smb, linux_name: newName };
          for (const share of fake.shares) {
            share.access = share.access.map((a) => (a.user_id === user.id && a.username === old ? { ...a, username: newName } : a));
          }
          return ok({ ...user, warnings: [...fake.userWarnings] });
        }
        if (data.is_admin === true && !user.is_admin) {
          user.is_admin = true;
          user.smb = smbStatus('unavailable', user.username);
          for (const share of fake.shares) {
            share.access = share.access.filter((a) => a.user_id !== user.id);
            withAvailability(share);
          }
        } else if (data.is_admin === false && user.is_admin) {
          user.is_admin = false;
          user.smb = smbStatus('off', user.username);
        }
        return ok({ ...user, warnings: [...fake.userWarnings] });
      }
      if (method === 'POST' && userPath[3] === 'enable') {
        if (data.username !== user.username) return stale();
        if (user.smb.state === 'unavailable') return detail(409, user.smb.reason ?? ADMIN_REASON);
        const state: SmbState =
          typeof data.password === 'string' || user.smb.state === 'active' ? 'active' : 'password_needed';
        user.smb = smbStatus(state, user.username);
        return ok({ ...user, warnings: [...fake.userWarnings] });
      }
      if (method === 'POST' && userPath[3] === 'disable') {
        if (data.username !== user.username) return stale();
        if (user.smb.state !== 'unavailable') user.smb = smbStatus('off', user.username);
        return ok({ ...user, warnings: [...fake.userWarnings] });
      }
    }

    // ---- The signed-in user's own SMB access ----
    if (path === '/auth/me/smb' && method === 'GET') return ok(fake.mySmb);
    if (path === '/auth/me/smb/enable' && method === 'POST') {
      const account = fake.mySmb.account;
      if (data.current_password !== fake.myPassword) return detail(400, 'Current password is incorrect');
      if (fake.mySmb.totp_required) {
        if (!data.totp_code) return detail(400, 'Two-factor code required');
        if (data.totp_code !== fake.myTotpCode) return detail(400, 'Invalid two-factor code');
      }
      if (account.state === 'unavailable') return detail(409, account.reason ?? ADMIN_REASON);
      if (!account.allowed) return detail(403, 'An administrator must allow SMB access for your account first');
      fake.mySmb.account = smbStatus('active', account.linux_name ?? 'admin');
      fake.mySmb.shares = fake.mySmb.shares.map((s) => ({ ...s, usable: true }));
      return ok({ ...fake.mySmb, warnings: [] });
    }
    if (path === '/auth/me/smb/disable' && method === 'POST') {
      const account = fake.mySmb.account;
      if (account.state === 'active') {
        fake.mySmb.account = smbStatus('password_needed', account.linux_name ?? 'admin');
        fake.mySmb.shares = fake.mySmb.shares.map((s) => ({ ...s, usable: s.guest_ok }));
      }
      return ok({ ...fake.mySmb, warnings: [] });
    }
    return null;
  }

  await page.route(
    (url) =>
      url.pathname.startsWith('/api/v1/shares/') ||
      url.pathname === '/api/v1/auth/users' ||
      url.pathname.startsWith('/api/v1/auth/users/') ||
      url.pathname === '/api/v1/auth/me/smb' ||
      url.pathname.startsWith('/api/v1/auth/me/smb/'),
    async (route) => {
      const request = route.request();
      const method = request.method();
      const url = new URL(request.url());
      const path = url.pathname.slice('/api/v1'.length);
      const search = url.search;
      const body = jsonBody(request.postData());
      if (method === 'GET') fake.reads.push(path);
      else fake.writes.push({ method, path: path + search, body });

      const response = fake.intercept?.({ method, path, search, body }) ?? answer(method, path, search, body);
      if (!response) return route.abort();
      return route.fulfill({ status: response.status, json: response.json });
    },
  );
  return fake;
}
