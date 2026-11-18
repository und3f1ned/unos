import { expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';

// Route fakes of the App Store for what the stand-in lacks (ADR-0189): its catalog is the
// stand-in's own, answered on a machine without a device tree, so it names no hardware; Docker's
// installed apps and the install are answered here as supervisor/src/apps/router.py answers them
// (ADR-0381, ADR-0382). The texts the specs copy from the server stay word for word.

/** RK3588_VIDEO of supervisor/src/apps/catalog.py: Jellyfin's and Immich's server's. */
export const RK3588_VIDEO = {
  id: 'rk3588-video',
  soc: 'rockchip,rk3588',
  devices: ['/dev/dri', '/dev/dma_heap', '/dev/mpp_service', '/dev/rga', '/dev/mali0'],
  requires: ['/dev/mpp_service'],
  unmasked_paths: ['/sys/firmware'],
  apparmor_unconfined: true,
  image: null as string | null,
};

/** The stable name of the NPU's render node, a link inside the folder /dev/dri. */
export const NPU_LINK = '/dev/dri/by-path/platform-fdab0000.npu-render';

/** Immich's machine learning for Rockchip's NPU, as the catalog pins it. */
export const RKNN_IMAGE =
  'ghcr.io/immich-app/immich-machine-learning:v3.4.1-rknn@sha256:b7baeb12cab0093803f09f0299b7c863bed46f36349c532096f40bfbf4f10c84';

/** RK3588_NPU of supervisor/src/apps/catalog.py as Immich's machine learning has it: with its image. */
export const RK3588_NPU = {
  id: 'rk3588-npu',
  soc: 'rockchip,rk3588',
  devices: ['/dev/dri'],
  requires: [NPU_LINK],
  unmasked_paths: ['/sys/firmware'],
  apparmor_unconfined: true,
  image: RKNN_IMAGE as string | null,
};

type Profile = typeof RK3588_VIDEO;

/** The profiles of an app's containers that apply on a host: the main container's under `main`, a companion's under its name. */
export type AppHardware = Record<string, Profile[]>;

/** The hardware of this NAS's RK3588 that the catalog gives Jellyfin and Immich. */
export const ON_AN_RK3588: Record<string, AppHardware> = {
  jellyfin: { main: [RK3588_VIDEO] },
  immich: { main: [RK3588_VIDEO], 'machine-learning': [RK3588_NPU] },
};

interface FakeSpec {
  image: string;
  hardware: unknown[];
}

/**
 * The stand-in's catalog as the catalog API answers it on an RK3588 (for_host of
 * supervisor/src/apps/hardware.py): the containers of the apps of `hardware` with those profiles,
 * each in the image of a profile that names one.
 */
export async function onAnRk3588(page: Page, hardware: Record<string, AppHardware> = ON_AN_RK3588) {
  const forHost = <T extends FakeSpec>(spec: T, profiles: Profile[] | undefined): T => {
    if (!profiles) return spec;
    const image = profiles.find((profile) => profile.image)?.image ?? spec.image;
    return { ...spec, hardware: profiles, image };
  };
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/catalog',
    async (route: Route) => {
      const response = await route.fetch();
      const catalog = (await response.json()) as (FakeSpec & { id: string; companions: (FakeSpec & { name: string })[] })[];
      const json = catalog.map((app) => {
        const own = hardware[app.id];
        if (!own) return app;
        const companions = app.companions.map((companion) => forHost(companion, own[companion.name]));
        return { ...forHost(app, own.main), companions };
      });
      await route.fulfill({ response, json });
    },
  );
}

export interface FakeVolume {
  host: string;
  container: string;
  readonly: boolean;
  companion: string | null;
}

/** A container's limits and its use (ContainerResources of supervisor/src/apps/schemas.py, ADR-0390). */
export interface FakeResources {
  memory_limit: number | null;
  cpu_limit: number | null;
  memory_used: number | null;
  cpu_used: number | null;
  sampled_at: string | null;
}

/** A container without limits whose use was not read yet. */
export function noResources(): FakeResources {
  return { memory_limit: null, cpu_limit: null, memory_used: null, cpu_used: null, sampled_at: null };
}

export interface FakeCompanion {
  name: string;
  container: string;
  container_id: string;
  status: string;
  health: string | null;
  image: string;
  devices: string[];
  resources: FakeResources;
}

/** An installed app as GET /api/v1/apps/installed lists it (InstalledApp of supervisor/src/apps/schemas.py). */
export interface FakeInstalledApp {
  id: string;
  template_id: string;
  name: string;
  container_id: string;
  status: string;
  web_port: number | null;
  created_at: string;
  pool: string | null;
  volumes: FakeVolume[];
  data_outside_pools: string[];
  runs_in_unos_smb: boolean;
  devices: string[];
  health: string | null;
  companions: FakeCompanion[];
  missing: string[];
  resources: FakeResources;
  version: string | null;
  update_to: string | null;
  previous: FakeEarlierVersion | null;
}

/** The earlier version that an app's update kept (AppEarlierVersion, ADR-0408). */
export interface FakeEarlierVersion {
  version: string | null;
  containers: string[];
  images: string[];
  copies: string[];
}

/** An app of one container, `id`, running on the pool at `poolPath` with its data folder /config. */
export function oneContainerApp(
  id: string,
  poolPath: string,
  options: { port?: number | null; devices?: string[] } = {},
): FakeInstalledApp {
  const folder = '/config';
  return {
    id: `e2e-${id}-id`,
    template_id: id,
    name: id,
    container_id: `e2e-${id}-id`,
    status: 'running',
    web_port: options.port ?? null,
    created_at: '2026-11-15T10:00:00Z',
    pool: poolPath.slice(poolPath.lastIndexOf('/') + 1),
    volumes: [{ host: `${poolPath}/apps/${id}${folder}`, container: folder, readonly: false, companion: null }],
    data_outside_pools: [],
    runs_in_unos_smb: false,
    devices: options.devices ?? [],
    health: null,
    companions: [],
    missing: [],
    resources: noResources(),
    version: null,
    update_to: null,
    previous: null,
  };
}

/** Immich's images as its containers were created from them: by digest (ADR-0381). */
const IMMICH_IMAGES: Record<string, string> = {
  database: 'ghcr.io/immich-app/postgres@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23',
  redis: 'docker.io/valkey/valkey@sha256:418652cfb58ef879d4978c33553735d7147016032d5aefaa14c828e611eb9dfd',
  'machine-learning': 'ghcr.io/immich-app/immich-machine-learning@sha256:b7baeb12cab0093803f09f0299b7c863bed46f36349c532096f40bfbf4f10c84',
};

/** What a container of an installed Immich is: its state and the nodes it got. */
export interface FakeContainerState {
  status?: string;
  health?: string | null;
  devices?: string[];
  resources?: FakeResources;
}

/**
 * Immich installed on the pool at `poolPath`, as the installed list names it: its server `immich`
 * and its companions database, redis and machine-learning, each with its bind mounts in the
 * order Docker lists them. A container named in `missing` is one Docker lacks; without the
 * server, the app is named by its first companion's id (InstalledApp).
 */
export function installedImmich(
  poolPath: string,
  options: { main?: FakeContainerState; companions?: Record<string, FakeContainerState>; missing?: string[] } = {},
): FakeInstalledApp {
  const app = `${poolPath}/apps/immich`;
  const password = { host: `${app}/postgres/password`, container: '/run/secrets/db-password', readonly: true };
  const mounts: Record<string, Omit<FakeVolume, 'companion'>[]> = {
    main: [
      { host: `${app}/library`, container: '/data', readonly: false },
      { host: '/etc/localtime', container: '/etc/localtime', readonly: true },
      password,
    ],
    database: [password, { host: `${app}/postgres/data`, container: '/var/lib/postgresql/data', readonly: false }],
    redis: [{ host: `${app}/redis`, container: '/data', readonly: false }],
    'machine-learning': [{ host: `${app}/model-cache`, container: '/cache', readonly: false }],
  };
  const missing = options.missing ?? [];
  const companions: FakeCompanion[] = ['database', 'redis', 'machine-learning']
    .filter((name) => !missing.includes(`immich-${name}`))
    .map((name) => {
      const state = options.companions?.[name] ?? {};
      return {
        name,
        container: `immich-${name}`,
        container_id: `e2e-immich-${name}-id`,
        status: state.status ?? 'running',
        health: state.health === undefined ? (name === 'redis' ? null : 'healthy') : state.health,
        image: IMMICH_IMAGES[name],
        devices: state.devices ?? [],
        resources: state.resources ?? noResources(),
      };
    });
  const hasMain = !missing.includes('immich');
  const main = options.main ?? {};
  const volumes: FakeVolume[] = [
    ...(hasMain ? mounts.main.map((volume) => ({ ...volume, companion: null })) : []),
    ...companions.flatMap((companion) => mounts[companion.name].map((volume) => ({ ...volume, companion: companion.name }))),
  ];
  const id = hasMain ? 'e2e-immich-id' : companions[0].container_id;
  return {
    id,
    template_id: 'immich',
    name: 'immich',
    container_id: id,
    status: hasMain ? (main.status ?? 'running') : 'missing',
    web_port: hasMain ? 2283 : null,
    created_at: '2026-11-16T10:00:00Z',
    pool: poolPath.slice(poolPath.lastIndexOf('/') + 1),
    volumes,
    data_outside_pools: [],
    runs_in_unos_smb: false,
    devices: hasMain ? (main.devices ?? []) : [],
    health: hasMain ? (main.health === undefined ? 'healthy' : main.health) : null,
    companions,
    missing,
    resources: hasMain ? (main.resources ?? noResources()) : noResources(),
    version: 'v3.4.1',
    update_to: null,
    previous: null,
  };
}

/**
 * The answer of POST /api/v1/apps/install that made `app` (AppInstallResponse): the server's
 * health right after its start, before its database migrations ran.
 */
export function installAnswer(app: FakeInstalledApp) {
  const { id, name, container_id, status, web_port, pool, volumes, devices, companions } = app;
  const several = companions.length > 0;
  return {
    id,
    name,
    container_id,
    status,
    web_url: web_port ? `http://localhost:${web_port}` : null,
    pool,
    volumes,
    devices,
    health: several ? 'starting' : app.health,
    companions,
  };
}

/** The card of the app `name` in the catalog's grid (Featured Apps lists some of them too). */
export function catalogCard(page: Page, name: string) {
  return page.locator('.card[role="button"]', { has: page.locator('h3', { hasText: new RegExp(`^${name}$`) }) });
}

/** The card of the installed app `name` on the Installed tab. */
export function installedCard(page: Page, name: string) {
  return page.locator('main .card', { has: page.locator('h3', { hasText: new RegExp(`^${name}$`) }) });
}

/** An image's pull in an install job (ImagePull of supervisor/src/apps/schemas.py). */
export interface FakeImagePull {
  image: string;
  state: 'waiting' | 'present' | 'pulling' | 'pulled' | 'failed';
  layers: number;
  layers_done: number;
  bytes_total: number;
  bytes_done: number;
}

/** A container of an install job (ContainerStart). */
export interface FakeContainerStart {
  name: string;
  companion: string | null;
  state: 'waiting' | 'created' | 'starting' | 'running' | 'failed' | 'removed';
}

/** An install job as POST /api/v1/apps/install answers it and /ws/v1/events sends it (AppInstallJob, ADR-0384). */
export interface FakeInstallJob {
  id: string;
  template_id: string;
  name: string;
  kind: 'install' | 'update' | 'rollback';
  state: 'running' | 'succeeded' | 'failed' | 'cancelled';
  step: 'checking' | 'pulling' | 'stopping' | 'copying' | 'creating' | 'starting' | 'cleaning' | 'restoring';
  started_at: string;
  finished_at: string | null;
  images: FakeImagePull[];
  containers: FakeContainerStart[];
  result: ReturnType<typeof installAnswer> | null;
  error: string | null;
  error_status: number | null;
}

let jobCount = 0;

/**
 * A new install job of the app `templateId` named `name`, as the server starts it: running, at its
 * checks, its containers (companions first, then the main container, named after the app) waiting.
 */
export function newInstallJob(templateId: string, name: string, companions: string[] = []): FakeInstallJob {
  jobCount += 1;
  return {
    id: `e2e-job-${jobCount}`,
    template_id: templateId,
    name,
    kind: 'install',
    state: 'running',
    step: 'checking',
    started_at: '2026-11-17T10:00:00Z',
    finished_at: null,
    images: [],
    containers: [
      ...companions.map((companion) => ({ name: `${templateId}-${companion}`, companion, state: 'waiting' as const })),
      { name: templateId, companion: null, state: 'waiting' as const },
    ],
    result: null,
    error: null,
    error_status: null,
  };
}

/** `job` once it ended as `end` says: succeeded with the answer of the install that made the app, or failed. */
export function endedJob(
  job: FakeInstallJob,
  end: FakeInstalledApp | { status: number; detail: string },
): FakeInstallJob {
  if ('detail' in end) {
    return { ...job, state: 'failed', finished_at: '2026-11-17T10:00:02Z', error: end.detail, error_status: end.status };
  }
  return {
    ...job,
    state: 'succeeded',
    step: 'starting',
    finished_at: '2026-11-17T10:05:00Z',
    containers: job.containers.map((container) => ({ ...container, state: 'running' })),
    result: installAnswer(end),
  };
}

/** The fake /ws/v1/events of fakeInstallEvents. */
export interface FakeInstallEvents {
  /** The jobs that the server keeps: sent after each sign-in when there is one, as the server does. */
  jobs: FakeInstallJob[];
  /** Keep `job` in place of the app's last one and send the jobs, as the server does on each change. */
  update(job: FakeInstallJob): Promise<void>;
  /** Forget the job `id` and send the jobs (DELETE /api/v1/apps/install/jobs/{id}). */
  forget(id: string): Promise<void>;
  /** How many times the page signed in to the socket. */
  signIns(): number;
  /**
   * Close the socket as a supervisor that restarts does (1012), which forgets its jobs, and wait
   * until the page has signed in again.
   */
  restart(): Promise<void>;
}

/**
 * Stand in for /ws/v1/events with the install jobs of apps (supervisor/src/websocket/events.py,
 * ADR-0384): the fake answers the page's auth message with auth_ok and, when it keeps a job, an
 * app_installs message with the jobs, as the server does after a reload; `update` sends the jobs
 * again on the latest socket, as the server does whenever a job changes.
 */
export async function fakeInstallEvents(page: Page): Promise<FakeInstallEvents> {
  let socket: WebSocketRoute | null = null;
  let signIns = 0;
  const jobs: FakeInstallJob[] = [];
  const send = (ws: WebSocketRoute) => ws.send(JSON.stringify({ type: 'app_installs', data: jobs }));
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    ws.onMessage((message) => {
      if (JSON.parse(String(message)).type !== 'auth') return;
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      if (jobs.length > 0) send(ws);
      socket = ws;
      signIns += 1;
    });
  });
  const signedIn = () =>
    expect.poll(() => socket !== null, { message: 'the page signed in to the events socket' }).toBe(true);
  return {
    jobs,
    update: async (job) => {
      const index = jobs.findIndex((kept) => kept.template_id === job.template_id);
      if (index === -1) jobs.push(job);
      else jobs[index] = job;
      await signedIn();
      send(socket!);
    },
    forget: async (id) => {
      const index = jobs.findIndex((kept) => kept.id === id);
      if (index !== -1) jobs.splice(index, 1);
      await signedIn();
      send(socket!);
    },
    signIns: () => signIns,
    restart: async () => {
      await signedIn();
      const before = signIns;
      const closing = socket!;
      socket = null;
      jobs.splice(0, jobs.length);
      await closing.close({ code: 1012, reason: 'Service Restart' });
      await expect.poll(() => signIns, { message: 'the page signed in to the events socket again' }).toBe(before + 1);
    },
  };
}

/**
 * Answer POST /api/v1/apps/install as the server does (ADR-0384): 202 with a new job of the
 * template that the request names, the app `name`, running at its checks, which `events` then
 * ends as `end` says: an installed app, which joins `installed`, or a refusal of its checks with
 * the server's status and detail. Returns the job as it started.
 */
export async function answerInstall(
  route: Route,
  events: FakeInstallEvents,
  name: string,
  end: FakeInstalledApp | { status: number; detail: string },
  installed: FakeInstalledApp[] = [],
): Promise<FakeInstallJob> {
  const body = route.request().postDataJSON() as { template_id: string };
  const companions = 'detail' in end ? [] : end.companions.map((companion) => companion.name);
  const job = newInstallJob(body.template_id, name, companions);
  await route.fulfill({ status: 202, json: job });
  if (!('detail' in end)) installed.push(end);
  await events.update(endedJob(job, end));
  return job;
}

/**
 * The stand-in's catalog as the catalog API answers it on a host of another platform than this
 * machine's (apps.catalog-platforms, ADR-0403): each app's `platforms` are the real, recorded ones;
 * the host's platform and which apps are available on it are set here by the server's rule
 * (supervisor/src/apps/platforms.py), for a platform that an image publishes by its exact name (every image of the catalog is recorded; Readarr's publishes none).
 */
export async function onPlatform(page: Page, platform: string) {
  const mark = (app: { platforms: string[]; companions?: { platforms: string[] }[] }) => ({
    ...app,
    host_platform: platform,
    available: [app, ...(app.companions ?? [])].every((spec) => spec.platforms.includes(platform)),
  });
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/catalog',
    async (route: Route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: ((await response.json()) as Parameters<typeof mark>[0][]).map(mark) });
    },
  );
}
