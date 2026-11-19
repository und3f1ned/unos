import type { Page } from '@playwright/test';

// Canned answers of the iSCSI API (supervisor/src/shares/iscsi.py, ADR-0417) for the specs of
// Shares > iSCSI: nothing reaches the supervisor, whose routes answer 503 on the stand-in (it runs
// its host commands locally). fakeIscsiApi() answers every request of the page to
// /api/v1/shares/iscsi*: writes are recorded and applied to the fake state, unknown requests are
// aborted. Register it after fakeSmbApi(), whose route of /api/v1/shares/* it then takes over for
// these paths. Its texts are the server's, word for word (ADR-0189), which
// supervisor/tests/test_iscsi.py checks.

export const NAME_RULE =
  "A target name has 1 to 32 lowercase letters, digits and '-', starting with a letter or digit";
export const SECRET_RULE =
  'A CHAP secret has 12 to 16 characters: letters, digits and !#%+,-./:=?@^_~, and does not start with NULL';
export const CONFIRM_NAME = "Type the target's name to delete it and the data of its LUN";
export const NO_SECRET = 'No CHAP secret is stored for this target: set a new secret';

/** The secrets the fake hands out, in turn, when the page lets the server generate them. */
export const GENERATED = ['Gener4tedSecret1', 'Gener4tedSecret2', 'Gener4tedSecret3', 'Gener4tedSecret4'];
export const PORTALS = ['192.168.1.10:3260', '[fd00::10]:3260'];

export interface FakeIscsiTarget {
  name: string;
  iqn: string;
  pool: string;
  volume: string;
  size_gib: number;
  path: string;
  initiators: string[];
  chap_user: string;
  mutual: boolean;
  served: boolean;
  problem: string | null;
  sessions: string[];
  created: string | null;
}

export interface FakeIscsiApi {
  kernel_support: boolean;
  portals: string[];
  targets: FakeIscsiTarget[];
  writes: { method: string; path: string; body: unknown }[];
}

export function fakeTarget(name: string, fields: Partial<FakeIscsiTarget> = {}): FakeIscsiTarget {
  return {
    name,
    iqn: `iqn.2003-01.org.linux-iscsi.unos:${name}.0123abcd`,
    pool: 'tank',
    volume: 'media',
    size_gib: 10,
    path: `/mnt/pools/tank/media/.unos-iscsi/${name}.lun`,
    initiators: ['iqn.1993-08.org.debian:01:5f3c0a1b2c3d'],
    chap_user: name,
    mutual: false,
    served: true,
    problem: null,
    sessions: [],
    created: '2026-11-19T10:00:00+00:00',
    ...fields,
  };
}

const NAME = /^[a-z0-9][a-z0-9-]{0,31}$/;
const SECRET = /^(?!NULL)[A-Za-z0-9!#%+,\-./:=?@^_~]{12,16}$/;
const INITIATOR = /^(iqn\.[0-9]{4}-(0[1-9]|1[0-2])\.[a-z0-9.-]+(:[a-z0-9._:-]+)?|eui\.[0-9a-f]{16}|naa\.([0-9a-f]{16}|[0-9a-f]{32}))$/;

type Answer = { status: number; json: unknown };

export async function fakeIscsiApi(
  page: Page,
  options: Partial<Pick<FakeIscsiApi, 'kernel_support' | 'portals' | 'targets'>> = {},
): Promise<FakeIscsiApi> {
  const fake: FakeIscsiApi = {
    kernel_support: options.kernel_support ?? true,
    portals: options.portals ?? PORTALS,
    targets: options.targets ?? [],
    writes: [],
  };
  let generated = 0;
  const nextSecret = () => GENERATED[generated++ % GENERATED.length];
  const refused = (detail: string): Answer => ({ status: 400, json: { detail } });

  function initiatorsProblem(names: unknown): string | null {
    if (!Array.isArray(names) || names.length === 0) return 'Allow at least one initiator';
    for (const name of names) {
      if (typeof name !== 'string' || !INITIATOR.test(name.trim().toLowerCase())) {
        return `${name} is not an iSCSI name: use iqn.YYYY-MM.<reversed domain>[:<name>], eui.<16 hex digits> or naa.<16 or 32 hex digits>`;
      }
    }
    return null;
  }

  function answer(method: string, path: string, body: Record<string, unknown>): Answer | null {
    if (method === 'GET' && path === '/shares/iscsi') {
      return {
        status: 200,
        json: { kernel_support: fake.kernel_support, portals: fake.portals, portals_problem: null, targets: fake.targets },
      };
    }
    if (method === 'POST' && path === '/shares/iscsi/targets') {
      const name = String(body.name ?? '');
      if (!NAME.test(name)) return refused(NAME_RULE);
      const problem = initiatorsProblem(body.initiators);
      if (problem) return refused(problem);
      if (body.chap_secret !== undefined && !SECRET.test(String(body.chap_secret))) return refused(SECRET_RULE);
      if (fake.targets.some((t) => t.name === name)) {
        return { status: 409, json: { detail: `An iSCSI target named ${name} exists already` } };
      }
      const chap = (body.chap_secret as string | undefined) ?? nextSecret();
      const mutual = body.mutual ? ((body.mutual_secret as string | undefined) ?? nextSecret()) : null;
      const target = fakeTarget(name, {
        pool: String(body.pool),
        volume: String(body.volume),
        size_gib: Number(body.size_gib),
        path: `/mnt/pools/${body.pool}/${body.volume}/.unos-iscsi/${name}.lun`,
        initiators: (body.initiators as string[]).map((i) => i.trim().toLowerCase()),
        chap_user: (body.chap_user as string | undefined) ?? name,
        mutual: Boolean(body.mutual),
      });
      fake.targets = [...fake.targets, target].sort((a, b) => a.name.localeCompare(b.name));
      return { status: 201, json: { target, chap_secret: chap, mutual_secret: mutual } };
    }
    const changed = path.match(/^\/shares\/iscsi\/targets\/([^/]+)$/);
    if (method === 'PATCH' && changed) {
      const target = fake.targets.find((t) => t.name === decodeURIComponent(changed[1]));
      if (!target) return { status: 404, json: { detail: `No iSCSI target is named ${changed[1]}` } };
      if (body.initiators !== undefined) {
        const problem = initiatorsProblem(body.initiators);
        if (problem) return refused(problem);
        target.initiators = (body.initiators as string[]).map((i) => i.trim().toLowerCase());
      }
      let chap: string | null = null;
      let mutual: string | null = null;
      if (body.new_secret) {
        if (body.chap_secret !== undefined && !SECRET.test(String(body.chap_secret))) return refused(SECRET_RULE);
        chap = (body.chap_secret as string | undefined) ?? nextSecret();
        target.served = true;
        target.problem = null;
      }
      if (body.mutual === true && (!target.mutual || body.new_mutual_secret || body.mutual_secret)) {
        mutual = (body.mutual_secret as string | undefined) ?? nextSecret();
      }
      if (typeof body.mutual === 'boolean') target.mutual = body.mutual;
      return { status: 200, json: { target, chap_secret: chap, mutual_secret: mutual } };
    }
    const deleted = path.match(/^\/shares\/iscsi\/targets\/([^/]+)\/delete$/);
    if (method === 'POST' && deleted) {
      const name = decodeURIComponent(deleted[1]);
      const target = fake.targets.find((t) => t.name === name);
      if (!target) return { status: 404, json: { detail: `No iSCSI target is named ${name}` } };
      if (body.confirm !== name) return refused(CONFIRM_NAME);
      fake.targets = fake.targets.filter((t) => t !== target);
      return { status: 200, json: { name, sessions: target.sessions } };
    }
    return null;
  }

  await page.route(
    (url) => url.pathname === '/api/v1/shares/iscsi' || url.pathname.startsWith('/api/v1/shares/iscsi/'),
    async (route) => {
      const request = route.request();
      const method = request.method();
      const path = new URL(request.url()).pathname.slice('/api/v1'.length);
      const raw = request.postData();
      const body = (raw ? JSON.parse(raw) : {}) as Record<string, unknown>;
      if (method !== 'GET') fake.writes.push({ method, path, body });
      const response = answer(method, path, body);
      if (!response) return route.abort();
      return route.fulfill({ status: response.status, json: response.json });
    },
  );
  return fake;
}
