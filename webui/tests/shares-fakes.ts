import type { Page } from '@playwright/test';

// Canned answers of the SMB share, user and account APIs for UI tests that
// must never change a real share, user or SMB account. fakeSmbApi() answers
// every request of the page to /api/v1/shares/*, /api/v1/auth/users* and
// /api/v1/auth/me/smb*: writes are recorded and applied to the fake state (so
// the page shows their outcome), unknown requests are aborted, and nothing
// reaches the supervisor. Sign-in (/auth/login, /auth/me) is not faked.

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
// A change sent with an outdated expected_version (ADR-0135).
export const VERSION_CONFLICT = 'The share was changed meanwhile; reload';

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
  state: 'done' | 'incomplete' | 'failed';
  started_at: string;
  finished_at: string;
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

export interface SharePathInfo {
  path: string;
  label: string;
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
  /** Last repair result per share name (lower case), as GET .../repair-permissions answers. */
  repairs: Record<string, FakeRepairResult>;
  /** Answer of POST .../repair-permissions (default: a "done" run). */
  repairAnswer: FakeResponse | null;
  /** Warnings of the answers to share changes (create, PATCH, delete), e.g. a failed reload. */
  changeWarnings: string[];
  /**
   * Warnings of the answers to user changes (PUT, SMB enable and disable,
   * delete), e.g. "An SMB connection of alice could not be ended: ...".
   */
  userWarnings: string[];
  mySmb: FakeMySmb;
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

export const FAKE_PATHS: SharePathInfo[] = [
  { path: `${POOLS}/tank`, label: 'tank (Pool)', type: 'pool', managed: true },
  { path: `${POOLS}/tank/media`, label: 'tank/media (Volume)', type: 'volume', managed: true },
];

export interface FakeSmbOptions {
  users?: FakeUser[];
  shares?: FakeShare[];
  status?: Partial<FakeServiceStatus>;
  paths?: SharePathInfo[];
  mySmb?: FakeMySmb;
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
    changeWarnings: [],
    userWarnings: [],
    // The signed-in user of the specs is the administrator, who cannot use SMB.
    mySmb: options.mySmb ?? { account: smbStatus('unavailable', 'admin'), totp_required: false, shares: [] },
    myPassword: 'e2e-Current-Password-1',
    myTotpCode: '123456',
    writes: [],
    reads: [],
    intercept: null,
  };

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
    if (path === '/shares/nfs' && method === 'GET') return ok([]);
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
      const share = withAvailability(
        fakeShare(name, {
          path: String(data.path),
          comment: typeof data.comment === 'string' ? data.comment : '',
          browseable: data.browseable !== false,
          guest_ok: guest,
          access: accessOf(fake.users, entries),
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
        const result = fake.repairAnswer ?? ok(repairResult(name));
        if (result.status === 200) fake.repairs[name.toLowerCase()] = result.json as FakeRepairResult;
        return result;
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
        let changed = false;
        for (const key of ['path', 'comment', 'browseable', 'guest_ok'] as const) {
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
        if (data.username !== undefined && data.username !== user.username) return stale();
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
