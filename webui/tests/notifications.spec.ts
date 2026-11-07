import type { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import {
  CHAT_NOT_FOUND,
  NO_SUCH_HOOK,
  TEST_MESSAGE,
  TEST_TITLE,
  UNTRUSTED_CERTIFICATE,
  fakeNotificationsApi,
  sendFailed,
  type Channel,
} from './notifications-fakes';

// System > Notifications (system.notification-channels; ADR-0045, ADR-0260). The tab has no
// heading of its own: it starts with summary cards ("Active Channels", ...) above its Channels
// and History views. The stand-in reaches no mail server, Telegram or webhook receiver, so the
// tests of deliveries answer the page with the route fakes of notifications-fakes.ts; the last
// test sends a real one, to a mail server that is not there.

const NO_SETTINGS = { email_config: null, telegram_config: null, webhook_config: null, discord_config: null, slack_config: null };
const ADMIN_MAIL: Channel = {
  ...NO_SETTINGS,
  id: 'notif_mail',
  name: 'Admin mail',
  channel: 'email',
  enabled: true,
  events: [],
  email_config: {
    smtp_host: 'mail.example.com', smtp_port: 587, smtp_user: 'nas@example.com', smtp_password: 'mail-pass',
    smtp_tls: true, smtp_verify_tls: true, from_address: 'nas@example.com', to_addresses: ['me@example.com'],
  },
};
const HOOK_WITH_HEADERS: Channel = {
  ...NO_SETTINGS,
  id: 'notif_hook',
  name: 'Home hook',
  channel: 'webhook',
  enabled: true,
  events: ['disk_warning'],
  webhook_config: {
    url: 'https://hooks.example.com/unos', method: 'POST', headers: { 'X-Hook-Key': 'k1' },
    body_template: '{title}: {message}', content_type: 'text/plain',
  },
};
// A method given to the API in lower case is stored so, and the server sends a PUT for it.
const LOWER_CASE_PUT: Channel = {
  ...NO_SETTINGS,
  id: 'notif_put',
  name: 'Put hook',
  channel: 'webhook',
  enabled: true,
  events: [],
  webhook_config: {
    url: 'https://hooks.example.com/put', method: 'put', headers: {}, body_template: null,
    content_type: 'application/json',
  },
};

async function openTab(page: Page) {
  await page.goto('/system#notifications');
  await expect(page.getByText('Active Channels', { exact: true })).toBeVisible();
}

/** The field of ``scope`` labelled ``label``, with the mark of a required field or without. */
function field(scope: Locator, label: string): Locator {
  return scope.getByLabel(new RegExp(`^\\s*${label}\\s*(\\*\\s*)?$`));
}

function channelItem(page: Page, name: string): Locator {
  return page.getByRole('list', { name: 'Notification channels' }).getByRole('listitem').filter({ hasText: name });
}

function historyItems(page: Page): Locator {
  return page.getByRole('list', { name: 'Notifications sent' }).getByRole('listitem');
}

/** Open the dialog for a new channel of ``kind``, fill in ``fields`` (label: value) and add it. */
async function addChannel(page: Page, kind: string, fields: Record<string, string>) {
  await page.getByRole('button', { name: `Add ${kind}` }).click();
  const dialog = page.getByRole('dialog', { name: `Add ${kind} channel` });
  await expect(dialog).toBeVisible();
  for (const [label, value] of Object.entries(fields)) await field(dialog, label).fill(value);
  await dialog.getByRole('button', { name: 'Add Channel' }).click();
  await expect(dialog).toBeHidden();
}

/** The number of a summary card: the line above its label. */
function stat(page: Page, label: string): Locator {
  return page.getByText(label, { exact: true }).locator('xpath=preceding-sibling::p');
}

test.describe('Notifications', () => {
  test('shows the summary, every kind of channel to add, and the history with its filters', async ({
    authenticatedPage: page,
  }) => {
    await fakeNotificationsApi(page);
    await openTab(page);

    for (const label of ['Active Channels', 'Total Sent', 'Delivered', 'Failed']) {
      await expect(stat(page, label)).toHaveText('0');
    }
    await expect(page.getByRole('button', { name: 'Channels', exact: true })).toHaveAttribute('aria-pressed', 'true');
    for (const kind of ['Email', 'Telegram', 'Webhook', 'Discord', 'Slack']) {
      await expect(page.getByRole('button', { name: `Add ${kind}` })).toBeVisible();
    }
    await expect(page.getByText('No notification channels')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Refresh' })).toBeVisible();

    await page.getByRole('button', { name: 'History', exact: true }).click();
    await expect(page.getByRole('button', { name: 'History', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('combobox', { name: 'Channel' })).toHaveText('All channels');
    await expect(page.getByRole('button', { name: 'All', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('button', { name: 'Delivered', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Failed', exact: true })).toBeVisible();
    await expect(page.getByText('No notifications yet')).toBeVisible();
  });

  test('an e-mail channel checks the mail server certificate unless it allows a self-signed one', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeNotificationsApi(page);
    await openTab(page);

    await page.getByRole('button', { name: 'Add Email' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add Email channel' });
    await expect(dialog.getByText('Leave both empty for a mail server that takes mail without a login')).toBeVisible();
    await expect(dialog.getByRole('checkbox', { name: "Verify the server's certificate" })).toBeChecked();
    await expect(dialog.getByRole('checkbox', { name: 'Use TLS/STARTTLS' })).toBeChecked();
    await expect(field(dialog, 'Port')).toHaveValue('587');
    // Without TLS there is no certificate to check, and the page says what that means.
    const plain = dialog.getByText('Without TLS, the password and the messages cross the network unencrypted');
    await expect(plain).toBeHidden();
    await dialog.getByRole('checkbox', { name: 'Use TLS/STARTTLS' }).uncheck();
    await expect(plain).toBeVisible();
    await expect(dialog.getByRole('checkbox', { name: "Verify the server's certificate" })).toBeDisabled();
    await dialog.getByRole('checkbox', { name: 'Use TLS/STARTTLS' }).check();
    await expect(plain).toBeHidden();
    await expect(dialog.getByRole('checkbox', { name: "Verify the server's certificate" })).toBeEnabled();
    // The mail server, the sender and a recipient are needed.
    await expect(dialog.getByRole('button', { name: 'Add Channel' })).toBeDisabled();
    await field(dialog, 'Channel name').fill('Admin mail');
    await field(dialog, 'SMTP host').fill('mail.example.com');
    await field(dialog, 'Username').fill('nas@example.com');
    await field(dialog, 'Password').fill('mail-pass');
    await field(dialog, 'From address').fill('nas@example.com');
    await field(dialog, 'To addresses').fill('me@example.com, you@example.com');
    await dialog.getByRole('button', { name: 'Add Channel' }).click();
    await expect(dialog).toBeHidden();

    expect(fake.saved[0]).toEqual({
      method: 'POST',
      path: '/configs',
      body: {
        name: 'Admin mail', channel: 'email', enabled: true, events: [],
        email_config: {
          smtp_host: 'mail.example.com', smtp_port: 587, smtp_user: 'nas@example.com', smtp_password: 'mail-pass',
          smtp_tls: true, smtp_verify_tls: true, from_address: 'nas@example.com',
          to_addresses: ['me@example.com', 'you@example.com'],
        },
      },
    });
    await expect(channelItem(page, 'Admin mail')).toContainText('me@example.com, you@example.com via mail.example.com:587');

    // A mail server with a self-signed certificate, on the SMTPS port.
    await page.getByRole('button', { name: 'Add Email' }).click();
    await field(dialog, 'Channel name').fill('Self-signed mail');
    await field(dialog, 'SMTP host').fill('mail.lan');
    await field(dialog, 'Port').fill('465');
    await field(dialog, 'From address').fill('nas@example.com');
    await field(dialog, 'To addresses').fill('me@example.com');
    await dialog.getByRole('checkbox', { name: "Verify the server's certificate" }).uncheck();
    await dialog.getByRole('button', { name: 'Add Channel' }).click();
    await expect(dialog).toBeHidden();
    // Without a username and password: the server sends without a login.
    expect(fake.saved[1].body.email_config).toMatchObject({
      smtp_host: 'mail.lan', smtp_port: 465, smtp_verify_tls: false, smtp_user: '', smtp_password: '',
    });

    // The dialog shows the stored choice again.
    await channelItem(page, 'Self-signed mail').getByRole('button', { name: 'Edit Self-signed mail' }).click();
    const edit = page.getByRole('dialog', { name: 'Edit Email channel' });
    await expect(edit.getByRole('checkbox', { name: "Verify the server's certificate" })).not.toBeChecked();
    await expect(field(edit, 'Port')).toHaveValue('465');
  });

  test('a test reports next to its channel whether it was sent or why not, and History lists it', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeNotificationsApi(page, [ADMIN_MAIL]);
    await openTab(page);
    const channel = channelItem(page, 'Admin mail');

    await channel.getByRole('button', { name: 'Send test to Admin mail' }).click();
    await expect(channel.getByRole('status')).toHaveText('Test notification sent');

    fake.errors[ADMIN_MAIL.id] = UNTRUSTED_CERTIFICATE;
    await channel.getByRole('button', { name: 'Send test to Admin mail' }).click();
    await expect(channel.getByRole('alert')).toContainText('Test notification not sent');
    await expect(channel.getByRole('alert')).toContainText(sendFailed(UNTRUSTED_CERTIFICATE));
    await expect(channel.getByRole('status')).toHaveCount(0);
    expect(fake.tests).toEqual([ADMIN_MAIL.id, ADMIN_MAIL.id]);
    await expect(stat(page, 'Total Sent')).toHaveText('2');
    await expect(stat(page, 'Delivered')).toHaveText('1');
    await expect(stat(page, 'Failed')).toHaveText('1');

    // Newest first, without a reload.
    await page.getByRole('button', { name: 'History', exact: true }).click();
    const [failed, delivered] = [historyItems(page).nth(0), historyItems(page).nth(1)];
    await expect(historyItems(page)).toHaveCount(2);
    for (const entry of [failed, delivered]) {
      await expect(entry).toContainText(TEST_TITLE);
      await expect(entry).toContainText(TEST_MESSAGE);
      await expect(entry).toContainText('Admin mail');
    }
    await expect(failed).toContainText('Failed');
    await expect(delivered).toContainText('Delivered');
    await failed.getByRole('button').click();
    await expect(failed.getByRole('button')).toHaveAttribute('aria-expanded', 'true');
    await expect(failed).toContainText(UNTRUSTED_CERTIFICATE);

    // The filters by result, each with the one entry it keeps.
    await page.getByRole('button', { name: 'Failed', exact: true }).click();
    await expect(historyItems(page)).toHaveCount(1);
    await expect(historyItems(page).first()).toContainText(UNTRUSTED_CERTIFICATE);
    await page.getByRole('button', { name: 'Delivered', exact: true }).click();
    await expect(historyItems(page)).toHaveCount(1);
    await expect(historyItems(page).first()).toContainText('Delivered');
    await expect(historyItems(page).first()).not.toContainText('Failed');
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await expect(historyItems(page)).toHaveCount(2);
    // The filter by the kind of channel: e-mail keeps both, Telegram none.
    await page.getByRole('combobox', { name: 'Channel' }).click();
    await page.getByRole('option', { name: 'Email' }).click();
    await expect(historyItems(page)).toHaveCount(2);
    for (const entry of [historyItems(page).nth(0), historyItems(page).nth(1)]) {
      await expect(entry).toContainText('Admin mail');
    }
    await page.getByRole('combobox', { name: 'Channel' }).click();
    await page.getByRole('option', { name: 'Telegram' }).click();
    await expect(page.getByText('No notifications match your filters')).toBeVisible();
  });

  test('while a test runs, its channel cannot be edited, so no outcome shows under settings it was not sent with', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeNotificationsApi(page, [ADMIN_MAIL, HOOK_WITH_HEADERS]);
    let answer = () => {};
    fake.hold = new Promise((resolve) => (answer = resolve));
    await openTab(page);
    const channel = channelItem(page, 'Admin mail');

    await channel.getByRole('button', { name: 'Send test to Admin mail' }).click();
    await expect(channel.getByRole('button', { name: 'Send test to Admin mail' })).toBeDisabled();
    await expect(channel.getByRole('button', { name: 'Edit Admin mail' })).toBeDisabled();
    await expect(channel.getByRole('button', { name: 'Disable Admin mail' })).toBeDisabled();
    // The other channels can be changed meanwhile; their tests wait for this one.
    const other = channelItem(page, 'Home hook');
    await expect(other.getByRole('button', { name: 'Edit Home hook' })).toBeEnabled();
    await expect(other.getByRole('button', { name: 'Send test to Home hook' })).toBeDisabled();

    answer();
    await expect(channel.getByRole('status')).toHaveText('Test notification sent');
    await expect(channel.getByRole('button', { name: 'Edit Admin mail' })).toBeEnabled();
    await expect(channel.getByRole('button', { name: 'Disable Admin mail' })).toBeEnabled();
    expect(fake.tests).toEqual([ADMIN_MAIL.id]);
  });

  test('a disabled channel can be tested before it receives notifications', async ({ authenticatedPage: page }) => {
    const fake = await fakeNotificationsApi(page, [{ ...ADMIN_MAIL, enabled: false }]);
    await openTab(page);
    const channel = channelItem(page, 'Admin mail');
    await expect(channel).toContainText('Disabled');
    await expect(stat(page, 'Active Channels')).toHaveText('0');

    await channel.getByRole('button', { name: 'Send test to Admin mail' }).click();
    await expect(channel.getByRole('status')).toHaveText('Test notification sent');
    expect(fake.tests).toEqual([ADMIN_MAIL.id]);

    await channel.getByRole('button', { name: 'Enable Admin mail' }).click();
    await expect(stat(page, 'Active Channels')).toHaveText('1');
    expect(fake.saved).toEqual([
      { method: 'PUT', path: `/configs/${ADMIN_MAIL.id}`, body: { ...ADMIN_MAIL, enabled: true } },
    ]);
  });

  test('Telegram, webhook, Discord and Slack channels are added and tested the same way', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeNotificationsApi(page);
    await openTab(page);

    await addChannel(page, 'Telegram', { 'Channel name': 'Phone', 'Bot token': '123456:ABC-DEF', 'Chat ID': '42' });
    await page.getByRole('button', { name: 'Add Webhook' }).click();
    const hookDialog = page.getByRole('dialog', { name: 'Add Webhook channel' });
    await field(hookDialog, 'Channel name').fill('Home hook');
    await field(hookDialog, 'URL').fill('https://hooks.example.com/unos?key=k1');
    await hookDialog.getByRole('combobox', { name: 'Method' }).click();
    await hookDialog.getByRole('option', { name: 'PUT' }).click();
    await hookDialog.getByRole('button', { name: 'Add Channel' }).click();
    await expect(hookDialog).toBeHidden();
    await addChannel(page, 'Discord', { 'Channel name': 'Discord', 'Webhook URL': 'https://discord.com/api/webhooks/1/abc' });
    await addChannel(page, 'Slack', { 'Channel name': 'Team', 'Webhook URL': 'https://hooks.slack.com/services/T1/B1/xyz' });

    expect(fake.saved.map((s) => s.body)).toEqual([
      { name: 'Phone', channel: 'telegram', enabled: true, events: [],
        telegram_config: { bot_token: '123456:ABC-DEF', chat_id: '42' } },
      { name: 'Home hook', channel: 'webhook', enabled: true, events: [],
        webhook_config: { url: 'https://hooks.example.com/unos?key=k1', method: 'PUT' } },
      { name: 'Discord', channel: 'discord', enabled: true, events: [],
        discord_config: { webhook_url: 'https://discord.com/api/webhooks/1/abc' } },
      { name: 'Team', channel: 'slack', enabled: true, events: [],
        slack_config: { webhook_url: 'https://hooks.slack.com/services/T1/B1/xyz' } },
    ]);
    // A webhook URL carries its secret: the list shows only the host.
    await expect(channelItem(page, 'Phone')).toContainText('Chat ID 42');
    await expect(channelItem(page, 'Home hook')).toContainText('PUT to hooks.example.com');
    await expect(channelItem(page, 'Discord')).toContainText('Webhook at discord.com');
    await expect(channelItem(page, 'Team')).toContainText('Webhook at hooks.slack.com');
    await expect(page.getByRole('list', { name: 'Notification channels' })).not.toContainText('key=k1');

    const [phone, , , team] = fake.channels;
    fake.errors[phone.id] = CHAT_NOT_FOUND;
    fake.errors[team.id] = NO_SUCH_HOOK;
    for (const name of ['Phone', 'Home hook', 'Discord', 'Team']) {
      await channelItem(page, name).getByRole('button', { name: `Send test to ${name}` }).click();
      await expect(channelItem(page, name).getByRole(name === 'Phone' || name === 'Team' ? 'alert' : 'status')).toBeVisible();
    }
    await expect(channelItem(page, 'Phone').getByRole('alert')).toContainText(sendFailed(CHAT_NOT_FOUND));
    await expect(channelItem(page, 'Home hook').getByRole('status')).toHaveText('Test notification sent');
    await expect(channelItem(page, 'Discord').getByRole('status')).toHaveText('Test notification sent');
    await expect(channelItem(page, 'Team').getByRole('alert')).toContainText(sendFailed(NO_SUCH_HOOK));

    await page.getByRole('button', { name: 'History', exact: true }).click();
    await expect(historyItems(page)).toHaveCount(4);
    const expected = [
      ['Team', 'Slack', 'Failed'],
      ['Discord', 'Discord', 'Delivered'],
      ['Home hook', 'Webhook', 'Delivered'],
      ['Phone', 'Telegram', 'Failed'],
    ];
    for (const [index, texts] of expected.entries()) {
      for (const text of texts) await expect(historyItems(page).nth(index)).toContainText(text);
    }
  });

  test('editing a channel keeps the settings that only the API sets', async ({ authenticatedPage: page }) => {
    const fake = await fakeNotificationsApi(page, [HOOK_WITH_HEADERS, LOWER_CASE_PUT]);
    await openTab(page);

    await channelItem(page, 'Home hook').getByRole('button', { name: 'Edit Home hook' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit Webhook channel' });
    await expect(field(dialog, 'URL')).toHaveValue('https://hooks.example.com/unos');
    await field(dialog, 'URL').fill('https://hooks.example.com/unos2');
    await dialog.getByRole('button', { name: 'Save Changes' }).click();
    await expect(dialog).toBeHidden();

    expect(fake.saved).toEqual([{
      method: 'PUT',
      path: '/configs/notif_hook',
      body: {
        name: 'Home hook', channel: 'webhook', enabled: true, events: ['disk_warning'],
        webhook_config: {
          url: 'https://hooks.example.com/unos2', method: 'POST', headers: { 'X-Hook-Key': 'k1' },
          body_template: '{title}: {message}', content_type: 'text/plain',
        },
      },
    }]);

    // A method stored in lower case shows as the method it is, and a save keeps it.
    await channelItem(page, 'Put hook').getByRole('button', { name: 'Edit Put hook' }).click();
    const put = page.getByRole('dialog', { name: 'Edit Webhook channel' });
    await expect(put.getByRole('combobox', { name: 'Method' })).toHaveText('PUT');
    await field(put, 'Channel name').fill('Upload hook');
    await put.getByRole('button', { name: 'Save Changes' }).click();
    await expect(put).toBeHidden();
    expect(fake.saved[1].body).toEqual({
      name: 'Upload hook', channel: 'webhook', enabled: true, events: [],
      webhook_config: { ...LOWER_CASE_PUT.webhook_config as object, method: 'PUT' },
    });
  });

  test('on the stand-in itself, a test to a mail server that is not there names the error in the list and History', async ({
    authenticatedPage: page,
  }) => {
    // No fakes: the supervisor stores the channel, tries the mail server and records the attempt.
    // Nothing listens on port 1 of the supervisor's own loopback, on every local target.
    const name = 'E2E unreachable mail';
    await openTab(page);
    await addChannel(page, 'Email', {
      'Channel name': name, 'SMTP host': '127.0.0.1', Port: '1', 'From address': 'nas@example.com',
      'To addresses': 'me@example.com',
    });
    const channel = channelItem(page, name);
    await expect(channel).toContainText('me@example.com via 127.0.0.1:1');

    await channel.getByRole('button', { name: `Send test to ${name}` }).click();
    await expect(channel.getByRole('alert')).toContainText(/Failed to send notification: .*Connection refused/);

    await page.getByRole('button', { name: 'History', exact: true }).click();
    const newest = historyItems(page).nth(0);
    await expect(newest).toContainText(name);
    await expect(newest).toContainText('Failed');
    await newest.getByRole('button').click();
    await expect(newest).toContainText(/Connection refused/);

    // The server kept the certificate check on, and the channel goes again.
    await page.getByRole('button', { name: 'Channels', exact: true }).click();
    await channel.getByRole('button', { name: `Edit ${name}` }).click();
    const edit = page.getByRole('dialog', { name: 'Edit Email channel' });
    await expect(edit.getByRole('checkbox', { name: "Verify the server's certificate" })).toBeChecked();
    await edit.getByRole('button', { name: 'Cancel' }).click();
    await channel.getByRole('button', { name: `Delete ${name}` }).click();
    const confirm = page.getByRole('dialog', { name: 'Delete the channel?' });
    await confirm.getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(confirm).toBeHidden();
    await expect(channel).toHaveCount(0);
  });
});
