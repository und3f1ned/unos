import type { Locator, Page, Route, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';

// The stand-in has no Docker. The page's requests to /api/v1/containers are
// answered from memory, and so is the log socket /ws/v1/logs/{id}: nothing
// reaches Docker. Unknown requests are aborted. The error texts are the
// supervisor's (supervisor/src/containers/router.py and apps/router.py) and
// Docker's reasons that it passes on, word for word.

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

function container(id: string, name: string, image: string, state: string): FakeContainer {
  const status = state === 'running' ? 'Up 2 hours' : state === 'paused' ? 'Up 2 hours (Paused)' : 'Exited (0) 3 hours ago';
  return { id, name, image, status, state, created: '2026-11-01T10:00:00Z', ports: [], labels: {} };
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
const ALPINE_OLD: FakeImage = { id: 'sha256:aa11bb22cc33', tags: ['alpine:3.19'], size: 7_800_000, created: '2026-01-05T08:00:00Z' };
const PULLED: FakeImage = { id: 'sha256:bb22cc33dd44', tags: ['alpine:3.20'], size: 8_100_000, created: '2026-11-02T08:00:00Z' };

// Docker's reason for an image that a container uses, as the server passes it on with 409.
const IN_USE = `conflict: unable to delete 605c77e624dd (cannot be forced) - image is being used by running container ${WEB.id}`;
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
}

/** Serve the containers API from memory; returns every request of the page to it ("METHOD path?query"). */
async function fakeContainersApi(page: Page, options: FakeDockerOptions = {}): Promise<string[]> {
  const containers = (options.containers ?? [WEB, DB, CACHE]).map((c) => ({ ...c }));
  const images = (options.images ?? [NGINX, POSTGRES, ALPINE_OLD]).map((i) => ({ ...i, tags: [...i.tags] }));
  const requests: string[] = [];
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
        if (containers.some((c) => images[index].tags.includes(c.image))) {
          return route.fulfill({ status: 409, json: { detail: IN_USE } });
        }
        images.splice(index, 1);
        return route.fulfill({ json: { status: 'removed' } });
      }
      const action = path.match(/^\/([^/]+)\/(start|stop|restart)$/);
      const target = action ? containers.find((c) => c.id === action[1]) : undefined;
      if (method === 'POST' && action && target) {
        if (action[2] !== 'stop' && target.labels['unos.app.pool'] === 'e2e_media') {
          return route.fulfill({ status: 409, json: { detail: MEDIA_REFUSED } });
        }
        target.state = action[2] === 'stop' ? 'exited' : 'running';
        target.status = action[2] === 'stop' ? 'Exited (0) 1 second ago' : 'Up 1 second';
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
 * message must carry the token, else 4401; then auth_ok and the first lines of the log.
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
        let token: unknown;
        try {
          const first = JSON.parse(String(message));
          token = first.type === 'auth' ? first.token : undefined;
        } catch {
          token = undefined;
        }
        if (typeof token !== 'string' || !token) {
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

function rows(page: Page): Locator {
  return page.locator('#docker-panel-containers tbody tr');
}

function rowOf(page: Page, name: string): Locator {
  return rows(page).filter({ hasText: name });
}

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
    const sockets = await fakeLogSockets(page, ['line 1\n', 'line 2\n']);
    await page.goto('/containers');

    await rowOf(page, 'e2e-web').getByText('e2e-web').click();
    const drawer = page.getByRole('dialog', { name: 'e2e-web' });
    await drawer.getByRole('button', { name: 'Logs' }).click();

    const log = drawer.getByLabel('Log of e2e-web');
    await expect(drawer.getByText('Live: new lines appear as the container writes them')).toBeVisible();
    await expect.poll(() => log.textContent()).toBe('line 1\nline 2\n');
    // The token travels in the first message, never in the URL (ADR-0031).
    expect(sockets).toHaveLength(1);
    expect(sockets[0].url.pathname).toBe(`/ws/v1/logs/${WEB.id}`);
    expect(sockets[0].url.search).toBe('');
    const token = await page.evaluate(() => localStorage.getItem('token'));
    expect(JSON.parse(sockets[0].received[0])).toEqual({ type: 'auth', token });

    // A line the container writes now appears without a reload.
    sockets[0].route.send(JSON.stringify({ type: 'log', container_id: WEB.id, data: 'line 3\n' }));
    await expect.poll(() => log.textContent()).toBe('line 1\nline 2\nline 3\n');

    // The stream ends (the container stopped): the drawer says so and offers to reconnect.
    await sockets[0].route.close({ code: 1000 });
    await expect(drawer.getByText('The log stream ended')).toBeVisible();
    await drawer.getByRole('button', { name: 'Reconnect' }).click();
    await expect.poll(() => sockets.length).toBe(2);
    await expect(drawer.getByText('Live: new lines appear as the container writes them')).toBeVisible();
    await expect.poll(() => log.textContent()).toBe('line 1\nline 2\n');

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
    await expect(drawer.getByText('Live: new lines appear as the container writes them')).toBeVisible();

    // What the server sends when Docker no longer knows the container, before it closes the socket.
    const reason = `404 Client Error for http+docker://localhost/v1.47/containers/${DB.id}/json: Not Found ("No such container: ${DB.id}")`;
    sockets[0].route.send(JSON.stringify({ type: 'error', message: reason }));
    await sockets[0].route.close({ code: 1000 });

    await expect(drawer.getByText('The log could not be read')).toBeVisible();
    await expect(drawer.getByText(reason)).toBeVisible();
    await expect(drawer.getByText('The log stream ended')).toBeVisible();
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

    // Remove an image that no container uses.
    await page.getByRole('button', { name: 'Remove image alpine:3.19' }).click();
    const dialog = page.getByRole('dialog', { name: 'Remove Image' });
    await dialog.getByRole('button', { name: 'Remove Image' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText('Removed alpine:3.19.')).toBeVisible();
    await expect(imageRow('alpine:3.19')).toHaveCount(0);
    expect(requests).toContain(`DELETE /api/v1/containers/images/${ALPINE_OLD.id}?force=false`);

    // An image that a container uses stays, with Docker's reason; it is never forced.
    await page.getByRole('button', { name: 'Remove image nginx:1.27' }).click();
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
