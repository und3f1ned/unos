import type { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';

/**
 * webui.fields-in-safari: every form field keeps the design's look and height in
 * WebKit, Safari's engine, as in Chromium. playwright.config.ts runs this spec in both (the
 * projects chromium and webkit); the other specs run in Chromium only.
 *
 * For a few forms with selects, number, date and time fields, at 1440 and 375 px: every one-line
 * field is the design's 36 px high and the fields of a row line up, a select draws the design's
 * chevron instead of the browser's own control, and a number field shows no spin buttons of the
 * browser's (a field that steps has Input's own steppers).
 */

const WIDTHS = [1440, 375];
// The height of a one-line field (ADR-0428): min-h-9 of .form-input, .form-select and .form-affix,
// and Select's trigger.
const FIELD_HEIGHT = 36;

interface Field {
  name: string;
  height: number;
  top: number;
  bottom: number;
}

/**
 * The one-line fields of `form` that show, as the eye sees them: a field framed with its unit or
 * steppers (Input's prefix, suffix and steppers) is its frame, and Select is its trigger.
 * offsetHeight leaves out a dialog's opening transform.
 */
async function fields(form: Locator): Promise<Field[]> {
  return form.evaluate((root) =>
    [
      ...root.querySelectorAll<HTMLElement>(
        'input:not([type="checkbox"], [type="radio"], [type="hidden"], [type="file"]), select, button[role="combobox"]',
      ),
    ]
      .filter((field) => field.offsetWidth > 0 && field.offsetHeight > 0)
      .map((field) => {
        const frame = field.closest<HTMLElement>('.form-affix') ?? field;
        const box = frame.getBoundingClientRect();
        const label = field.id ? root.querySelector(`label[for="${field.id}"]`)?.textContent?.trim() : '';
        return {
          name: `${field.tagName.toLowerCase()}[${field.getAttribute('type') ?? ''}] ${label || field.getAttribute('aria-label') || field.id}`,
          height: frame.offsetHeight,
          top: Math.round(box.top),
          bottom: Math.round(box.bottom),
        };
      }),
  );
}

/** Each field one height, the design's, and the fields that share a line start on one line. */
async function expectOneHeight(form: Locator, where: string) {
  const found = await fields(form);
  expect(found.length, `${where}: its fields`).toBeGreaterThan(1);
  const heights = Object.fromEntries(found.map((field) => [field.name, field.height]));
  expect(heights, `${where}: the height of each field`).toEqual(
    Object.fromEntries(found.map((field) => [field.name, FIELD_HEIGHT])),
  );
  for (const field of found) {
    const row = found.filter((other) => other.top < field.bottom && field.top < other.bottom);
    expect(
      row.map((other) => [other.name, other.top]),
      `${where}: the row of ${field.name}`,
    ).toEqual(row.map((other) => [other.name, field.top]));
  }
}

/**
 * The `count` selects of `form`, each drawn by the design: no look of the browser's own, and the
 * design's chevron in the theme's content-secondary.
 */
async function expectDesignedSelects(form: Locator, where: string, count: number) {
  const selects = form.locator('select:visible');
  await expect(selects, `${where}: its selects`).toHaveCount(count);
  const drawn = await selects.evaluateAll((all) => {
    const token = getComputedStyle(document.documentElement).getPropertyValue('--color-content-secondary').trim();
    const colour = `rgb(${token.split(/\s+/).join(', ')})`;
    return all.map((select) => {
      const style = getComputedStyle(select);
      return { id: select.id, appearance: style.appearance, chevron: style.backgroundImage.includes(colour) };
    });
  });
  expect(drawn, where).toEqual(drawn.map(({ id }) => ({ id, appearance: 'none', chevron: true })));
}

/**
 * No number field of `form` steps when the top of its right end is pressed, where the browser
 * draws its spin buttons (Chromium under the pointer, WebKit always): the press only puts the
 * caret there.
 */
async function expectNoSpinButtons(form: Locator, where: string, count: number) {
  const numbers = form.locator('input[type="number"]:visible');
  await expect(numbers, `${where}: its number fields`).toHaveCount(count);
  for (let i = 0; i < count; i++) {
    const field = numbers.nth(i);
    const [width, paddingRight, paddingTop] = await field.evaluate((input: HTMLInputElement) => {
      const style = getComputedStyle(input);
      return [input.offsetWidth, parseFloat(style.paddingRight), parseFloat(style.paddingTop)];
    });
    await field.fill('5');
    await field.click({ position: { x: width - paddingRight - 4, y: paddingTop + 4 } });
    await expect(field, `${where}: the number field ${(await field.getAttribute('id')) ?? i}`).toHaveValue('5');
  }
}

/** The fields of `form` at each width: one height, `selects` designed selects, `numbers` number fields without spin buttons. */
async function expectDesignedFields(
  page: Page,
  form: Locator,
  view: string,
  { selects, numbers }: { selects: number; numbers: number },
) {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    const where = `${view} at ${width} px`;
    await expectOneHeight(form, where);
    await expectDesignedSelects(form, where, selects);
    await expectNoSpinButtons(form, where, numbers);
  }
}

test.describe('Form fields in Chromium and WebKit', () => {
  test('the Create task dialog: name, selects, Copies to keep with its steppers, passphrases and the time', async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/system#scheduler');
    await page.getByRole('button', { name: 'New task' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create task' });
    await dialog.getByLabel('Task type', { exact: true }).selectOption('backup');
    await dialog.getByLabel('Frequency', { exact: true }).selectOption('weekly');
    await expect(dialog.getByLabel('Day of the week', { exact: true })).toBeVisible();

    // In both themes: the chevron takes each theme's colour (the account's theme follows the system's).
    for (const scheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      await expect(page.locator('html')).toHaveAttribute('data-theme', scheme);
      await expectDesignedFields(page, dialog, `the Create task dialog, ${scheme}`, { selects: 5, numbers: 1 });
    }

    // Copies to keep steps by one with Input's own buttons instead (ADR-0432).
    const keep = dialog.getByLabel('Copies to keep', { exact: true });
    await keep.fill('7');
    await dialog.getByRole('button', { name: 'Increase Copies to keep' }).click();
    await expect(keep).toHaveValue('8');
    await dialog.getByRole('button', { name: 'Decrease Copies to keep' }).click();
    await expect(keep).toHaveValue('7');
  });

  test('the certificate dialog: text fields and the validity in days', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.getByRole('button', { name: 'Manage', exact: true }).first().click();
    await page.getByRole('button', { name: 'Generate Self-Signed' }).click();
    const dialog = page.getByRole('dialog', { name: 'Generate Self-Signed Certificate' });
    await expect(dialog.getByLabel('Validity', { exact: false })).toBeVisible();

    await expectDesignedFields(page, dialog, 'the certificate dialog', { selects: 0, numbers: 1 });
  });

  test("the off-device copy's settings: a REST server's fields and select, limits with units, counts with steppers", async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/system#backup');
    const section = page.getByRole('region', { name: 'Off-device copy' });
    const settings = section.locator('.card', { has: page.getByRole('heading', { name: 'Settings of the copy' }) });
    await settings.getByText('restic REST server', { exact: true }).click();
    await expect(settings.locator('#offsite-rest-tls')).toBeVisible();

    // The TLS check of the REST server; the two limits, three counts and the check's percent.
    await expectDesignedFields(page, settings, "the off-device copy's settings", { selects: 1, numbers: 6 });
  });

  test("the audit log's filters: search, a name, the action and two dates", async ({ authenticatedPage: page }) => {
    await page.goto('/audit');
    const filters = page.locator('#audit-filters');
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      // On a phone the fields fold behind Filters.
      if (width < 640) await page.getByRole('button', { name: 'Filters' }).click();
      await expect(filters.getByLabel('From', { exact: true })).toBeVisible();
      const where = `the audit log's filters at ${width} px`;
      await expectOneHeight(filters, where);
    }
  });

  test('the power schedule: two times and the idle minutes', async ({ authenticatedPage: page }) => {
    await page.goto('/system#power');
    const schedule = page.locator('.card', { has: page.getByRole('heading', { name: 'Shutdown and wake schedule' }) });
    for (const setting of ['Daily shutdown', 'Daily wake', 'Idle shutdown']) {
      await schedule.getByLabel(setting, { exact: true }).check();
    }
    await expect(schedule.locator('input[type="time"]')).toHaveCount(2);
    await expect(schedule.locator('input[type="number"]')).toHaveCount(1);

    await expectDesignedFields(page, schedule, 'the power schedule', { selects: 0, numbers: 1 });
  });
});
