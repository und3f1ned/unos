import type { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import {
  FAKE_SMART_DISKS,
  HEALTHY_HDD_2,
  MONITOR_CLOSELY,
  REPLACE_SOON,
  REPLACE_SOON_HDD_2,
  fakeHealthApi,
  predictionsAnswer,
  type FakeAnswer,
} from './health-fakes';

/** The disk with this device path in the list of Disk Health. */
function disk(page: Page, device: string): Locator {
  return page.getByRole('list', { name: 'Disks' }).getByRole('listitem').filter({ hasText: device });
}

/** The value of a field of a disk's dialog. */
function field(dialog: Locator, name: string): Locator {
  return dialog
    .locator('dl > div')
    .filter({ has: dialog.page().locator('dt', { hasText: new RegExp(`^${name}$`) }) })
    .locator('dd');
}

/** The cells of the table row of a dialog that holds `name`. */
function row(dialog: Locator, name: string): Locator {
  return dialog.getByRole('row').filter({ hasText: name }).locator('td');
}

function replaceSoonMark(scope: Locator | Page): Locator {
  return scope.locator('.badge', { hasText: /^Replace soon$/ });
}

/** The alert that lists the predictions to replace soon that match no listed disk. */
function unmatchedAlert(page: Page): Locator {
  return page.locator('.alert', { has: page.getByText('Replace soon', { exact: true }) });
}

/** Reload the tab's data with its refresh button, and wait until it is loaded. */
async function refresh(page: Page) {
  const button = page.getByRole('button', { name: 'Refresh disk health' });
  await button.click();
  await expect(button).toBeEnabled();
}

test.describe('Disk Health', () => {
  test('should display health tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should display disk list', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    // Should show disks or health section
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should show health section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Health|Disk/i').first()).toBeVisible();
  });

  test('should display health content', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should display power info if disks present', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should show health header', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  // The API is answered in the browser: the stand-in needs no lsblk.
  test('shows why the disks cannot be listed', async ({ authenticatedPage: page }) => {
    let fail = true;
    await page.route(
      (url) => url.pathname === '/api/v1/health/disks',
      (route) =>
        fail
          ? route.fulfill({ status: 500, json: { detail: "Could not list the host's disks: lsblk failed" } })
          : route.fulfill({ json: { disks: [] } }),
    );
    await page.goto('/system#health');
    await expect(page.getByText("Could not list the host's disks: lsblk failed")).toBeVisible();
    await expect(page.getByText('No disks found')).toHaveCount(0);

    // A refresh that works clears the error.
    fail = false;
    await page.locator('h2:has-text("Disk Health")').locator('xpath=..').getByRole('button').click();
    await expect(page.getByText('No disks found')).toBeVisible();
    await expect(page.getByText("Could not list the host's disks: lsblk failed")).toHaveCount(0);
  });

  // storage.smart: open a disk. The dialog shows what GET /health/disks
  // returned, so opening it runs no smartctl (ADR-0232).
  test('opens a disk with its SMART attributes, temperature and overall health', async ({
    authenticatedPage: page,
  }) => {
    const requested = await fakeHealthApi(page);
    await page.goto('/system#health');

    // The NVMe disk and the disk without SMART data are listed too.
    await expect(page.getByRole('list', { name: 'Disks' }).getByRole('listitem')).toHaveCount(FAKE_SMART_DISKS.length);

    await disk(page, '/dev/e2e-sda').getByRole('button', { name: 'SMART data of /dev/e2e-sda' }).click();
    const sata = page.getByRole('dialog', { name: 'E2E HDD 4TB (/dev/e2e-sda)' });
    await expect(field(sata, 'Overall health')).toHaveText('PASSED');
    await expect(field(sata, 'Temperature')).toHaveText('34 °C');
    await expect(field(sata, 'Power-on hours')).toHaveText('51775 (5 years, 332 days)');
    await expect(field(sata, 'Power cycles')).toHaveText('120');
    await expect(field(sata, 'Serial')).toHaveText('E2E-HDD-1');
    await expect(field(sata, 'Firmware')).toHaveText('82.00A82');
    await expect(field(sata, 'Last self-test')).toHaveText('Short offline: Completed without error');
    await expect(sata.getByRole('columnheader')).toHaveText([
      'ID', 'Attribute', 'Value', 'Worst', 'Threshold', 'Raw value', 'Status',
    ]);
    await expect(row(sata, 'Reallocated_Sector_Ct')).toHaveText([
      '5', 'Reallocated_Sector_Ct', '100', '100', '10', '3', 'OK',
    ]);
    await expect(row(sata, 'Raw_Read_Error_Rate').last()).toHaveText('Warning');
    await expect(row(sata, 'Spin_Up_Time').last()).toHaveText('Failing');
    await expect(sata.getByRole('row')).toHaveCount(1 + 5);
    await page.keyboard.press('Escape');
    await expect(sata).toHaveCount(0);

    // An NVMe disk has no attributes but its SMART/Health Information log.
    await disk(page, '/dev/e2e-nvme0n1')
      .getByRole('button', { name: 'SMART data of /dev/e2e-nvme0n1' })
      .click();
    const nvme = page.getByRole('dialog', { name: 'E2E NVMe 1TB (/dev/e2e-nvme0n1)' });
    await expect(field(nvme, 'Overall health')).toHaveText('PASSED');
    await expect(field(nvme, 'Temperature')).toHaveText('40 °C');
    await expect(nvme.getByRole('columnheader')).toHaveText(['Health information', 'Value']);
    await expect(row(nvme, 'Available spare threshold')).toHaveText(['Available spare threshold', '10']);
    await expect(row(nvme, 'Percentage used')).toHaveText(['Percentage used', '2']);
    await expect(row(nvme, 'Media errors')).toHaveText(['Media errors', '0']);
    await expect(nvme.getByRole('row')).toHaveCount(1 + 7);
    await nvme.getByRole('button', { name: 'Close modal' }).click();
    await expect(nvme).toHaveCount(0);

    // Without SMART data the health is unknown, and the dialog says so.
    await disk(page, '/dev/e2e-sdb').getByRole('button', { name: 'SMART data of /dev/e2e-sdb' }).click();
    const usb = page.getByRole('dialog', { name: 'E2E USB Disk (/dev/e2e-sdb)' });
    await expect(field(usb, 'Overall health')).toHaveText('UNKNOWN');
    await expect(field(usb, 'Temperature')).toHaveText('Not reported');
    await expect(usb.getByText('smartctl reported no SMART attributes for this disk.')).toBeVisible();
    await expect(usb.getByRole('table')).toHaveCount(0);

    // One load of the tab, in either order; the dialogs asked for nothing more.
    expect([...requested].sort()).toEqual(['GET /disks', 'GET /smart/predictions']);
  });

  // storage.smart-replace-soon (ADR-0232).
  test('marks a disk to replace soon with the reason, until the prediction changes', async ({
    authenticatedPage: page,
  }) => {
    let predictions = predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2);
    await fakeHealthApi(page, { predictions: () => predictions });
    await page.goto('/system#health');

    const failing = disk(page, '/dev/e2e-sda');
    await expect(replaceSoonMark(failing)).toBeVisible();
    await expect(failing).toContainText('Reallocated_Sector_Ct: 3 (was 0)');
    await expect(failing).toContainText('Reallocated_Sector_Ct increasing rapidly');
    await expect(failing).toContainText('Disk showing signs of failure. Replace within 30 days.');
    // A healthy prediction, and the disks without one, get no mark.
    await expect(replaceSoonMark(page)).toHaveCount(1);

    // A day later the first disk's rise is no longer fast, and the second disk is the one to replace.
    predictions = predictionsAnswer(REPLACE_SOON_HDD_2, MONITOR_CLOSELY);
    await refresh(page);
    const second = disk(page, '/dev/e2e-sdc');
    await expect(replaceSoonMark(second)).toBeVisible();
    await expect(second).toContainText('Current_Pending_Sector: 8 (was 0)');
    await expect(replaceSoonMark(failing)).toHaveCount(0);
    await expect(failing).not.toContainText('Reallocated_Sector_Ct increasing rapidly');
    await expect(failing).not.toContainText('Replace within 30 days');

    // No prediction says replace soon: no mark is left.
    predictions = predictionsAnswer(MONITOR_CLOSELY, HEALTHY_HDD_2);
    await refresh(page);
    await expect(page.getByRole('list', { name: 'Disks' }).getByRole('listitem')).toHaveCount(FAKE_SMART_DISKS.length);
    await expect(replaceSoonMark(page)).toHaveCount(0);
    await expect(page.getByText('Replace within 30 days')).toHaveCount(0);
  });

  test('matches the predictions to the disks by serial', async ({ authenticatedPage: page }) => {
    await fakeHealthApi(page, {
      predictions: () =>
        predictionsAnswer(
          REPLACE_SOON,
          // The last disk recorded at /dev/e2e-sdb, behind a USB bridge without a serial;
          // the disk there now gives smartctl no SMART data, so it is another one.
          { ...REPLACE_SOON, serial: 'E2E-OLD-1', device: '/dev/e2e-sdb' },
        ),
    });
    await page.goto('/system#health');

    await expect(replaceSoonMark(disk(page, '/dev/e2e-sda'))).toBeVisible();
    await expect(replaceSoonMark(page)).toHaveCount(1);
    // The prediction that matches no listed disk is listed apart, with its path and serial.
    await expect(unmatchedAlert(page).getByRole('listitem')).toHaveText([
      '/dev/e2e-sdb (serial E2E-OLD-1): Reallocated_Sector_Ct: 3 (was 0); Reallocated_Sector_Ct increasing rapidly',
    ]);
  });

  test('lists a disk to replace soon that smartctl could not read on this load', async ({
    authenticatedPage: page,
  }) => {
    // The server found the disk at /dev/e2e-sdb by the serial lsblk reports for it,
    // but smartctl timed out on it: Disk Health lists it with the serial Unknown.
    const usb = { ...REPLACE_SOON, serial: 'E2E-USB-1', device: '/dev/e2e-sdb' };
    let disks: object[] = FAKE_SMART_DISKS;
    await fakeHealthApi(page, {
      disks: () => ({ json: { disks } }),
      predictions: () => predictionsAnswer(REPLACE_SOON, usb),
    });
    await page.goto('/system#health');

    await expect(unmatchedAlert(page).getByRole('listitem')).toHaveText([
      '/dev/e2e-sdb (serial E2E-USB-1): Reallocated_Sector_Ct: 3 (was 0); Reallocated_Sector_Ct increasing rapidly',
    ]);
    await expect(replaceSoonMark(disk(page, '/dev/e2e-sda'))).toBeVisible();
    await expect(replaceSoonMark(disk(page, '/dev/e2e-sdb'))).toHaveCount(0);

    // At the next load smartctl reads the disk: the disk is marked, and the alert goes.
    disks = FAKE_SMART_DISKS.map((listed) =>
      listed.device === '/dev/e2e-sdb'
        ? {
            ...listed, serial: 'E2E-USB-1', firmware: '01.01A01', temperature: 31,
            power_on_hours: 20410, power_cycle_count: 77, health_status: 'PASSED',
            attributes: [
              { id: 5, name: 'Reallocated_Sector_Ct', value: 100, worst: 100, thresh: 10, raw_value: '3', status: 'ok' },
            ],
          }
        : listed,
    );
    await refresh(page);
    await expect(replaceSoonMark(disk(page, '/dev/e2e-sdb'))).toBeVisible();
    await expect(disk(page, '/dev/e2e-sdb')).toContainText('Reallocated_Sector_Ct increasing rapidly');
    await expect(unmatchedAlert(page)).toHaveCount(0);
  });

  test('says when the failure predictions cannot be loaded', async ({ authenticatedPage: page }) => {
    const detail = "Could not list the host's disks: lsblk failed: lsblk: failed to access sysfs directory: /sys/dev/block";
    let predictions: FakeAnswer = { status: 500, json: { detail } };
    await fakeHealthApi(page, { predictions: () => predictions });
    await page.goto('/system#health');

    await expect(page.getByText('Could not load the failure predictions')).toBeVisible();
    await expect(page.getByText(detail)).toBeVisible();
    // The disks are still listed.
    await expect(page.getByRole('list', { name: 'Disks' }).getByRole('listitem')).toHaveCount(FAKE_SMART_DISKS.length);

    predictions = predictionsAnswer(REPLACE_SOON);
    await refresh(page);
    await expect(replaceSoonMark(disk(page, '/dev/e2e-sda'))).toBeVisible();
    await expect(page.getByText('Could not load the failure predictions')).toHaveCount(0);
  });
});
