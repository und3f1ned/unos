import { test, expect, E2E_ADMIN } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import type { Page } from '@playwright/test';

// The web UI in English or Russian (feature webui.translations, ADR-0273), against the real
// supervisor: the Account page offers both languages, the choice applies at once and stays in
// this browser, also on the sign-in page. Without a choice the browser's language decides:
// playwright.config.ts runs the browser in en-US, so every other spec sees English. Messages
// from the server stay in English.

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
  });
});
