import { test, expect } from '@playwright/test';

// Caddy (webui/Caddyfile): the UI and API only over HTTPS with security
// headers; plain HTTP redirects, except the health check used by install.sh,
// update.sh and monitors.

/**
 * The plain-HTTP address of the same Caddy. Set HTTP_URL when port 80 is
 * remapped (docker-compose.test.yml publishes 3080:80); otherwise it is the
 * base URL's host on port 80, which only works when the base URL uses 443.
 */
function plainHttpUrl(baseURL: string): string | null {
  if (process.env.HTTP_URL) return process.env.HTTP_URL.replace(/\/$/, '');
  const base = new URL(baseURL);
  return base.protocol === 'https:' && base.port === '' ? `http://${base.hostname}` : null;
}

test.describe('HTTPS and security headers', () => {
  test('the UI is served over HTTPS with security headers', async ({ request, baseURL }) => {
    expect(new URL(baseURL!).protocol).toBe('https:');
    const response = await request.get('/');
    expect(response.status()).toBe(200);

    const headers = response.headers();
    const csp = headers['content-security-policy'] ?? '';
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(headers['x-frame-options']).toBe('DENY');
    expect(headers['x-content-type-options']).toBe('nosniff');
    expect(headers['referrer-policy']).toBe('no-referrer');
    // Deliberately absent: with HSTS, browsers refuse to let users past the
    // warning for the local CA certificate (see the Caddyfile).
    expect(headers['strict-transport-security']).toBeUndefined();

    // SvelteKit's hash-based policy for the inline bootstrap script.
    const html = await response.text();
    expect(html).toMatch(/<meta http-equiv="content-security-policy" content="[^"]*script-src 'self' 'sha256-/);
  });

  test('API responses carry the same headers', async ({ request }) => {
    const response = await request.get('/api/v1/health');
    expect(response.ok()).toBeTruthy();
    expect(response.headers()['x-frame-options']).toBe('DENY');
    expect(response.headers()['content-security-policy']).toContain("frame-ancestors 'none'");
  });

  test('plain HTTP redirects to HTTPS', async ({ request, baseURL }) => {
    const httpUrl = plainHttpUrl(baseURL!);
    test.skip(!httpUrl, 'set HTTP_URL to the plain-HTTP address');
    const response = await request.get(`${httpUrl}/login?next=1`, { maxRedirects: 0 });
    expect(response.status()).toBe(308);
    expect(response.headers()['location']).toBe(`https://${new URL(httpUrl!).hostname}/login?next=1`);
  });

  test('the health check stays reachable over plain HTTP', async ({ request, baseURL }) => {
    const httpUrl = plainHttpUrl(baseURL!);
    test.skip(!httpUrl, 'set HTTP_URL to the plain-HTTP address');
    const response = await request.get(`${httpUrl}/api/v1/health`, { maxRedirects: 0 });
    expect(response.status()).toBe(200);
    expect(await response.json()).toEqual({ status: 'ok' });
  });
});
