import { test, expect } from './fixtures';

// The stats socket authenticates with its first message. A token in the URL
// would end up in proxy and server access logs (and the server refuses it).
test.describe('Live stats WebSocket', () => {
  test('sends the token in the first message, not in the URL', async ({ authenticatedPage: page }) => {
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
    const token = await page.evaluate(() => localStorage.getItem('token'));
    expect(JSON.parse(socket.sent[0])).toEqual({ type: 'auth', token });

    // auth_ok, then the stats stream.
    await expect.poll(() => socket.received.length, { timeout: 15000 }).toBeGreaterThan(1);
    expect(JSON.parse(socket.received[0]).type).toBe('auth_ok');
    expect(JSON.parse(socket.received[1]).type).toBe('stats');
    expect(socket.closed).toBe(false);
    await expect(page.getByText('WebSocket disconnected')).toHaveCount(0);
  });
});
