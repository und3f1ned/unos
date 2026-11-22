import type { Browser, BrowserContext, Page } from '@playwright/test';
import { test, expect, E2E_ADMIN } from './fixtures';
import { apiLogin, createAccount, deleteAccount, type Account } from './accounts';
import { fakeStorageApi } from './storage-fakes';
import { fakeSmbApi } from './shares-fakes';
import { fakeHealthApi } from './health-fakes';

// webui.light-theme (ADR-0405): the Account page offers Dark, Light and System; the server keeps
// each user's choice, so it stays across sign-ins and follows the user to another browser, and
// every page takes the colours of the chosen theme's tokens. System follows the device's
// prefers-color-scheme as it changes. The theme is on <html> before the page is drawn, from the
// last choice of the browser, which the sign-in and first-run pages follow too. The specs that
// choose a theme use an account of their own; the walk through the pages uses the
// administrator's System and the device's colour scheme, so that the administrator's theme stays
// System, as the other specs expect it.

const THEME_API = '/api/v1/auth/me/theme';
const THEME_KEY = 'unos-theme';
const USER = { username: 'e2e-theme-user', password: 'Theme-pass-2026' };
type Theme = 'dark' | 'light';
type Choice = Theme | 'system';

const html = (page: Page) => page.locator('html');
const option = (page: Page, name: string) => page.getByRole('radio', { name, exact: true });

async function expectTheme(page: Page, theme: Theme) {
  await expect(html(page)).toHaveAttribute('data-theme', theme);
}

/** The red, green and blue of each colour token of `theme`, as app.css defines them: 'r g b'. */
async function tokens(page: Page, theme: Theme): Promise<Record<string, string>> {
  return page.evaluate((theme) => {
    const names = new Set<string>();
    // The rules of a sheet, also those inside @layer blocks (Tailwind CSS 4 puts app.css's base styles
    // in its layer base, ADR-0450), @media, @supports and nested rules.
    const rules = (list: CSSRuleList): CSSRule[] =>
      [...list].flatMap((rule) => [
        ...(rule instanceof CSSStyleRule || !(rule instanceof CSSGroupingRule) ? [rule] : []),
        ...(rule instanceof CSSGroupingRule ? rules(rule.cssRules) : []),
      ]);
    for (const sheet of [...document.styleSheets]) {
      for (const rule of rules(sheet.cssRules)) {
        if (!(rule instanceof CSSStyleRule) || !rule.selectorText.includes('data-theme')) continue;
        for (const property of [...rule.style]) if (property.startsWith('--color-')) names.add(property.slice(8));
      }
    }
    const probe = document.createElement('div');
    probe.setAttribute('data-theme', theme);
    document.body.append(probe);
    const style = getComputedStyle(probe);
    const values = Object.fromEntries(
      [...names].map((name) => [name, style.getPropertyValue(`--color-${name}`).trim().split(/\s+/).join(' ')]),
    );
    probe.remove();
    return values;
  }, theme);
}

/**
 * The red, green, blue and opacity of a computed colour: 'rgba(1, 2, 3, 0.5)', or 'oklab(L a b / 0.5)',
 * in which the browser writes a token with an opacity of Tailwind CSS 4 (bg-brand/20 is a color-mix in
 * oklab, ADR-0450); the conversion gives back the token's red, green and blue exactly.
 */
function rgba(css: string): { rgb: string; alpha: number } | null {
  const rgb = css.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/);
  if (rgb) return { rgb: `${rgb[1]} ${rgb[2]} ${rgb[3]}`, alpha: rgb[4] === undefined ? 1 : Number(rgb[4]) };
  const lab = css.match(/^oklab\((\S+) (\S+) (\S+)(?: \/ ([\d.]+))?\)$/);
  if (!lab) return null;
  const [L, a, b] = [Number(lab[1]), Number(lab[2]), Number(lab[3])];
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const linear = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  const srgb = linear.map((x) => {
    const v = x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055;
    return Math.round(255 * Math.min(1, Math.max(0, v)));
  });
  return { rgb: srgb.join(' '), alpha: lab[4] === undefined ? 1 : Number(lab[4]) };
}

/** 'r g b' of a computed colour such as 'rgba(1, 2, 3, 0.5)'. */
function channels(css: string): string {
  return rgba(css)?.rgb ?? css;
}

// The tokens that are never a background, never text, never a border: the text colours of one
// theme can be the backgrounds of the other (light content is dark surface).
const TEXT_ONLY = ['content', 'content-soft', 'content-secondary', 'content-muted', 'content-inverse'];
const BACKGROUND_ONLY = ['surface', 'surface-elevated', 'surface-overlay', 'surface-hover', 'surface-muted', 'scrim'];
const NOT_BACKGROUND = TEXT_ONLY;
const NOT_TEXT = [...BACKGROUND_ONLY, 'border', 'border-strong', 'border-muted'];
// A border may be content-muted (a Select under the pointer); one in currentColor counts as text.
const NOT_BORDER = [...BACKGROUND_ONLY, ...TEXT_ONLY.filter((name) => name !== 'content-muted')];

/**
 * Every colour of the page that is not a token of the theme it shows, for what it colours: the
 * text, the background and the borders of each element (a colour with an opacity counts by its red,
 * green and blue; transparent ones and borders that are not drawn are left out).
 */
async function foreignColours(page: Page): Promise<string[]> {
  const theme = (await html(page).getAttribute('data-theme')) as Theme;
  const values = await tokens(page, theme);
  const allowed = (excluded: string[]) =>
    Object.entries(values).filter(([name]) => !excluded.includes(name)).map(([, value]) => value);
  const sets = { text: allowed(NOT_TEXT), background: allowed(NOT_BACKGROUND), border: allowed(NOT_BORDER) };
  const known = { text: new Set(sets.text), background: new Set(sets.background), border: new Set(sets.border) };
  const colours = await page.evaluate(() => {
    // A colour on its way to another (transition-colors, as a tab that was selected) counts as
    // where it goes.
    for (const animation of document.getAnimations()) {
      if (animation instanceof CSSTransition) animation.finish();
    }
    // Each colour of each kind once, with the first element that has it.
    const found = new Map<string, [keyof typeof known, string, string]>();
    for (const element of document.body.querySelectorAll('*')) {
      // The browser draws these itself (their accent colour is the theme's).
      if (element instanceof HTMLInputElement && ['checkbox', 'radio', 'range', 'file'].includes(element.type)) continue;
      const style = getComputedStyle(element);
      const colours: [keyof typeof known, string, string][] = [
        ['text', 'color', style.color],
        ['background', 'background-color', style.backgroundColor],
      ];
      for (const side of ['top', 'right', 'bottom', 'left']) {
        const width = style.getPropertyValue(`border-${side}-width`);
        if (width !== '0px' && style.getPropertyValue(`border-${side}-style`) !== 'none') {
          const value = style.getPropertyValue(`border-${side}-color`);
          colours.push([value === style.color ? 'text' : 'border', `border-${side}-color`, value]);
        }
      }
      for (const [kind, property, value] of colours) {
        if (found.has(`${kind} ${value}`)) continue;
        const words = (element.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40);
        found.set(`${kind} ${value}`, [kind, value, `<${element.tagName.toLowerCase()} class="${element.getAttribute('class') ?? ''}"> ${property} ${value} ${words}`]);
      }
    }
    return [...found.values()];
  });
  return colours
    .filter(([kind, value]) => {
      const colour = rgba(value);
      return colour !== null && colour.alpha !== 0 && !known[kind].has(colour.rgb);
    })
    .map(([, , where]) => where)
    .slice(0, 20);
}

async function background(page: Page, selector: string): Promise<string> {
  return channels(await page.locator(selector).first().evaluate((element) => getComputedStyle(element).backgroundColor));
}

async function colour(page: Page, selector: string): Promise<string> {
  return channels(await page.locator(selector).first().evaluate((element) => getComputedStyle(element).color));
}

/** The relative luminance of 'r g b' (WCAG). */
function luminance(rgb: string): number {
  const [r, g, b] = rgb.split(' ').map((value) => {
    const c = Number(value) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

async function storedChoice(page: Page, who = USER): Promise<Choice> {
  const token = await apiLogin(page.request, who);
  const response = await page.request.get(THEME_API, { headers: { Authorization: `Bearer ${token}` } });
  return (await response.json()).theme;
}

async function storeChoice(page: Page, theme: Choice, who = USER): Promise<void> {
  const token = await apiLogin(page.request, who);
  const response = await page.request.put(THEME_API, { data: { theme }, headers: { Authorization: `Bearer ${token}` } });
  expect(response.ok()).toBeTruthy();
}

async function signIn(page: Page, who = USER) {
  await page.goto('/login');
  await page.getByLabel('Username').fill(who.username);
  await page.getByLabel('Password').fill(who.password);
  await page.getByRole('button', { name: 'Sign In' }).click();
  await expect(page).toHaveURL('/');
}

/** A new browser, with nothing kept from another, whose device prefers `scheme`. */
async function newBrowser(browser: Browser, scheme: Theme): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ colorScheme: scheme });
  return { context, page: await context.newPage() };
}

async function openAccount(page: Page) {
  await page.goto('/account#appearance');
  await expect(page.getByRole('group', { name: 'Colour theme' })).toBeVisible();
}

test.describe('The theme of a user', () => {
  let account: Account | undefined;

  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });
  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });
  test.beforeEach(async ({ page }) => {
    await storeChoice(page, 'system');
  });

  // Step 1: the Account page offers Dark, Light and System.
  test('the Account page offers Dark, Light and System, and System until the user chooses', async ({ browser }) => {
    const { context, page } = await newBrowser(browser, 'dark');
    try {
      await signIn(page);
      await openAccount(page);
      const group = page.getByRole('group', { name: 'Colour theme' });
      await expect(group.getByRole('radio')).toHaveCount(3);
      await expect(option(page, 'Dark')).not.toBeChecked();
      await expect(option(page, 'Light')).not.toBeChecked();
      await expect(option(page, 'System')).toBeChecked();
      await expect(page.getByText('System follows the light or dark setting of your device.', { exact: false })).toBeVisible();
      await expectTheme(page, 'dark');
    } finally {
      await context.close();
    }
  });

  // Step 2: the choice stays across sign-ins, and in another browser.
  test('Light applies at once and stays across sign-ins and in another browser', async ({ browser }) => {
    const first = await newBrowser(browser, 'dark');
    try {
      await signIn(first.page);
      await openAccount(first.page);
      await expectTheme(first.page, 'dark');
      const saved = first.page.waitForResponse((r) => r.url().endsWith(THEME_API) && r.request().method() === 'PUT');
      await option(first.page, 'Light').check();
      expect((await saved).status()).toBe(200);
      await expectTheme(first.page, 'light');
      await expect(option(first.page, 'Light')).toBeChecked();
      expect(await storedChoice(first.page)).toBe('light');

      // Sign out: the sign-in page keeps the theme of this browser.
      await first.page.getByRole('button', { name: 'Logout' }).click();
      await expect(first.page).toHaveURL('/login');
      await expectTheme(first.page, 'light');
      await first.page.reload();
      await expectTheme(first.page, 'light');

      // Sign in again: still Light.
      await signIn(first.page);
      await expectTheme(first.page, 'light');
      await openAccount(first.page);
      await expect(option(first.page, 'Light')).toBeChecked();
    } finally {
      await first.context.close();
    }

    // Another browser, nothing kept in it, on a device that prefers dark: the sign-in page follows
    // the device, and the user's Light comes with the sign-in.
    const second = await newBrowser(browser, 'dark');
    try {
      await second.page.goto('/login');
      await expectTheme(second.page, 'dark');
      await signIn(second.page);
      await expectTheme(second.page, 'light');
      await openAccount(second.page);
      await expect(option(second.page, 'Light')).toBeChecked();
    } finally {
      await second.context.close();
    }
  });

  test('System follows the colour scheme of the device as it changes, Dark and Light do not', async ({ browser }) => {
    const { context, page } = await newBrowser(browser, 'dark');
    try {
      await signIn(page);
      await openAccount(page);
      await expectTheme(page, 'dark');
      await page.emulateMedia({ colorScheme: 'light' });
      await expectTheme(page, 'light');
      await page.emulateMedia({ colorScheme: 'dark' });
      await expectTheme(page, 'dark');

      await option(page, 'Light').check();
      await expectTheme(page, 'light');
      await page.emulateMedia({ colorScheme: 'dark' });
      await expectTheme(page, 'light');

      await option(page, 'Dark').check();
      await expectTheme(page, 'dark');
      await page.emulateMedia({ colorScheme: 'light' });
      await expectTheme(page, 'dark');
      await expect.poll(() => storedChoice(page)).toBe('dark');

      await option(page, 'System').check();
      await expectTheme(page, 'light');
      await expect.poll(() => storedChoice(page)).toBe('system');
    } finally {
      await context.close();
    }
  });

  test('the theme is on the page before it is drawn, from the last choice of the browser', async ({ browser }) => {
    const { context, page } = await newBrowser(browser, 'light');
    try {
      // The theme of <html> when <body> comes: before anything of the page is drawn.
      await context.addInitScript(() => {
        new MutationObserver((_, observer) => {
          if (!document.body) return;
          const root = document.documentElement;
          // The theme, and the scheme of the canvas that the browser paints before the style sheet.
          (window as unknown as { themeAtBody: string }).themeAtBody =
            `${root.getAttribute('data-theme')} ${getComputedStyle(root).colorScheme}`;
          observer.disconnect();
        }).observe(document, { childList: true, subtree: true });
      });
      const themeAtBody = () => page.evaluate(() => (window as unknown as { themeAtBody: string }).themeAtBody);

      // Nothing kept: System, so the device's.
      await page.goto('/login');
      expect(await themeAtBody()).toBe('light light');

      // A choice kept in the browser wins over the device, before any sign-in.
      await page.evaluate((key) => localStorage.setItem(key, 'dark'), THEME_KEY);
      await page.reload();
      expect(await themeAtBody()).toBe('dark dark');
      await expectTheme(page, 'dark');

      // Signed in, the page opens in the user's theme, as the browser keeps it.
      await storeChoice(page, 'dark');
      await signIn(page);
      await page.goto('/account#appearance');
      expect(await themeAtBody()).toBe('dark dark');
      await expect(option(page, 'Dark')).toBeChecked();
      expect(await page.evaluate((key) => localStorage.getItem(key), THEME_KEY)).toBe('dark');
    } finally {
      await context.close();
    }
  });

  test('choosing the theme the user has sends nothing', async ({ browser }) => {
    const { context, page } = await newBrowser(browser, 'light');
    try {
      await signIn(page);
      await openAccount(page);
      await page.waitForLoadState('networkidle');
      const requests: string[] = [];
      page.on('request', (request) => {
        if (new URL(request.url()).pathname === THEME_API) requests.push(request.method());
      });
      await option(page, 'System').click();
      await option(page, 'Light').check();
      await expect.poll(() => requests).toEqual(['PUT']);
      await option(page, 'Light').click();
      await page.waitForTimeout(300);
      expect(requests).toEqual(['PUT']);
    } finally {
      await context.close();
    }
  });

  test('a choice that cannot be saved is undone, with the reason', async ({ browser }) => {
    const { context, page } = await newBrowser(browser, 'dark');
    try {
      await signIn(page);
      await openAccount(page);
      await page.route(`**${THEME_API}`, (route) =>
        route.request().method() === 'PUT'
          ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'The database is busy' }) })
          : route.fallback(),
      );
      await option(page, 'Light').check();
      await expect(page.getByText('The database is busy')).toBeVisible();
      await expectTheme(page, 'dark');
      await expect(option(page, 'System')).toBeChecked();
      await expect(option(page, 'Light')).not.toBeChecked();
      expect(await page.evaluate((key) => localStorage.getItem(key), THEME_KEY)).toBe('system');
      expect(await storedChoice(page)).toBe('system');
    } finally {
      await context.close();
    }
  });

  test('the arrow keys go through the choices while one is saved, and the last one is kept', async ({ browser }) => {
    const { context, page } = await newBrowser(browser, 'dark');
    try {
      await signIn(page);
      await openAccount(page);
      await page.waitForLoadState('networkidle');
      const sent: string[] = [];
      page.on('request', (request) => {
        if (new URL(request.url()).pathname === THEME_API && request.method() === 'PUT') sent.push(request.postDataJSON().theme);
      });
      await option(page, 'System').focus();
      await page.keyboard.press('ArrowDown'); // to Dark
      await page.keyboard.press('ArrowDown'); // to Light, while Dark is sent
      await expect(option(page, 'Light')).toBeFocused();
      await expect(option(page, 'Light')).toBeChecked();
      await expectTheme(page, 'light');
      await expect.poll(() => storedChoice(page)).toBe('light');
      expect(sent.at(-1)).toBe('light');
      expect(sent.length).toBeLessThanOrEqual(2);
    } finally {
      await context.close();
    }
  });

  test('a theme that cannot be read leaves the one of the browser, says so, and is read again', async ({ browser, page: api }) => {
    await storeChoice(api, 'light');
    const { context, page } = await newBrowser(browser, 'dark');
    try {
      await page.route(`**${THEME_API}`, (route) =>
        route.request().method() === 'GET' ? route.fulfill({ status: 503, body: '{"detail": "busy"}' }) : route.fallback(),
      );
      await signIn(page);
      await expectTheme(page, 'dark');
      await page.locator('nav').getByRole('link', { name: 'Account' }).click();
      await page.locator('main nav').getByRole('button', { name: 'Appearance and language' }).click();
      await expect(page.getByText('Your saved theme could not be loaded')).toBeVisible();
      await expect(option(page, 'System')).toBeChecked();
      await page.unrouteAll({ behavior: 'ignoreErrors' });
      await page.locator('nav').getByRole('link', { name: 'Dashboard' }).click();
      await page.locator('nav').getByRole('link', { name: 'Account' }).click();
      await page.locator('main nav').getByRole('button', { name: 'Appearance and language' }).click();
      await expect(option(page, 'Light')).toBeChecked();
      await expectTheme(page, 'light');
      await expect(page.getByText('Your saved theme could not be loaded')).toHaveCount(0);
    } finally {
      await context.close();
    }
  });

  test('is in Russian too', async ({ browser }) => {
    const { context, page } = await newBrowser(browser, 'light');
    try {
      await page.goto('/login');
      await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
      await page.goto('/login');
      await page.getByLabel('Имя пользователя').fill(USER.username);
      await page.getByLabel('Пароль').fill(USER.password);
      await page.getByRole('button', { name: 'Войти' }).click();
      await expect(page).toHaveURL('/');
      await page.goto('/account#appearance');
      const group = page.getByRole('group', { name: 'Цветовая тема' });
      await expect(group).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Тема', exact: true })).toBeVisible();
      for (const name of ['Тёмная', 'Светлая', 'Системная']) await expect(option(page, name)).toBeVisible();
      await option(page, 'Тёмная').check();
      await expectTheme(page, 'dark');
    } finally {
      await context.close();
    }
  });
});

test.describe('Every page in each theme', () => {
  // The administrator's theme stays System: the device's colour scheme picks the theme.
  test.beforeEach(async ({ page }) => {
    await storeChoice(page, 'system', E2E_ADMIN);
  });

  /** The pages of the navigation and the tabs of System, with fakes where the stand-in has no data. */
  const PAGES: { path: string; fake?: (page: Page) => Promise<unknown> }[] = [
    { path: '/' },
    { path: '/storage', fake: (page) => fakeStorageApi(page) },
    { path: '/snapshots' },
    { path: '/shares', fake: (page) => fakeSmbApi(page) },
    { path: '/files' },
    { path: '/containers' },
    { path: '/apps' },
    { path: '/users' },
    { path: '/audit' },
    { path: '/system#general' },
    { path: '/system#updates' },
    { path: '/system#security' },
    { path: '/account#security' },
    { path: '/account#appearance' },
    { path: '/system#monitor' },
    { path: '/system#logs' },
    { path: '/system#health', fake: (page) => fakeHealthApi(page) },
    { path: '/system#backup' },
    { path: '/system#scheduler' },
    { path: '/system#notifications' },
    { path: '/system#network' },
    { path: '/system#power' },
  ];

  for (const theme of ['light', 'dark'] as const) {
    test(`every page takes its colours from the ${theme} theme's tokens`, async ({ authenticatedPage: page }) => {
      test.setTimeout(180_000);
      await page.emulateMedia({ colorScheme: theme });
      const values = await tokens(page, theme);
      const problems: string[] = [];
      for (const { path, fake } of PAGES) {
        if (fake) await fake(page);
        await page.goto(path);
        await expectTheme(page, theme);
        await expect(page.locator('main h1, main h2').first()).toBeVisible();
        await page.waitForLoadState('networkidle');
        for (const found of await foreignColours(page)) problems.push(`${path}: ${found}`);
        await page.unrouteAll({ behavior: 'ignoreErrors' });
      }
      expect(problems).toEqual([]);

      // The key colours of a page are the theme's.
      await page.goto('/system#security');
      await expect(page.getByRole('heading', { name: 'Security of the NAS' })).toBeVisible();
      expect(await background(page, 'main')).toBe(values['surface']);
      expect(await background(page, 'aside')).toBe(values['surface-elevated']);
      expect(await background(page, '.card')).toBe(values['surface-elevated']);
      expect(await colour(page, 'main h2.text-xl')).toBe(values['content']); // Security of the NAS
      expect(await background(page, 'nav .nav-item-active')).toBe(values['brand']);
      // A 20 % tint of the brand, which test_webui_theme.py checks with the text on it.
      expect(rgba(await page.locator('nav .nav-item-active').first().evaluate((e) => getComputedStyle(e).backgroundColor))?.alpha)
        .toBe(0.2);
      // The active link is a brand tint with the text's colour (webui.redesign, ADR-0428).
      expect(await colour(page, 'nav .nav-item-active')).toBe(values['content']);
      const lightPage = luminance(values['surface']) > 0.8 && luminance(values['content']) < 0.05;
      const darkPage = luminance(values['surface']) < 0.05 && luminance(values['content']) > 0.8;
      expect(theme === 'light' ? lightPage : darkPage).toBe(true);

      // A dashboard card, a badge and a field of the Storage page, a dialog over its backdrop.
      await page.goto('/');
      await expect(page.locator('main [data-widget="cpu"]')).toBeVisible();
      expect(await background(page, 'main [data-widget="cpu"] .card')).toBe(values['surface-elevated']);
      await fakeStorageApi(page);
      await page.goto('/storage');
      await expect(page.locator('main .badge-success').first()).toBeVisible();
      expect(await colour(page, 'main .badge-success')).toBe(values['status-success']);
      expect(await background(page, 'main .badge-success')).toBe(values['status-success']); // its 20 % tint
      await page.unrouteAll({ behavior: 'ignoreErrors' });
      await page.goto('/users');
      await page.getByRole('button', { name: 'Add user' }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      expect(await background(page, '.modal')).toBe(values['surface-elevated']);
      expect(await background(page, '.modal-overlay')).toBe(values['scrim']);
      expect(await background(page, '.modal input.form-input')).toBe(values['surface-overlay']);
      expect(channels(await page.locator('.modal input.form-input').first().evaluate((e) => getComputedStyle(e).borderTopColor)))
        .toBe(values['border-strong']);
      expect(await foreignColours(page)).toEqual([]);
    });
  }

  test('the sign-in page takes the theme of the device, and the tokens of that theme', async ({ browser }) => {
    for (const theme of ['light', 'dark'] as const) {
      const { context, page } = await newBrowser(browser, theme);
      try {
        await page.goto('/login');
        await expectTheme(page, theme);
        await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible();
        const values = await tokens(page, theme);
        expect(await background(page, 'div.min-h-screen')).toBe(values['surface']);
        expect(await background(page, '.bg-surface-elevated')).toBe(values['surface-elevated']);
        expect(await background(page, 'button[type="submit"]')).toBe(values['brand']);
        expect(await foreignColours(page)).toEqual([]);
      } finally {
        await context.close();
      }
    }
  });
});
