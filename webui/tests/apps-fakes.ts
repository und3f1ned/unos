import type { Page, Route } from '@playwright/test';

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

export interface FakeCompanion {
  name: string;
  container: string;
  container_id: string;
  status: string;
  health: string | null;
  image: string;
  devices: string[];
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
