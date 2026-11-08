import type { Page } from '@playwright/test';

// Canned answers of the notifications API (/api/v1/notifications/*) for UI tests: the stand-in
// reaches no mail server, Telegram or webhook receiver. fakeNotificationsApi() answers every
// notifications request of the page itself, as supervisor/src/notifications/router.py does:
// channels are kept in the fake, a test is answered with the delivery that the spec chose and
// recorded in the history, which lists the newest first; unknown requests are aborted, and
// nothing reaches the supervisor.

// The texts of supervisor/src/notifications/router.py, word for word
// (supervisor/tests/test_notifications.py checks them).
export const TEST_TITLE = 'Test Notification';
export const TEST_MESSAGE = 'This is a test notification from your UNOS NAS system.';
export const sendFailed = (error: string) => `Failed to send notification: ${error}`;
export const notFound = (id: string) => `Config not found: ${id}`;
export const EVENT_TYPES = [
  { id: 'disk_warning', name: 'Disk Space Warning', description: 'When disk space is low' },
  { id: 'smart_error', name: 'SMART Error', description: 'When disk SMART reports errors' },
  { id: 'pool_degraded', name: 'Pool Degraded', description: 'When a storage pool is degraded' },
  { id: 'backup_complete', name: 'Backup Complete', description: 'When a backup/snapshot completes' },
  { id: 'backup_failed', name: 'Backup Failed', description: 'When a backup/snapshot fails' },
  { id: 'container_stopped', name: 'Container Stopped', description: 'When a container stops unexpectedly' },
  { id: 'system_boot', name: 'System Boot', description: 'When the system boots up' },
  { id: 'user_login', name: 'User Login', description: 'When a user logs in' },
];

// Errors as the senders name them, for a self-signed mail server, a chat that the bot cannot
// reach and a webhook that answers 404 with "no such hook": supervisor/tests/test_notifications.py
// compares them with the senders' own, the certificate error up to the line of Python's _ssl.c.
export const UNTRUSTED_CERTIFICATE =
  '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate (_ssl.c:1029)';
export const CHAT_NOT_FOUND = '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}';
export const NO_SUCH_HOOK = 'HTTP 404: no such hook';

/** A channel as the API sends it: its id, name, kind, and the settings of its kind. */
export type Channel = Record<string, unknown> & { id: string; name: string; channel: string; enabled: boolean };

export interface FakeNotifications {
  /** The channels, as the server keeps them. */
  channels: Channel[];
  /** The bodies of the page's POST and PUT requests for channels, in order. */
  saved: Array<{ method: string; path: string; body: Channel }>;
  /** The ids of the channels that the page sent a test to, in order. */
  tests: string[];
  /** The error that the next tests of a channel end with, by its id; none: the test is delivered. */
  errors: Record<string, string>;
  /** While set, tests are answered only once it resolves, as a mail server that is slow to answer. */
  hold: Promise<void> | null;
}

/** Serve the fake notifications API to the page, starting with ``channels``. */
export async function fakeNotificationsApi(page: Page, channels: Channel[] = []): Promise<FakeNotifications> {
  const fake: FakeNotifications = {
    channels: channels.map((c) => ({ ...c })), saved: [], tests: [], errors: {}, hold: null,
  };
  // The history, newest first.
  const history: object[] = [];
  let ids = 0;

  await page.route(
    (url) => url.pathname.startsWith('/api/v1/notifications/'),
    async (route) => {
      const request = route.request();
      const method = request.method();
      const path = new URL(request.url()).pathname.slice('/api/v1/notifications'.length);
      const missing = (id: string) => route.fulfill({ status: 404, json: { detail: notFound(id) } });

      if (method === 'GET' && path === '/configs') return route.fulfill({ json: fake.channels });
      if (method === 'GET' && path === '/logs') return route.fulfill({ json: history });
      if (method === 'GET' && path === '/events') return route.fulfill({ json: EVENT_TYPES });

      if (method === 'POST' && path === '/configs') {
        const body = request.postDataJSON() as Channel;
        fake.saved.push({ method, path, body });
        const channel = { ...body, id: `notif_e2e${++ids}` };
        fake.channels.push(channel);
        return route.fulfill({ json: channel });
      }

      const one = path.match(/^\/configs\/([^/]+)$/);
      if (one && (method === 'PUT' || method === 'DELETE')) {
        const id = decodeURIComponent(one[1]);
        const index = fake.channels.findIndex((c) => c.id === id);
        if (index < 0) return missing(id);
        if (method === 'DELETE') {
          fake.channels.splice(index, 1);
          return route.fulfill({ json: { status: 'deleted', id } });
        }
        const body = request.postDataJSON() as Channel;
        fake.saved.push({ method, path, body });
        fake.channels[index] = { ...body, id };
        return route.fulfill({ json: fake.channels[index] });
      }

      const test = method === 'POST' ? path.match(/^\/test\/([^/]+)$/) : null;
      if (test) {
        const id = decodeURIComponent(test[1]);
        const channel = fake.channels.find((c) => c.id === id);
        if (!channel) return missing(id);
        fake.tests.push(id);
        if (fake.hold) await fake.hold;
        const error = fake.errors[id] ?? null;
        const timestamp = new Date().toISOString();
        history.unshift({
          id: `log_e2e${++ids}`,
          event: { type: 'info', title: TEST_TITLE, message: TEST_MESSAGE, timestamp },
          channel: channel.channel,
          channel_name: channel.name,
          sent_at: timestamp,
          success: error === null,
          error,
        });
        if (error !== null) return route.fulfill({ status: 500, json: { detail: sendFailed(error) } });
        return route.fulfill({ json: { status: 'sent', channel: channel.channel } });
      }

      return route.abort();
    },
  );
  return fake;
}
