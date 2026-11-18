import { test, expect, E2E_ADMIN } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import type { Page } from '@playwright/test';

// The web UI in English or Russian (feature webui.translations, ADR-0287), against the real
// supervisor: the Account page offers both languages, the choice applies at once and stays in
// this browser, also on the sign-in page. Without a choice the browser's language decides:
// playwright.config.ts runs the browser in en-US, so every other spec sees English. Values that
// the server answers as words, and dates, follow the language; messages from the server stay in
// English.

const LOCALE_KEY = 'unos-locale';

async function chooseLanguage(page: Page, label: string, language: 'English' | 'Русский') {
  await page.getByRole('combobox', { name: label }).click();
  await page.getByRole('option', { name: language }).click();
}

async function savedLanguage(page: Page): Promise<string | null> {
  return page.evaluate((key) => localStorage.getItem(key), LOCALE_KEY);
}

/** The navigation and an administrator's Account page (System > Security), in Russian. */
async function expectRussianAccountPage(page: Page) {
  await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
  const sidebar = page.locator('aside');
  await expect(sidebar.locator('nav a[href="/"]')).toHaveText('Обзор');
  await expect(sidebar.locator('nav a[href="/storage"]')).toHaveText('Хранилище');
  await expect(sidebar.locator('nav a[href="/users"]')).toHaveText('Пользователи');
  await expect(sidebar.locator('nav a[href="/system"]')).toHaveText('Система');
  await expect(sidebar.getByText('Администратор', { exact: true })).toBeVisible();
  await expect(sidebar.getByRole('button', { name: 'Выйти' })).toBeVisible();

  const main = page.locator('main');
  await expect(main.locator('nav').getByRole('button', { name: 'Безопасность' })).toBeVisible();
  await expect(main.locator('nav').getByRole('button', { name: 'Резервные копии' })).toBeVisible();
  for (const heading of [
    'Настройки безопасности',
    'Язык',
    'Двухфакторная аутентификация',
    'Смена пароля',
    'Сетевой доступ к файлам (SMB)',
    'Брандмауэр',
    'Сертификаты SSL/TLS',
    'История входов',
  ]) {
    await expect(main.getByRole('heading', { name: heading, exact: true })).toBeVisible();
  }
  await expect(main.getByLabel('Текущий пароль', { exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Сменить пароль' })).toBeVisible();
  await expect(main.getByRole('combobox', { name: 'Язык интерфейса' })).toHaveText('Русский');
  // Nothing of the English page is left.
  for (const heading of ['Security Settings', 'Two-Factor Authentication', 'Change Password', 'Login History']) {
    await expect(main.getByRole('heading', { name: heading })).toHaveCount(0);
  }
}

test.describe('Language', () => {
  test('the Account page switches the web UI to Russian at once, and the choice stays', async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/system#security');
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(page.getByRole('heading', { name: 'Security Settings' })).toBeVisible();
    expect(await savedLanguage(page)).toBeNull();
    // The login history is open while the language changes: the administrator's sign-in of this
    // test, with its time as an en-US browser writes it.
    await page.getByRole('button', { name: 'View History' }).click();
    const englishTime = page.getByRole('list', { name: 'Login history' }).locator('time').first();
    await expect(englishTime).toHaveText(/^\d{1,2}\/\d{1,2}\/\d{4}, \d{1,2}:\d{2}:\d{2}\s[AP]M$/);

    await chooseLanguage(page, 'Interface language', 'Русский');
    // At once, without loading the page again.
    await expectRussianAccountPage(page);
    expect(await savedLanguage(page)).toBe('ru');
    const history = page.getByRole('list', { name: 'История входов' });
    await expect(history.getByRole('listitem').first()).toContainText('УСПЕШНО');
    await expect(history.locator('time').first()).toHaveText(/^\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}:\d{2}$/);
    await expect(page.getByRole('button', { name: 'Скрыть' })).toBeVisible();

    await page.reload();
    await expectRussianAccountPage(page);

    // The sign-in page, after signing out and after a reload.
    await page.getByRole('button', { name: 'Выйти' }).click();
    await expect(page).toHaveURL('/login');
    for (let load = 0; load < 2; load++) {
      await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
      await expect(page.getByLabel('Имя пользователя')).toBeVisible();
      await expect(page.getByLabel('Пароль')).toBeVisible();
      await expect(page.getByRole('button', { name: 'Войти' })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Sign In' })).toHaveCount(0);
      if (load === 0) await page.reload();
    }

    // Signed in again, the Account page goes back to English.
    await page.getByLabel('Имя пользователя').fill(E2E_ADMIN.username);
    await page.getByLabel('Пароль').fill(E2E_ADMIN.password);
    await page.getByRole('button', { name: 'Войти' }).click();
    await expect(page).toHaveURL('/');
    await page.locator('aside nav a[href="/system"]').click();
    await page.locator('main nav').getByRole('button', { name: 'Безопасность' }).click();
    await chooseLanguage(page, 'Язык интерфейса', 'English');

    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(page.getByRole('heading', { name: 'Security Settings' })).toBeVisible();
    await expect(page.locator('aside nav a[href="/storage"]')).toHaveText('Storage');
    await expect(page.locator('main nav').getByRole('button', { name: 'Security' })).toBeVisible();
    expect(await savedLanguage(page)).toBe('en');
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Security Settings' })).toBeVisible();
    await expect(page.getByRole('combobox', { name: 'Interface language' })).toHaveText('English');
  });

  test.describe('the values of the firewall and the certificates', () => {
    // The dates below are in UTC.
    test.use({ timezoneId: 'UTC' });

    // As the server answers them (FirewallStatus, CertificateStatus and Certificate of
    // supervisor/src/security/schemas.py): the stand-in has no ufw and no certificate. A
    // certificate's dates are openssl's.
    const FIREWALL = {
      enabled: true,
      default_incoming: 'deny',
      default_outgoing: 'allow',
      rules: [
        { id: 'rule_1', name: 'SSH', action: 'deny', direction: 'in', protocol: 'tcp', port: '22',
          source: '192.168.1.0/24', destination: null, enabled: true, comment: null },
      ],
    };
    const VALID_UNTIL = 'Nov  8 01:47:31 2027 GMT';
    const CERTIFICATE_STATUS = { served_by: 'caddy_local_ca' };
    const CERTIFICATES = {
      certificates: [
        { id: 'custom_nas.local', type: 'self_signed', domain: 'nas.local', issuer: 'nas.local',
          valid_from: 'Nov  8 01:47:31 2026 GMT', valid_until: VALID_UNTIL, is_valid: true, served: false,
          fingerprint: null },
      ],
      served_by: 'caddy_local_ca',
    };

    test('come in the language of the page', async ({ authenticatedPage: page }) => {
      for (const [path, answer] of [
        ['/api/v1/security/firewall', FIREWALL],
        ['/api/v1/security/certificates/status', CERTIFICATE_STATUS],
        ['/api/v1/security/certificates', CERTIFICATES],
      ] as const) {
        await page.route(
          (url) => url.pathname === path,
          (route) => (route.request().method() === 'GET' ? route.fulfill({ json: answer }) : route.abort()),
        );
      }
      await page.goto('/system#security');
      const main = page.locator('main');
      await main.getByRole('button', { name: 'Configure', exact: true }).click();
      await main.getByRole('button', { name: 'Manage', exact: true }).click();
      const rule = main.locator('div.bg-surface-overlay').filter({ has: page.getByText('SSH', { exact: true }) });

      // English: the firewall's words as before the catalogs; what browsers get and a certificate's
      // type by their labels, and its dates in the browser's format, not as the server answers them.
      await expect(main.getByText('Default: Incoming deny, Outgoing allow', { exact: true })).toBeVisible();
      expect(await rule.locator('span').first().innerText()).toBe('DENY');
      await expect(rule.getByText('IN | TCP | Port: 22 | From: 192.168.1.0/24', { exact: true })).toBeVisible();
      // Text, not a regular expression: Playwright compares text with its white space collapsed,
      // the space before AM included.
      await expect(main.getByText("Caddy's local CA", { exact: true })).toBeVisible();
      await expect(main.getByText("HTTPS for browsers: Caddy's local CA", { exact: true })).toBeVisible();
      await expect(main.getByText('NOT SERVED', { exact: true })).toBeVisible();
      await expect(main.getByText('Self-signed | Issuer: nas.local | Until: 11/8/2027, 1:47:31 AM', { exact: true }))
        .toBeVisible();

      await chooseLanguage(page, 'Interface language', 'Русский');
      await expect(main.getByText('По умолчанию: входящие — запрещены, исходящие — разрешены', { exact: true }))
        .toBeVisible();
      expect(await rule.locator('span').first().innerText()).toBe('ЗАПРЕТИТЬ');
      await expect(rule.getByText('Входящие | TCP | Порт: 22 | Источник: 192.168.1.0/24', { exact: true }))
        .toBeVisible();
      await expect(main.getByText('Локальный ЦС Caddy', { exact: true })).toBeVisible();
      await expect(main.getByText('HTTPS для браузеров: Локальный ЦС Caddy', { exact: true })).toBeVisible();
      await expect(main.getByText('НЕ ОТДАЁТСЯ', { exact: true })).toBeVisible();
      await expect(main.getByText('Самоподписанный | Издатель: nas.local | До: 08.11.2027, 01:47:31', { exact: true }))
        .toBeVisible();
    });
  });

  test.describe('a regular user', () => {
    const USER = { username: `e2e_lang_${Date.now().toString(36)}`, password: 'e2e-Language-Password-1' };
    let account: Account | undefined;

    test.beforeAll(async ({ request }) => {
      account = await createAccount(request, USER);
    });

    test.afterAll(async ({ request }) => {
      await deleteAccount(request, account, USER.username);
    });

    test('chooses Russian on the Account page', async ({ page }) => {
      await page.goto('/login');
      await page.getByLabel('Username').fill(USER.username);
      await page.getByLabel('Password').fill(USER.password);
      await page.getByRole('button', { name: 'Sign In' }).click();
      await expect(page).toHaveURL('/');
      await page.locator('aside nav a[href="/system#security"]').click();

      await chooseLanguage(page, 'Interface language', 'Русский');
      for (let load = 0; load < 2; load++) {
        const sidebar = page.locator('aside');
        await expect(sidebar.locator('nav a')).toHaveText(['Обзор', 'Учётная запись']);
        await expect(sidebar.getByText('Пользователь', { exact: true })).toBeVisible();
        const main = page.locator('main');
        await expect(main.getByRole('heading', { name: 'Учётная запись', exact: true })).toBeVisible();
        await expect(main.locator('nav').getByRole('button')).toHaveText(['Безопасность']);
        await expect(main.getByRole('heading', { name: 'Настройки безопасности' })).toBeVisible();
        await expect(main.getByRole('heading', { name: 'Двухфакторная аутентификация' })).toBeVisible();
        await expect(main.getByRole('heading', { name: 'Брандмауэр' })).toHaveCount(0);
        if (load === 0) await page.reload();
      }
    });
  });

  test.describe('a browser set to Russian', () => {
    test.use({ locale: 'ru-RU' });

    test('shows Russian until another language is chosen in it', async ({ page }) => {
      await page.goto('/login');
      await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
      await expect(page.getByRole('button', { name: 'Войти' })).toBeVisible();

      // A language chosen in this browser wins over the browser's own.
      await page.evaluate((key) => localStorage.setItem(key, 'en'), LOCALE_KEY);
      await page.reload();
      await expect(page.locator('html')).toHaveAttribute('lang', 'en');
      await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible();
    });

    test('an English page in it writes the times day first, on a 24-hour clock', async ({
      authenticatedPage: page,
    }) => {
      await page.evaluate((key) => localStorage.setItem(key, 'en'), LOCALE_KEY);
      await page.goto('/system#security');
      await expect(page.locator('html')).toHaveAttribute('lang', 'en');
      await page.getByRole('button', { name: 'View History' }).click();

      // The administrator's sign-in of this test, as en-GB writes it (08/11/2026, 02:47:31), not
      // en-US (11/8/2026, 2:47:31 AM): this browser's own format is Russian, not English.
      const time = page.getByRole('list', { name: 'Login history' }).locator('time').first();
      await expect(time).toHaveText(/^\d{2}\/\d{2}\/\d{4}, \d{2}:\d{2}:\d{2}$/);
    });
  });
});
