import type { Page } from '@playwright/test';

// Canned /api/v1/health answers for System > Disk Health and the dashboard:
// the stand-in has neither lsblk nor smartctl, and the specs must not depend on
// the disks of the machine they run on. fakeHealthApi() answers the page's
// requests for the disks, the failure predictions and the system disk and aborts every other
// /api/v1/health/ request. The texts are the server's
// (supervisor/src/health/router.py, supervisor/tests/test_smart_predictions.py):
// change them with the server (ADR-0189). The device paths exist on no machine.

export const FAKE_SMART_DISKS = [
  {
    device: '/dev/e2e-sda', model: 'E2E HDD 4TB', serial: 'E2E-HDD-1', firmware: '82.00A82',
    capacity: '3.64 TB', temperature: 34, power_on_hours: 51775, power_cycle_count: 120,
    health_status: 'PASSED',
    attributes: [
      { id: 1, name: 'Raw_Read_Error_Rate', value: 55, worst: 50, thresh: 51, raw_value: '1234', status: 'warning' },
      { id: 3, name: 'Spin_Up_Time', value: 21, worst: 21, thresh: 21, raw_value: '9000', status: 'critical' },
      { id: 5, name: 'Reallocated_Sector_Ct', value: 100, worst: 100, thresh: 10, raw_value: '3', status: 'ok' },
      { id: 9, name: 'Power_On_Hours', value: 30, worst: 30, thresh: 0, raw_value: '51775', status: 'ok' },
      { id: 194, name: 'Temperature_Celsius', value: 116, worst: 100, thresh: 0, raw_value: '34', status: 'ok' },
    ],
    nvme_health: null,
    self_test_status: 'Short offline', last_test_result: 'Completed without error',
  },
  {
    device: '/dev/e2e-nvme0n1', model: 'E2E NVMe 1TB', serial: 'E2E-NVME-1', firmware: '1B2QGXA7',
    capacity: '931.51 GB', temperature: 40, power_on_hours: 1200, power_cycle_count: 45,
    health_status: 'PASSED', attributes: [],
    nvme_health: {
      critical_warning: 0, temperature: 40, available_spare: 100, available_spare_threshold: 10,
      percentage_used: 2, media_errors: 0, num_err_log_entries: 0,
    },
    self_test_status: null, last_test_result: null,
  },
  {
    // smartctl gave no SMART data (a USB bridge it does not know): lsblk's model and size.
    device: '/dev/e2e-sdb', model: 'E2E USB Disk', serial: 'Unknown', firmware: 'Unknown',
    capacity: '1.82 TB', temperature: null, power_on_hours: null, power_cycle_count: null,
    health_status: 'UNKNOWN', attributes: [], nvme_health: null,
    self_test_status: null, last_test_result: null,
  },
  {
    device: '/dev/e2e-sdc', model: 'E2E HDD 8TB', serial: 'E2E-HDD-2', firmware: '83.H0A83',
    capacity: '7.28 TB', temperature: 37, power_on_hours: 9120, power_cycle_count: 31,
    health_status: 'PASSED',
    attributes: [
      { id: 5, name: 'Reallocated_Sector_Ct', value: 100, worst: 100, thresh: 10, raw_value: '0', status: 'ok' },
      { id: 197, name: 'Current_Pending_Sector', value: 200, worst: 200, thresh: 0, raw_value: '8', status: 'ok' },
    ],
    nvme_health: null,
    self_test_status: null, last_test_result: null,
  },
];

/**
 * A disk that sleeps, as GET /health/disks answers it (ADR-0378): smartctl -n standby left it
 * alone, so no SMART data; lsblk's model, serial and size.
 */
export const SLEEPING_DISK = {
  device: '/dev/e2e-sdd', model: 'E2E HDD 640GB', serial: 'E2E-HDD-3', firmware: 'Unknown',
  capacity: '596.17 GB', temperature: null, power_on_hours: null, power_cycle_count: null,
  health_status: 'STANDBY', attributes: [], nvme_health: null,
  self_test_status: null, last_test_result: null,
};

// Predictions as GET /health/smart/predictions reports them. NVMe disks get
// none: the SMART monitor records ATA attributes only.

/** 3 new reallocated sectors within the last day. */
export const REPLACE_SOON = {
  serial: 'E2E-HDD-1', device: '/dev/e2e-sda', risk_score: 50, status: 'replace_soon',
  risk_factors: ['Reallocated_Sector_Ct: 3 (was 0)', 'Reallocated_Sector_Ct increasing rapidly'],
  recommendation: 'Disk showing signs of failure. Replace within 30 days.',
  data_points: 24,
};

/** The same disk a day later, without a further rise: no longer replace soon. */
export const MONITOR_CLOSELY = {
  ...REPLACE_SOON, risk_score: 30, status: 'monitor_closely',
  risk_factors: ['Reallocated_Sector_Ct: 3 (was 0)'],
  recommendation: 'Disk health degrading. Monitor daily and prepare replacement.',
  data_points: 45,
};

/** The second disk, quiet. */
export const HEALTHY_HDD_2 = {
  serial: 'E2E-HDD-2', device: '/dev/e2e-sdc', risk_score: 0, status: 'healthy',
  risk_factors: [], recommendation: 'No concerning trends detected.', data_points: 48,
};

/** The second disk after 8 new pending sectors within the last day. */
export const REPLACE_SOON_HDD_2 = {
  ...HEALTHY_HDD_2, risk_score: 50, status: 'replace_soon',
  risk_factors: ['Current_Pending_Sector: 8 (was 0)', 'Current_Pending_Sector increasing rapidly'],
  recommendation: 'Disk showing signs of failure. Replace within 30 days.',
};

// The system disk as GET /health/system-disk answers it (supervisor/src/health/system_disk.py,
// ADR-0471): the BPI-W3's eMMC of 2026-11-23, read with mmc extcsd read.
export const W3_EMMC = {
  device: '/dev/mmcblk0', model: 'BJTD4R', size: 31268536320, root: '/dev/mmcblk0p1', filesystem: 'ext4',
  life_time_a: 1, life_time_b: 1, pre_eol: 1, wear_source: 'mmc', wear_read_at: '2026-11-23T08:00:04Z',
  mmc_missing: false, wear_error: null, io_errors: 0, ext4_errors: 0, read_only: false,
  direct_read_failed: false, checked_at: '2026-11-23T10:05:04Z', check_error: null,
};

/** The answer for an eMMC system disk with `emmc`'s values and `problems` (default: the W3, none). */
export function systemDiskAnswer(emmc: object = W3_EMMC, problems: object[] = []): FakeAnswer {
  return { json: { emmc, reason: null, system_disk: null, detail: null, problems } };
}

/** The answer for a system disk that is no eMMC: an x86 host's SATA disk. */
export const NO_EMMC: FakeAnswer = {
  json: { emmc: null, reason: 'not_emmc', system_disk: '/dev/e2e-sda', detail: null, problems: [] },
};

export interface FakeAnswer {
  status?: number;
  json: unknown;
}

export interface FakeHealthOptions {
  /** The answer of GET /health/disks at the time of each request (default FAKE_SMART_DISKS). */
  disks?: () => FakeAnswer;
  /** The answer of GET /health/smart/predictions at each request (default: no predictions). */
  predictions?: () => FakeAnswer;
  /** The answer of GET /health/system-disk at each request (default: the W3's eMMC, healthy). */
  systemDisk?: () => FakeAnswer;
}

/** The predictions answer of GET /health/smart/predictions. */
export function predictionsAnswer(...predictions: object[]): FakeAnswer {
  return { json: { predictions } };
}

/** Answer the Disk Health and prediction requests; returns the paths requested. */
export async function fakeHealthApi(page: Page, options: FakeHealthOptions = {}): Promise<string[]> {
  const disks: () => FakeAnswer = options.disks ?? (() => ({ json: { disks: FAKE_SMART_DISKS } }));
  const predictions: () => FakeAnswer = options.predictions ?? (() => predictionsAnswer());
  const systemDisk: () => FakeAnswer = options.systemDisk ?? (() => systemDiskAnswer());
  const requested: string[] = [];
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/health/'),
    async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.slice('/api/v1/health'.length);
      requested.push(`${request.method()} ${path}`);
      const answer =
        request.method() !== 'GET'
          ? undefined
          : path === '/disks'
            ? disks()
            : path === '/smart/predictions'
              ? predictions()
              : path === '/system-disk'
                ? systemDisk()
                : undefined;
      if (!answer) return route.abort();
      return route.fulfill({ status: answer.status ?? 200, json: answer.json });
    },
  );
  return requested;
}
