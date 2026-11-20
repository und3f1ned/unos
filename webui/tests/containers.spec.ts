import type { Locator, Page, Route, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { pageSessionKey } from './session';

// The stand-in has no Docker. The page's requests to /api/v1/containers are
// answered from memory, and so is the log socket /ws/v1/logs/{id}: nothing
// reaches Docker. Unknown requests are aborted. The texts are the supervisor's
// (supervisor/src/containers/docker.py and router.py, apps/router.py and
// websocket/events.py) and Docker's that it passes on, word for word.

interface FakeContainer {
  id: string;
  name: string;
  image: string;
  status: string;
  state: string;
  created: string;
  ports: { host_port: number | null; container_port: number; protocol: string }[];
  labels: Record<string, string>;
}

interface FakeImage {
  id: string;
  tags: string[];
  size: number;
  created: string;
}

// The server's status is docker-py's Container.status, Docker's State.Status: the same word as
// the state (list_containers of docker.py), not the "Up 2 hours" of `docker ps`.
function container(id: string, name: string, image: string, state: string): FakeContainer {
  return { id, name, image, status: state, state, created: '2026-11-01T10:00:00Z', ports: [], labels: {} };
}

const WEB = container('0d1f2e3c4b5a', 'e2e-web', 'nginx:1.27', 'running');
const DB = container('1a2b3c4d5e6f', 'e2e-db', 'postgres:16', 'exited');
const CACHE = container('2b3c4d5e6f70', 'e2e-cache', 'redis:7', 'paused');
// A UNOS app whose pool is not mounted: its start is refused (apps/router.py).
const MEDIA = {
  ...container('3c4d5e6f7081', 'e2e-media', 'jellyfin/jellyfin:10.10', 'exited'),
  labels: { 'unos.app': 'jellyfin', 'unos.app.pool': 'e2e_media' },
};
const MEDIA_REFUSED =
  "e2e-media keeps the data for /config in /mnt/pools/e2e_media/apps/jellyfin/config, which is not " +
  "on a mounted storage pool. Mount the pool ('e2e_media' holds the app's data) and start the app again.";

const NGINX: FakeImage = { id: 'sha256:605c77e624dd', tags: ['nginx:1.27'], size: 192_000_000, created: '2026-10-01T08:00:00Z' };
const POSTGRES: FakeImage = { id: 'sha256:7a8b9c0d1e2f', tags: ['postgres:16'], size: 438_000_000, created: '2026-10-02T08:00:00Z' };
// One image with two tags: a removal takes both.
const ALPINE_OLD: FakeImage = { id: 'sha256:aa11bb22cc33', tags: ['alpine:3.19', 'alpine:3'], size: 7_800_000, created: '2026-01-05T08:00:00Z' };
const PULLED: FakeImage = { id: 'sha256:bb22cc33dd44', tags: ['alpine:3.20'], size: 8_100_000, created: '2026-11-02T08:00:00Z' };

/** What the server answers with 409 for an image that containers use (remove_image of docker.py). */
function inUse(image: string, containers: string[]): string {
  const names = [...containers].sort();
  return names.length === 1
    ? `The image ${image} is used by the container ${names[0]}. Remove the container first.`
    : `The image ${image} is used by the containers ${names.join(', ')}. Remove the containers first.`;
}
const IN_USE = inUse('nginx:1.27', [WEB.name]);
// What the server answers with 500 for an image that no registry has.
const NOT_PULLED =
  '404 Client Error for http+docker://localhost/v1.47/images/create?tag=1&fromImage=e2e%2Fmissing: Not Found ' +
  '("pull access denied for e2e/missing, repository does not exist or may require \'docker login\': ' +
  'denied: requested access to the resource is denied")';

interface FakeDockerOptions {
  containers?: FakeContainer[];
  images?: FakeImage[];
  /** A pull is answered only once this settles. */
  pullHeld?: Promise<void>;
  /** A start, stop or restart is answered only once the promise this returns for it settles. */
  actionGate?: (request: string) => Promise<void>;
}

/**
 * Serve the containers API from memory; returns every request of the page to it ("METHOD
 * path?query"), with the fake's list of containers, which a test may change, as `containers`.
 */
async function fakeContainersApi(
  page: Page,
  options: FakeDockerOptions = {},
): Promise<string[] & { containers: FakeContainer[] }> {
  const containers = (options.containers ?? [WEB, DB, CACHE]).map((c) => ({ ...c }));
  const images = (options.images ?? [NGINX, POSTGRES, ALPINE_OLD]).map((i) => ({ ...i, tags: [...i.tags] }));
  const requests = Object.assign([] as string[], { containers });
  await page.route(
    (url) => url.pathname === '/api/v1/containers' || url.pathname.startsWith('/api/v1/containers/'),
    async (route: Route) => {
      const request = route.request();
      const method = request.method();
      const url = new URL(request.url());
      const path = url.pathname.slice('/api/v1/containers'.length);
      requests.push(`${method} ${url.pathname}${url.search}`);

      if (method === 'GET' && path === '') return route.fulfill({ json: containers });
      if (method === 'GET' && path === '/images') return route.fulfill({ json: images });
      if (method === 'POST' && path === '/images/pull') {
        await options.pullHeld;
        const name = url.searchParams.get('image');
        if (name === PULLED.tags[0]) {
          images.push({ ...PULLED });
          return route.fulfill({ json: PULLED });
        }
        return route.fulfill({ status: 500, json: { detail: NOT_PULLED } });
      }
      const imageRemoval = path.match(/^\/images\/([^/]+)$/);
      if (method === 'DELETE' && imageRemoval) {
        const index = images.findIndex((i) => i.id === decodeURIComponent(imageRemoval[1]));
        if (index === -1) return route.fulfill({ status: 404, json: { detail: `Image ${imageRemoval[1]} not found` } });
        const image = images[index];
        const users = containers.filter((c) => image.tags.includes(c.image)).map((c) => c.name);
        if (users.length > 0) {
          return route.fulfill({ status: 409, json: { detail: inUse(image.tags[0] ?? image.id, users) } });
        }
        // The image goes with all its tags.
        images.splice(index, 1);
        return route.fulfill({ json: { status: 'removed' } });
      }
      const action = path.match(/^\/([^/]+)\/(start|stop|restart)$/);
      if (method === 'POST' && action) await options.actionGate?.(`${method} ${url.pathname}`);
      const target = action ? containers.find((c) => c.id === action[1]) : undefined;
      // A container that Docker no longer knows (docker.py's ValueError, answered with 404).
      if (method === 'POST' && action && !target) {
        return route.fulfill({ status: 404, json: { detail: `Container ${action[1]} not found` } });
      }
      if (method === 'POST' && action && target) {
        if (action[2] !== 'stop' && target.labels['unos.app.pool'] === 'e2e_media') {
          return route.fulfill({ status: 409, json: { detail: MEDIA_REFUSED } });
        }
        target.state = action[2] === 'stop' ? 'exited' : 'running';
        target.status = target.state;
        return route.fulfill({ json: target });
      }
      return route.abort();
    },
  );
  return requests;
}

interface FakeLogSocket {
  url: URL;
  /** The messages the page sent, in order. */
  received: string[];
  closedByPage: boolean;
  route: WebSocketRoute;
}

/**
 * Answer /ws/v1/logs/{id} like the server (supervisor/src/websocket/events.py): the first
 * message must be the web UI's {"type": "auth", "key": ...}, which the server answers with the
 * session cookie of the handshake and its session key (ADR-0397), else 4401; then auth_ok and the
 * first lines of the log.
 */
async function fakeLogSockets(page: Page, firstLines: string[]): Promise<FakeLogSocket[]> {
  const sockets: FakeLogSocket[] = [];
  await page.routeWebSocket(
    (url) => url.pathname.startsWith('/ws/v1/logs/'),
    (ws) => {
      const socket: FakeLogSocket = { url: new URL(ws.url()), received: [], closedByPage: false, route: ws };
      const containerId = decodeURIComponent(socket.url.pathname.slice('/ws/v1/logs/'.length));
      sockets.push(socket);
      ws.onMessage((message) => {
        socket.received.push(String(message));
        if (socket.received.length > 1) return;
        let auth = false;
        try {
          const first = JSON.parse(String(message));
          auth = first.type === 'auth' && !('token' in first) && typeof first.key === 'string' && first.key !== '';
        } catch {
          auth = false;
        }
        if (!auth) {
          ws.close({ code: 4401, reason: 'unauthorized' });
          return;
        }
        ws.send(JSON.stringify({ type: 'auth_ok' }));
        for (const data of firstLines) ws.send(JSON.stringify({ type: 'log', container_id: containerId, data }));
      });
      ws.onClose(() => {
        socket.closedByPage = true;
      });
    },
  );
  return sockets;
}

// Log lines as the server sends them: each starts with the time Docker received it.
const LINE_1 = '2026-11-07T10:00:01.000000000Z line 1\n';
const LINE_2 = '2026-11-07T10:00:02.000000000Z line 2\n';
const LINE_3 = '2026-11-07T10:00:03.000000000Z line 3\n';
const LIVE = 'Live: new lines appear as the container writes them';

function rows(page: Page): Locator {
  return page.locator('#docker-panel-containers tbody tr');
}

function rowOf(page: Page, name: string): Locator {
  return rows(page).filter({ hasText: name });
}

/** Open the container page, the drawer of `name` and its Logs tab, and wait until the log is live. */
async function openLogsOf(page: Page, name: string): Promise<Locator> {
  await page.goto('/containers');
  await rowOf(page, name).getByText(name).click();
  const drawer = page.getByRole('dialog', { name });
  await drawer.getByRole('button', { name: 'Logs' }).click();
  await expect(drawer.getByText(LIVE)).toBeVisible();
  return drawer;
}

// The detail of the supervisor's 503 when docker.from_env finds no Docker socket (docker.py).
const NO_DOCKER_ANSWER = /Docker is not available: Error while fetching server API version: /;

test.describe('Containers without Docker (stand-in)', () => {
  // The stand-in's own supervisor, without route fakes: the stand-in has no Docker socket, and
  // the API answers 503 with Docker's reason (ADR-0323). The test stack reaches the host's Docker.
  test.beforeEach(() => {
    test.skip(!process.env.E2E_STANDIN_STATE, "Only the stand-in has no Docker: the test stack reaches the host's");
  });

  test('the Containers page says that Docker is not available, and lists nothing', async ({
    authenticatedPage: page,
  }) => {
    const answer = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/containers');
    await page.goto('/containers');

    expect((await answer).status()).toBe(503);
    await expect(page.locator('.alert').getByText(NO_DOCKER_ANSWER)).toBeVisible();
    // The error alone: no counts of 0, search, empty list or Create button.
    const panel = page.getByRole('tabpanel', { name: 'Containers' });
    await expect(panel.getByText('Running', { exact: true })).toHaveCount(0);
    await expect(panel.getByPlaceholder('Search containers...')).toHaveCount(0);
    await expect(page.getByText('No containers found')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Create Container' })).toHaveCount(0);
  });

  test('the Images tab says that Docker is not available', async ({ authenticatedPage: page }) => {
    await page.goto('/containers');
    await page.getByRole('tab', { name: 'Images' }).click();

    const alert = page.getByRole('tabpanel', { name: 'Images' }).locator('.alert');
    await expect(alert.getByText('The images could not be read')).toBeVisible();
    await expect(alert.getByText(NO_DOCKER_ANSWER)).toBeVisible();
  });

  test('the Apps page lists the catalog and says that the installed apps cannot be loaded', async ({
    authenticatedPage: page,
  }) => {
    const answer = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/apps/installed');
    await page.goto('/apps');

    expect((await answer).status()).toBe(503);
    const alert = page.locator('.alert', { hasText: 'The installed apps could not be loaded' });
    await expect(alert.getByText(NO_DOCKER_ANSWER)).toBeVisible();
    // The catalog does not wait for Docker (apps.catalog-without-docker).
    await expect(page.getByRole('heading', { name: 'Jellyfin' }).first()).toBeVisible();
    await expect(page.getByText(/^\d+ apps available$/)).toBeVisible();
    await expect(page.getByText(/installed$/)).toHaveCount(0);

    // The Installed tab has no count, and says why it lists nothing, instead of "No apps installed".
    await page.getByRole('button', { name: 'Installed', exact: true }).click();
    await expect(page.getByText('No apps installed')).toHaveCount(0);
    await expect(alert.getByText(NO_DOCKER_ANSWER)).toBeVisible();
  });
});

test.describe('Containers', () => {
  test('lists every container with its state and filters by state', async ({ authenticatedPage: page }) => {
    await fakeContainersApi(page);
    await page.goto('/containers');

    await expect(rows(page)).toHaveCount(3);
    await expect(rowOf(page, 'e2e-web').getByText('running', { exact: true })).toBeVisible();
    await expect(rowOf(page, 'e2e-web').getByText('nginx:1.27')).toBeVisible();
    await expect(rowOf(page, 'e2e-db').getByText('exited', { exact: true })).toBeVisible();
    await expect(rowOf(page, 'e2e-cache').getByText('paused', { exact: true })).toBeVisible();

    const stateFilter = page.locator('#docker-panel-containers select');
    for (const [state, name] of [['running', 'e2e-web'], ['exited', 'e2e-db'], ['paused', 'e2e-cache']]) {
      await stateFilter.selectOption(state);
      await expect(rows(page)).toHaveCount(1);
      await expect(rowOf(page, name)).toBeVisible();
    }
    await stateFilter.selectOption('');
    await expect(rows(page)).toHaveCount(3);
  });

  test('Stop, Start and Restart act on the container', async ({ authenticatedPage: page }) => {
    const requests = await fakeContainersApi(page);
    await page.goto('/containers');
    const web = rowOf(page, 'e2e-web');

    await web.getByRole('button', { name: 'Stop' }).click();
    await expect(web.getByText('exited', { exact: true })).toBeVisible();
    await web.getByRole('button', { name: 'Start' }).click();
    await expect(web.getByText('running', { exact: true })).toBeVisible();
    await web.getByRole('button', { name: 'Restart' }).click();
    await expect.poll(() => requests.filter((r) => r.startsWith('POST'))).toEqual([
      `POST /api/v1/containers/${WEB.id}/stop`,
      `POST /api/v1/containers/${WEB.id}/start`,
      `POST /api/v1/containers/${WEB.id}/restart`,
    ]);
    await expect(web.getByText('running', { exact: true })).toBeVisible();
  });

  test('a UNOS app whose pool is not mounted is not started, and the page says which pool to mount', async ({
    authenticatedPage: page,
  }) => {
    await fakeContainersApi(page, { containers: [WEB, MEDIA] });
    await page.goto('/containers');
    const media = rowOf(page, 'e2e-media');

    await media.getByRole('button', { name: 'Start' }).click();

    await expect(page.getByText(MEDIA_REFUSED)).toBeVisible();
    await expect(media.getByText('exited', { exact: true })).toBeVisible();
  });

  test('the log of a container streams live over the WebSocket', async ({ authenticatedPage: page }) => {
    const requests = await fakeContainersApi(page);
    const sockets = await fakeLogSockets(page, [LINE_1, LINE_2]);
    await page.goto('/containers');

    await rowOf(page, 'e2e-web').getByText('e2e-web').click();
    const drawer = page.getByRole('dialog', { name: 'e2e-web' });
    await drawer.getByRole('button', { name: 'Logs' }).click();

    const log = drawer.getByLabel('Log of e2e-web');
    await expect(drawer.getByText(LIVE)).toBeVisible();
    await expect.poll(() => log.textContent()).toBe(LINE_1 + LINE_2);
    // The first message authenticates the socket, with the session cookie of the handshake and the
    // session key: no token in it nor in the URL (ADR-0031, ADR-0397).
    expect(sockets).toHaveLength(1);
    expect(sockets[0].url.pathname).toBe(`/ws/v1/logs/${WEB.id}`);
    expect(sockets[0].url.search).toBe('');
    expect(JSON.parse(sockets[0].received[0])).toEqual({ type: 'auth', key: await pageSessionKey(page) });

    // A line the container writes now appears without a reload.
    sockets[0].route.send(JSON.stringify({ type: 'log', container_id: WEB.id, data: LINE_3 }));
    await expect.poll(() => log.textContent()).toBe(LINE_1 + LINE_2 + LINE_3);

    // The log ends (the container stopped), and the server closes with 1000: the drawer
    // says so and offers to reconnect.
    await sockets[0].route.close({ code: 1000, reason: 'log ended' });
    await expect(drawer.getByText('The log stream ended')).toBeVisible();
    await drawer.getByRole('button', { name: 'Reconnect' }).click();
    await expect.poll(() => sockets.length).toBe(2);
    await expect(drawer.getByText(LIVE)).toBeVisible();
    await expect.poll(() => log.textContent()).toBe(LINE_1 + LINE_2);

    // Closing the drawer closes the socket.
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect.poll(() => sockets[1].closedByPage).toBe(true);
    // The log came only over the socket: no request polled the HTTP log endpoint.
    expect(requests.filter((r) => r.includes('/logs'))).toEqual([]);
  });

  test('a log that cannot be read shows the reason', async ({ authenticatedPage: page }) => {
    await fakeContainersApi(page);
    const sockets = await fakeLogSockets(page, []);
    await page.goto('/containers');

    await rowOf(page, 'e2e-db').getByText('e2e-db').click();
    const drawer = page.getByRole('dialog', { name: 'e2e-db' });
    await drawer.getByRole('button', { name: 'Logs' }).click();
    await expect.poll(() => sockets.length).toBe(1);
    await expect(drawer.getByText(LIVE)).toBeVisible();

    // What the server sends when Docker no longer knows the container, before it closes with 1000.
    const reason = `404 Client Error for http+docker://localhost/v1.47/containers/${DB.id}/json: Not Found ("No such container: ${DB.id}")`;
    sockets[0].route.send(JSON.stringify({ type: 'error', message: reason }));
    await sockets[0].route.close({ code: 1000, reason: 'log error' });

    await expect(drawer.getByText('The log could not be read')).toBeVisible();
    await expect(drawer.getByText(reason)).toBeVisible();
    await expect(drawer.getByText('The log stream ended')).toBeVisible();
  });

  test('a lost connection to the log says so and offers Reconnect', async ({ authenticatedPage: page }) => {
    await fakeContainersApi(page);
    const sockets = await fakeLogSockets(page, [LINE_1]);
    const drawer = await openLogsOf(page, 'e2e-web');

    // 1011: the server could not check the session again; any close but 1000 is a lost connection.
    await sockets[0].route.close({ code: 1011, reason: 're-authentication failed' });

    await expect(drawer.getByText('The connection to the log was lost')).toBeVisible();
    await drawer.getByRole('button', { name: 'Reconnect' }).click();
    await expect.poll(() => sockets.length).toBe(2);
    await expect(drawer.getByText(LIVE)).toBeVisible();
  });

  test('a log refused with 4403 says that only administrators read it, and the session stays', async ({
    authenticatedPage: page,
  }) => {
    await fakeContainersApi(page);
    const sockets = await fakeLogSockets(page, [LINE_1]);
    const drawer = await openLogsOf(page, 'e2e-web');

    // The account is no administrator any more (supervisor/src/websocket/events.py).
    await sockets[0].route.close({ code: 4403, reason: 'forbidden' });

    await expect(drawer.getByText('Only administrators can read container logs')).toBeVisible();
    await expect(drawer.getByRole('button', { name: 'Reconnect' })).toHaveCount(0);
    await expect(page).toHaveURL('/containers');
    await expect(page.locator('aside').getByText('Administrator', { exact: true })).toBeVisible();
  });

  test('a log socket closed with 4401 ends the session', async ({ authenticatedPage: page }) => {
    await fakeContainersApi(page);
    const sockets = await fakeLogSockets(page, [LINE_1]);
    await openLogsOf(page, 'e2e-web');

    // The session is no longer valid (supervisor/src/websocket/events.py): as an HTTP 401 does.
    await sockets[0].route.close({ code: 4401, reason: 'unauthorized' });

    await expect(page).toHaveURL('/login');
    await expect(page.locator('aside')).toHaveCount(0);
    expect(await pageSessionKey(page)).toBeNull();
  });

  test('the log view keeps the newest 500,000 characters and follows new lines at its end', async ({
    authenticatedPage: page,
  }) => {
    await fakeContainersApi(page);
    const sockets = await fakeLogSockets(page, []);
    const drawer = await openLogsOf(page, 'e2e-web');
    const log = drawer.getByLabel('Log of e2e-web');
    const send = (data: string) => sockets[0].route.send(JSON.stringify({ type: 'log', container_id: WEB.id, data }));

    // Lines of 100 characters, 6,000 of them in six pieces: 600,000 characters.
    const line = (n: number) => `${String(n).padStart(6, '0')} ${'x'.repeat(92)}\n`;
    for (let piece = 0; piece < 6; piece++) {
      send(Array.from({ length: 1000 }, (_, i) => line(piece * 1000 + i + 1)).join(''));
    }

    const endsWith = (text: string) => log.evaluate((pre, end) => pre.textContent?.endsWith(end), text);

    // The oldest 1,000 lines are gone: what is left starts at a line and holds 500,000 characters.
    await expect.poll(() => endsWith(line(6000))).toBe(true);
    const kept = (await log.textContent()) ?? '';
    expect(kept.length).toBe(500_000);
    expect(kept.startsWith(line(1001))).toBe(true);
    // The view followed the new lines to its end.
    await expect.poll(() => log.evaluate((pre) => pre.scrollHeight - pre.scrollTop - pre.clientHeight)).toBeLessThan(24);

    // Scrolled up, it stays where the reader is while new lines arrive.
    await log.evaluate((pre) => pre.scrollTo({ top: 0 }));
    send(line(6001));
    await expect.poll(() => endsWith(line(6001))).toBe(true);
    expect(await log.evaluate((pre) => pre.scrollTop)).toBe(0);
  });
});

test.describe('Images', () => {
  test('lists the images; pull adds one, and remove deletes one that no container uses', async ({
    authenticatedPage: page,
  }) => {
    let releasePull = () => {};
    const pullHeld = new Promise<void>((resolve) => (releasePull = resolve));
    const requests = await fakeContainersApi(page, { pullHeld });
    await page.goto('/containers');
    await page.getByRole('tab', { name: 'Images' }).click();

    const table = page.getByRole('table', { name: 'Images' });
    const imageRow = (tag: string) => table.locator('tbody tr').filter({ hasText: tag });
    await expect(table.locator('tbody tr')).toHaveCount(3);
    await expect(imageRow('alpine:3.19')).toContainText(ALPINE_OLD.id);
    await expect(imageRow('alpine:3.19')).toContainText('7.44 MB');

    // Pull: the page waits for the pull, then lists the new image.
    await page.getByLabel('Image', { exact: true }).fill('alpine:3.20');
    await page.getByRole('button', { name: 'Pull' }).click();
    await expect(page.getByText('Pulling alpine:3.20. A large image can take several minutes.')).toBeVisible();
    releasePull();
    await expect(page.getByText('Pulled alpine:3.20.')).toBeVisible();
    await expect(imageRow('alpine:3.20')).toContainText(PULLED.id);
    expect(requests).toContain('POST /api/v1/containers/images/pull?image=alpine%3A3.20');

    // Remove an image that no container uses: the dialog names the tag that goes with it.
    await page.getByRole('button', { name: 'Remove image alpine:3.19' }).click();
    const dialog = page.getByRole('dialog', { name: 'Remove Image' });
    await expect(dialog).toContainText('Its other tags go with it: alpine:3.');
    await dialog.getByRole('button', { name: 'Remove Image' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText('Removed alpine:3.19.')).toBeVisible();
    await expect(table.locator('tbody tr').filter({ hasText: ALPINE_OLD.id })).toHaveCount(0);
    expect(requests).toContain(`DELETE /api/v1/containers/images/${ALPINE_OLD.id}?force=false`);

    // An image that a container uses stays, and the answer names the container; it is never forced.
    await page.getByRole('button', { name: 'Remove image nginx:1.27' }).click();
    await expect(dialog).not.toContainText('Its other tags go with it');
    await dialog.getByRole('button', { name: 'Remove Image' }).click();
    await expect(dialog.getByText('The image was not removed')).toBeVisible();
    await expect(dialog.getByText(IN_USE)).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    await expect(imageRow('nginx:1.27')).toHaveCount(1);
    expect(requests).toContain(`DELETE /api/v1/containers/images/${NGINX.id}?force=false`);
    expect(requests.filter((r) => r.includes('force=true'))).toEqual([]);
  });

  test('a pull that fails shows the reason', async ({ authenticatedPage: page }) => {
    await fakeContainersApi(page);
    await page.goto('/containers');
    await page.getByRole('tab', { name: 'Images' }).click();

    await page.getByLabel('Image', { exact: true }).fill('e2e/missing:1');
    await page.getByRole('button', { name: 'Pull' }).click();

    await expect(page.getByText('The image was not pulled')).toBeVisible();
    await expect(page.getByText(NOT_PULLED)).toBeVisible();
    await expect(page.getByRole('table', { name: 'Images' }).locator('tbody tr')).toHaveCount(3);
  });
});

test.describe('In Russian', () => {
  // The language chosen in this browser, as the Account page keeps it (ADR-0287).
  const LOCALE_KEY = 'unos-locale';
  // A date in Russian: the day first and a 24-hour clock, not the en-US browser's own format.
  const RUSSIAN_DATE = /^\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}:\d{2}$/;

  test('the page, the drawer and the Images tab come from the catalog', async ({ authenticatedPage: page }) => {
    // e2e-web publishes a TCP and a UDP port, for the drawer's Network tab.
    const ports = [
      { host_port: 8080, container_port: 80, protocol: 'tcp' },
      { host_port: 5353, container_port: 53, protocol: 'udp' },
    ];
    await fakeContainersApi(page, { containers: [{ ...WEB, ports }, DB, CACHE] });
    await fakeLogSockets(page, [LINE_1]);
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    await page.goto('/containers');

    await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Контейнеры');
    await expect(page.getByRole('tab')).toHaveText(['Контейнеры', 'Образы']);
    const panel = page.locator('#docker-panel-containers');
    await expect(panel.locator('thead th')).toHaveText(['', 'Имя', 'Образ', 'Состояние', 'Порты', 'Действия']);
    // A state is the server's word, shown through its key; the buttons are named by their titles.
    const web = rowOf(page, 'e2e-web');
    await expect(web.getByText('работает', { exact: true })).toBeVisible();
    await expect(rowOf(page, 'e2e-db').getByText('остановлен', { exact: true })).toBeVisible();
    await expect(rowOf(page, 'e2e-cache').getByText('приостановлен', { exact: true })).toBeVisible();
    await expect(panel.getByText('running', { exact: true })).toHaveCount(0);
    await expect(web.getByRole('button', { name: 'Остановить' })).toBeVisible();

    // The cards show the states through their keys too. A card is a button that opens the drawer.
    await page.getByRole('button', { name: 'Карточки' }).click();
    const card = (name: string) => panel.getByRole('button').filter({ hasText: name });
    await expect(card('e2e-web').getByText('работает', { exact: true })).toBeVisible();
    await expect(card('e2e-db').getByText('остановлен', { exact: true })).toBeVisible();
    await expect(card('e2e-cache').getByText('приостановлен', { exact: true })).toBeVisible();
    await expect(panel.getByText('running', { exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Таблица' }).click();
    await expect(web).toBeVisible();

    // The form of a new container.
    await page.getByRole('button', { name: 'Создать контейнер' }).click();
    const form = page.getByRole('dialog', { name: 'Новый контейнер' });
    await expect(form.getByLabel('Имя контейнера')).toBeVisible();
    await expect(form.getByLabel('Политика перезапуска')).toBeVisible();
    await form.getByRole('button', { name: 'Отмена' }).click();
    await expect(form).toBeHidden();

    // The drawer: its tabs, the state in its header and the status (Docker's words) in the
    // page's language, the date day first, the ports' protocols, and the state of the log.
    await web.getByText('e2e-web').click();
    const drawer = page.getByRole('dialog', { name: 'e2e-web' });
    await expect(drawer.getByRole('button', { name: 'Сведения' })).toBeVisible();
    await expect(drawer.locator('.badge')).toHaveText(['работает']);
    await expect(drawer.locator('.card-nested').filter({ hasText: 'Статус' })).toContainText('работает');
    await expect(drawer.locator('.card-nested').filter({ hasText: 'Создан' }).locator('div').last()).toHaveText(
      RUSSIAN_DATE,
    );
    await expect(drawer.getByText('running', { exact: true })).toHaveCount(0);
    await drawer.getByRole('button', { name: 'Сеть' }).click();
    await expect(drawer.getByText('Проброс портов', { exact: true })).toBeVisible();
    // The header's state, then a badge per port with its protocol (Docker's tcp and udp).
    await expect(drawer.locator('.badge')).toHaveText(['работает', 'TCP', 'UDP']);
    await drawer.getByRole('button', { name: 'Журнал' }).click();
    await expect(
      drawer.getByText('В реальном времени: новые строки появляются, как только контейнер их записывает'),
    ).toBeVisible();
    await expect(drawer.getByLabel('Журнал контейнера e2e-web')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();

    // The Images tab, an image's date day first, and the removal of an image with two tags.
    await page.getByRole('tab', { name: 'Образы' }).click();
    const table = page.getByRole('table', { name: 'Образы' });
    await expect(table.getByRole('columnheader')).toHaveText(['Образ', 'ID', 'Размер', 'Создан', 'Действия']);
    // The fourth cell of a row is its date (Создан).
    await expect(table.locator('tbody tr').filter({ hasText: 'alpine:3.19' }).locator('td').nth(3)).toHaveText(
      RUSSIAN_DATE,
    );
    await expect(page.getByLabel('Образ', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Скачать' })).toBeVisible();
    await page.getByRole('button', { name: 'Удалить образ alpine:3.19' }).click();
    const dialog = page.getByRole('dialog', { name: 'Удаление образа' });
    await expect(dialog).toContainText(
      `Удалить образ alpine:3.19 (${ALPINE_OLD.id})? Вместе с ним удаляются и другие его теги: alpine:3. ` +
        'Образ, который используется контейнером, не удаляется. Чтобы использовать образ позже, скачайте его снова.',
    );
    await dialog.getByRole('button', { name: 'Отмена' }).click();
    await expect(dialog).toBeHidden();
  });
});

// Several containers at once (webui.bulk-actions, ADR-0389): the page calls the endpoint of each
// selected container, one after another, and lists each one's result.
test.describe('Several containers at once', () => {
  const posts = (requests: string[]) => requests.filter((r) => r.startsWith('POST'));
  const toolbar = (page: Page) => page.getByRole('toolbar', { name: 'Selected containers' });
  const button = (page: Page, name: string) => toolbar(page).getByRole('button', { name, exact: true });
  const checkbox = (page: Page, name: string) => page.getByRole('checkbox', { name: `Select ${name}`, exact: true });
  const selectAll = (page: Page) => page.getByRole('checkbox', { name: 'Select all shown', exact: true });

  test('Stop acts on every selected container, one after another, and shows its progress', async ({
    authenticatedPage: page,
  }) => {
    const gates: (() => void)[] = [];
    const requests = await fakeContainersApi(page, {
      actionGate: () => new Promise<void>((resolve) => gates.push(resolve)),
    });
    await page.goto('/containers');
    await expect(rows(page)).toHaveCount(3);
    await expect(toolbar(page)).toContainText('0 containers selected');
    for (const name of ['Start selected', 'Stop selected', 'Restart selected']) {
      await expect(button(page, name)).toBeDisabled();
    }

    await checkbox(page, 'e2e-web').check();
    await checkbox(page, 'e2e-cache').check();
    // A checkbox selects; it does not open the container's drawer.
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(toolbar(page)).toContainText('2 containers selected');

    await button(page, 'Stop selected').click();
    // In the order of the list, and the next only after the answer for the one before.
    await expect(page.getByText('Stopping e2e-web (1 of 2)')).toBeVisible();
    await expect.poll(() => gates.length).toBe(1);
    expect(posts(requests)).toEqual([`POST /api/v1/containers/${WEB.id}/stop`]);
    // Nothing else is started while it runs: not from the bar or a row. The drawer has no actions:
    // a container's are its row's (ADR-0431).
    await expect(button(page, 'Start selected')).toBeDisabled();
    await expect(checkbox(page, 'e2e-db')).toBeDisabled();
    await expect(rowOf(page, 'e2e-db').getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
    await expect(rowOf(page, 'e2e-db').getByRole('button', { name: 'Remove', exact: true })).toBeDisabled();
    await rowOf(page, 'e2e-db').getByText('e2e-db').click();
    const drawer = page.getByRole('dialog', { name: 'e2e-db' });
    await expect(drawer.getByRole('button', { name: 'Start', exact: true })).toHaveCount(0);
    await expect(drawer.getByRole('button', { name: 'Remove', exact: true })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    gates.shift()!();
    await expect(page.getByText('Stopping e2e-cache (2 of 2)')).toBeVisible();
    await expect.poll(() => gates.length).toBe(1);
    gates.shift()!();

    const result = page.getByRole('list', { name: 'Stopped 2 of 2 containers' });
    await expect(result.getByRole('listitem')).toHaveText(['e2e-web: exited', 'e2e-cache: exited']);
    expect(posts(requests)).toEqual([
      `POST /api/v1/containers/${WEB.id}/stop`,
      `POST /api/v1/containers/${CACHE.id}/stop`,
    ]);
    await expect(rowOf(page, 'e2e-web').getByText('exited', { exact: true })).toBeVisible();
    await expect(rowOf(page, 'e2e-cache').getByText('exited', { exact: true })).toBeVisible();
    await expect(rowOf(page, 'e2e-db').getByText('exited', { exact: true })).toBeVisible();
    // Once it ended, a row's own actions are back.
    await expect(rowOf(page, 'e2e-db').getByRole('button', { name: 'Start', exact: true })).toBeEnabled();
    // What was done leaves the selection.
    await expect(toolbar(page)).toContainText('0 containers selected');
    await expect(checkbox(page, 'e2e-web')).not.toBeChecked();
  });

  test('Start and Restart act on all of them; a refusal is listed with its reason and the others go on', async ({
    authenticatedPage: page,
  }) => {
    const requests = await fakeContainersApi(page, { containers: [WEB, DB, MEDIA, CACHE] });
    await page.goto('/containers');
    await expect(rows(page)).toHaveCount(4);

    await selectAll(page).check();
    await expect(toolbar(page)).toContainText('4 containers selected');
    await button(page, 'Start selected').click();

    const started = page.getByRole('list', { name: 'Started 3 of 4 containers' });
    await expect(started.getByRole('listitem')).toHaveText([
      'e2e-web: running',
      'e2e-db: running',
      `e2e-media: failed: ${MEDIA_REFUSED}`,
      'e2e-cache: running',
    ]);
    expect(posts(requests)).toEqual(
      [WEB, DB, MEDIA, CACHE].map((c) => `POST /api/v1/containers/${c.id}/start`),
    );
    await expect(rowOf(page, 'e2e-db').getByText('running', { exact: true })).toBeVisible();
    await expect(rowOf(page, 'e2e-media').getByText('exited', { exact: true })).toBeVisible();
    // The one that failed stays selected, for another try; the others leave the selection.
    await expect(toolbar(page)).toContainText('1 container selected');
    await expect(checkbox(page, 'e2e-media')).toBeChecked();
    await expect(checkbox(page, 'e2e-web')).not.toBeChecked();

    // Restart, as on a container's own row, is for the running ones: e2e-media, stopped, is listed
    // as not restarted, and no request is sent for it.
    await checkbox(page, 'e2e-db').check();
    await button(page, 'Restart selected').click();
    const restarted = page.getByRole('list', { name: 'Restarted 1 of 2 containers' });
    await expect(restarted.getByRole('listitem')).toHaveText([
      'e2e-db: running',
      'e2e-media: not restarted: it is not running',
    ]);
    expect(posts(requests).slice(4)).toEqual([`POST /api/v1/containers/${DB.id}/restart`]);
    await expect(checkbox(page, 'e2e-media')).toBeChecked();
    await expect(checkbox(page, 'e2e-db')).not.toBeChecked();
    // Only the last action's results are shown.
    await expect(started).toHaveCount(0);
  });

  test('the selection holds only what the list shows, also after a container is gone elsewhere', async ({
    authenticatedPage: page,
  }) => {
    const requests = await fakeContainersApi(page);
    await page.goto('/containers');

    // Select all shown selects what the search shows; what it hides leaves the selection.
    await page.getByPlaceholder('Search containers...').fill('e2e-d');
    await expect(rows(page)).toHaveCount(1);
    await selectAll(page).check();
    await expect(toolbar(page)).toContainText('1 container selected');
    await page.getByPlaceholder('Search containers...').fill('');
    await expect(rows(page)).toHaveCount(3);
    await expect(checkbox(page, 'e2e-db')).toBeChecked();
    await expect(selectAll(page)).not.toBeChecked();

    // All three selected, then the state filter shows only e2e-web: an action reaches it alone.
    await selectAll(page).check();
    await expect(toolbar(page)).toContainText('3 containers selected');
    await page.locator('#docker-panel-containers select').selectOption('running');
    await expect(rows(page)).toHaveCount(1);
    await expect(toolbar(page)).toContainText('1 container selected');
    await button(page, 'Stop selected').click();
    await expect(page.getByRole('list', { name: 'Stopped 1 of 1 container' }).getByRole('listitem')).toHaveText([
      'e2e-web: exited',
    ]);
    expect(posts(requests)).toEqual([`POST /api/v1/containers/${WEB.id}/stop`]);
    await page.locator('#docker-panel-containers select').selectOption('');
    await expect(rows(page)).toHaveCount(3);
    await expect(toolbar(page)).toContainText('0 containers selected');

    // The cards have the same checkboxes, which do not open the drawer either.
    await page.getByRole('button', { name: 'Card view' }).click();
    await checkbox(page, 'e2e-web').check();
    await checkbox(page, 'e2e-db').check();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(toolbar(page)).toContainText('2 containers selected');

    // e2e-db is removed elsewhere after the page listed it: its start is refused with the server's
    // 404, e2e-web is started all the same, and e2e-db leaves the list and the selection.
    requests.containers.splice(requests.containers.findIndex((c) => c.id === DB.id), 1);
    await button(page, 'Start selected').click();
    const result = page.getByRole('list', { name: 'Started 1 of 2 containers' });
    await expect(result.getByRole('listitem')).toHaveText([
      'e2e-web: running',
      `e2e-db: failed: Container ${DB.id} not found`,
    ]);
    await expect(page.locator('#docker-panel-containers').getByText('e2e-db', { exact: true })).toHaveCount(0);
    await expect(toolbar(page)).toContainText('0 containers selected');
  });

  test('leaving the page ends a run after the container it is at', async ({ authenticatedPage: page }) => {
    const gates: (() => void)[] = [];
    const requests = await fakeContainersApi(page, {
      actionGate: () => new Promise<void>((resolve) => gates.push(resolve)),
    });
    await page.goto('/containers');
    await selectAll(page).check();
    await button(page, 'Stop selected').click();
    await expect.poll(() => gates.length).toBe(1);

    // Another page of the web UI, without a reload, while the first stop waits for its answer.
    await page.locator('aside nav a[href="/shares"]').click();
    await expect(page).toHaveURL('/shares');
    const answered = page.waitForResponse((r) => r.url().endsWith(`/api/v1/containers/${WEB.id}/stop`));
    gates.shift()!();
    await answered;
    // Time for the next request, which does not come.
    await page.waitForTimeout(1000);
    expect(posts(requests)).toEqual([`POST /api/v1/containers/${WEB.id}/stop`]);
    expect(gates).toHaveLength(0);
  });

  test('a 401 ends the session and the run', async ({ authenticatedPage: page }) => {
    const requests = await fakeContainersApi(page);
    // The session expired while the run went on: the answer for e2e-db is 401.
    const refused: string[] = [];
    await page.route(`**/api/v1/containers/${DB.id}/stop`, (route) => {
      refused.push(route.request().url());
      return route.fulfill({ status: 401, json: { detail: 'Could not validate credentials' } });
    });
    await page.goto('/containers');
    await selectAll(page).check();
    await button(page, 'Stop selected').click();

    await expect(page).toHaveURL('/login');
    expect(posts(requests)).toEqual([`POST /api/v1/containers/${WEB.id}/stop`]);
    expect(refused).toHaveLength(1);
  });

  test('the selection and the results come from the catalog in Russian', async ({ authenticatedPage: page }) => {
    await fakeContainersApi(page);
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.goto('/containers');

    const bar = page.getByRole('toolbar', { name: 'Выбранные контейнеры' });
    await expect(bar).toContainText('Выбрано 0 контейнеров');
    await page.getByRole('checkbox', { name: 'Выбрать e2e-web', exact: true }).check();
    await expect(bar).toContainText('Выбран 1 контейнер');
    await page.getByRole('checkbox', { name: 'Выбрать все показанные', exact: true }).check();
    await expect(bar).toContainText('Выбрано 3 контейнера');
    await page.getByRole('checkbox', { name: 'Выбрать e2e-cache', exact: true }).uncheck();
    await bar.getByRole('button', { name: 'Остановить выбранные', exact: true }).click();
    const result = page.getByRole('list', { name: 'Остановлено 2 из 2 контейнеров' });
    await expect(result.getByRole('listitem')).toHaveText(['e2e-web: остановлен', 'e2e-db: остановлен']);
    await expect(bar.getByRole('button', { name: 'Запустить выбранные', exact: true })).toBeDisabled();
    await expect(bar.getByRole('button', { name: 'Перезапустить выбранные', exact: true })).toBeDisabled();

    await page.getByRole('checkbox', { name: 'Выбрать e2e-web', exact: true }).check();
    await bar.getByRole('button', { name: 'Перезапустить выбранные', exact: true }).click();
    await expect(
      page.getByRole('list', { name: 'Перезапущено 0 из 1 контейнера' }).getByRole('listitem'),
    ).toHaveText(['e2e-web: не перезапущен: контейнер не работает']);
  });
});
