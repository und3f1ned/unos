import type { WebSocketRoute } from '@playwright/test';
import { test, expect, E2E_ADMIN } from './fixtures';
import { pageSessionKey, sessionToken } from './session';

// The stats socket authenticates with its first message, which takes the session cookie of the
// handshake (ADR-0395). A token in the URL would end up in proxy and server access logs (and the
// server refuses it).
test.describe('Live stats WebSocket', () => {
  test('a socket closed with 4401 leads to the sign-in page once', async ({ page }) => {
    // The server closes the socket with 4401 when its session stops being valid: it checks the
    // session again every 60 seconds (ADR-0031). Here a fake server stands in for the socket
    // only, so that the HTTP requests, whose session stays valid, cannot end the session first.
    const sockets: WebSocketRoute[] = [];
    const received: string[] = [];
    await page.routeWebSocket('**/ws/v1/events', (ws) => {
      sockets.push(ws);
      ws.onMessage((message) => {
        received.push(String(message));
        // The first message authenticates the socket, as the server answers it.
        if (received.length === 1) ws.send(JSON.stringify({ type: 'auth_ok' }));
      });
    });
    await page.goto('/login');
    await page.fill('#username', E2E_ADMIN.username);
    await page.fill('#password', E2E_ADMIN.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/');
    await expect(page.locator('aside').getByText('Administrator', { exact: true })).toBeVisible();
    await expect.poll(() => received.length).toBeGreaterThan(0);
    // No token: the server takes the session cookie of the handshake, with the session key (ADR-0395).
    expect(JSON.parse(received[0])).toEqual({ type: 'auth', key: await pageSessionKey(page) });
    expect(sockets).toHaveLength(1);

    const pages: string[] = [];
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame()) pages.push(new URL(frame.url()).pathname);
    });
    await sockets[0].close({ code: 4401, reason: 'Invalid token' });

    await expect(page).toHaveURL('/login');
    await expect(page.locator('#username')).toBeVisible();
    // The tab no longer counts the session as its own.
    expect(await pageSessionKey(page)).toBeNull();
    // Once: no second redirect, and no new socket, also after the first reconnect delay (1 to 1.2 s).
    await page.waitForTimeout(2500);
    expect(pages).toEqual(['/login']);
    expect(sockets).toHaveLength(1);
    await expect(page).toHaveURL('/login');
  });

  test('authenticates with its first message and the session cookie, not with the URL', async ({
    authenticatedPage: page,
  }) => {
    const sockets: { url: string; sent: string[]; received: string[]; closed: boolean }[] = [];
    page.on('websocket', (ws) => {
      const socket = { url: ws.url(), sent: [] as string[], received: [] as string[], closed: false };
      sockets.push(socket);
      ws.on('framesent', (frame) => socket.sent.push(String(frame.payload)));
      ws.on('framereceived', (frame) => socket.received.push(String(frame.payload)));
      ws.on('close', () => {
        socket.closed = true;
      });
    });

    await page.goto('/');
    await expect.poll(() => sockets.length).toBeGreaterThan(0);
    const socket = sockets[0];
    const url = new URL(socket.url);
    expect(url.protocol).toBe('wss:');
    expect(url.pathname).toBe('/ws/v1/events');
    expect(url.search).toBe('');

    await expect.poll(() => socket.sent.length).toBeGreaterThan(0);
    const key = await pageSessionKey(page);
    expect(key).toBeTruthy();
    expect(JSON.parse(socket.sent[0])).toEqual({ type: 'auth', key });
    expect(socket.sent[0]).not.toContain((await sessionToken(page))!);

    // auth_ok, then the problems and the repair of a share's permissions, which an administrator
    // gets (none: the stand-in runs no monitors, ADR-0332, and no repair ran, ADR-0363), then the
    // stats stream.
    await expect.poll(() => socket.received.length, { timeout: 15000 }).toBeGreaterThan(3);
    expect(JSON.parse(socket.received[0]).type).toBe('auth_ok');
    expect(JSON.parse(socket.received[1])).toEqual({ type: 'problems', data: [] });
    expect(JSON.parse(socket.received[2])).toEqual({ type: 'repair', data: null });
    expect(JSON.parse(socket.received[3]).type).toBe('stats');
    expect(socket.closed).toBe(false);
    await expect(page.getByText('WebSocket disconnected')).toHaveCount(0);
  });
});
