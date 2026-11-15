import type { Locator, Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import { FAKE_IMPORTABLE, fakeStorageApi, noBalance } from './storage-fakes';
import { POOLS, accessOf, fakeNfsExport, fakeShare, fakeSmbApi, fakeUser } from './shares-fakes';
import { HEALTHY_HDD_2, REPLACE_SOON, fakeHealthApi, predictionsAnswer } from './health-fakes';
import { fakeNetworkApi } from './network-fakes';
import { fakeNotificationsApi, type Channel } from './notifications-fakes';

// The web UI on a phone (feature webui.mobile-layout, ADR-0302). At 375 x 812, a small phone,
// every page of the navigation, the views behind its tabs, each tab of System, the main dialogs,
// a regular user's Account page, sign-in and first run fit the width of the screen, in English
// and in Russian, whose texts are longer: neither the page nor a part of it scrolls sideways,
// except a table in its own frame (the class table-frame), and nothing is cut off at the side.
// What can be taller than the screen, a dialog or a long folder, shows its start as it opens and
// its end once scrolled, also on a phone held sideways (667 x 375, still below md).
// Below Tailwind's md breakpoint the sidebar folds into a menu behind a button of the header bar.
// The data that the stand-in lacks (disks, btrfs, Docker, the host's network, journal and power)
// comes from route fakes, with names and paths as long as a NAS has them. Nothing here changes
// the stand-in but the regular account that a test of the menu creates and deletes. The other
// specs run at the desktop size of playwright.config.ts, where the sidebar stays.

const PHONE = { width: 375, height: 812 };
/** The same phone held sideways: narrower than md, so with the phone's layout, and 375 px high. */
const PHONE_SIDEWAYS = { width: 667, height: 375 };
/** The window of the other specs (Playwright's Desktop Chrome). */
const DESKTOP = { width: 1280, height: 720 };

/**
 * What makes the page wider than the screen: the document scrolling sideways, an element that
 * scrolls sideways other than a table's frame (.table-frame), and an element that cuts off its
 * content at the side without an ellipsis. Each comes with its tag, classes and first words,
 * and with the innermost elements that reach out of it on the right.
 */
async function tooWide(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const describe = (element: Element) => {
      const words = (element.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 60);
      return `<${element.tagName.toLowerCase()} class="${element.getAttribute('class') ?? ''}"> ${words}`;
    };
    /**
     * The innermost elements under `container`, outside its tables' frames, that reach beyond
     * `right`: with their box, or with content that overflows it (a long word).
     */
    const beyond = (container: Element, right: number) => {
      const out = [...container.querySelectorAll('*')].filter((e) => {
        if (e instanceof SVGElement && !(e instanceof SVGSVGElement)) return false;
        const frame = e.parentElement?.closest('.table-frame');
        if (frame && container.contains(frame)) return false;
        const box = e.getBoundingClientRect();
        return box.left + Math.max(box.width, e.scrollWidth) > right + 1;
      });
      return out
        .filter((e) => !out.some((other) => other !== e && e.contains(other)))
        .slice(0, 3)
        .map((e) => `\n    reaching out: ${describe(e)}`)
        .join('');
    };

    const found: string[] = [];
    const root = document.documentElement;
    if (root.scrollWidth > root.clientWidth) {
      found.push(`the page is ${root.scrollWidth} px wide${beyond(document.body, root.clientWidth)}`);
    }
    for (const element of document.body.querySelectorAll('*')) {
      // Form fields scroll their own value; a 1 px element is text for screen readers only.
      if (!(element instanceof HTMLElement) || element.clientWidth <= 1) continue;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName)) continue;
      // A pixel of rounding is no overflow.
      if (element.scrollWidth <= element.clientWidth + 1) continue;
      const { overflowX, textOverflow } = getComputedStyle(element);
      const scrolls = overflowX === 'auto' || overflowX === 'scroll';
      const cuts = (overflowX === 'hidden' || overflowX === 'clip') && textOverflow !== 'ellipsis';
      if ((scrolls && !element.classList.contains('table-frame')) || cuts) {
        const right = element.getBoundingClientRect().left + element.clientLeft + element.clientWidth;
        found.push(
          `${scrolls ? 'scrolls' : 'cuts off'} ${element.scrollWidth - element.clientWidth} px: ` +
            `${describe(element)}${beyond(element, right)}`,
        );
      }
    }
    return found;
  });
}

/**
 * The share of an element's box that shows when it shows whole: all of it but the fraction of a
 * pixel that the rounding of positions can leave cut off at an edge.
 */
const WHOLE = 0.99;

/**
 * Scroll `target` into view as a finger can, and expect it to show whole: each ancestor that
 * scrolls (overflow auto or scroll), from the innermost one out, and then the document, scroll
 * just far enough to show it with a margin of 2 px. Never an ancestor that cuts off its content
 * (overflow hidden or clip): no swipe moves it, though a script can (scrollIntoView does). Whether
 * the target then shows whole, toBeInViewport says: the IntersectionObserver that it asks leaves
 * out what an ancestor cuts off. Again until it holds: a dialog can still grow once it is shown.
 */
async function expectReachable(target: Locator, message: string): Promise<void> {
  await expect(async () => {
    await target.evaluate((element) => {
      const MARGIN = 2;
      const scrollers: Element[] = [];
      for (let box = element.parentElement; box; box = box.parentElement) {
        const { overflowY } = getComputedStyle(box);
        if (overflowY === 'auto' || overflowY === 'scroll') scrollers.push(box);
      }
      if (document.scrollingElement) scrollers.push(document.scrollingElement);
      for (const box of scrollers) {
        const frame = box.getBoundingClientRect();
        const view =
          box === document.scrollingElement
            ? { top: 0, bottom: window.innerHeight }
            : { top: frame.top + box.clientTop, bottom: frame.top + box.clientTop + box.clientHeight };
        const { top, bottom } = element.getBoundingClientRect();
        // Down until its bottom shows, but never past its top (an element taller than the view).
        if (bottom > view.bottom - MARGIN) box.scrollTop += Math.min(bottom - view.bottom + MARGIN, top - view.top);
        else if (top < view.top + MARGIN) box.scrollTop -= view.top - top + MARGIN;
      }
    });
    await expect(target, message).toBeInViewport({ ratio: WHOLE, timeout: 1000 });
  }).toPass({ timeout: 15_000 });
}

/**
 * How far the overlay of an open dialog (Modal's, or one with its classes) scrolls past the
 * dialog. An element placed absolutely inside a part of the dialog that scrolls on its own, such
 * as a text for screen readers, is placed in the overlay unless a box between them is positioned:
 * it then makes the overlay scroll on below the dialog, into the void.
 */
async function overscroll(page: Page): Promise<number> {
  return page.evaluate(() => {
    const overlay = document.querySelector('.modal-overlay');
    const dialog = overlay?.firstElementChild;
    if (!(overlay instanceof HTMLElement) || !(dialog instanceof HTMLElement)) return 0;
    const { paddingTop, paddingBottom } = getComputedStyle(overlay);
    const needed = dialog.offsetHeight + parseFloat(paddingTop) + parseFloat(paddingBottom);
    return overlay.scrollHeight - Math.max(overlay.clientHeight, needed);
  });
}

/**
 * Answer the page's GET requests for the paths of `answers` with their bodies, and abort any
 * other request to them: nothing of this file changes the stand-in.
 */
async function serve(page: Page, answers: Record<string, unknown>): Promise<void> {
  await page.route(
    (url) => Object.hasOwn(answers, url.pathname),
    (route) => {
      const request = route.request();
      if (request.method() !== 'GET') return route.abort();
      return route.fulfill({ json: answers[new URL(request.url()).pathname] });
    },
  );
}

// As the API answers them (the interfaces of webui/src/lib/api), with long names where a NAS has
// them: image names of a registry, folders of people, Samba's log lines.

// A host name with no hyphen, so no place where a browser breaks it on its own (ADR-0302).
const LONG_HOST_NAME = 'nasinthelivingroomoftheoldhouse.home.arpa';

const SYSTEM_INFO = {
  hostname: 'nas-living-room.home.arpa',
  kernel: '6.12.48+deb13-arm64',
  os: 'Debian GNU/Linux 13 (trixie)',
  architecture: 'aarch64',
  // When the host started (psutil.boot_time()): 3 days and 4 hours ago.
  boot_time: Date.now() / 1000 - (3 * 24 + 4) * 3600,
};

function container(id: string, name: string, image: string, state: string, ports: object[] = []) {
  return { id, name, image, status: state, state, created: '2026-11-01T10:00:00Z', ports, labels: {} };
}
const CONTAINERS = [
  container('0d1f2e3c4b5a', 'e2e-web', 'nginx:1.27', 'running', [
    { host_port: 8080, container_port: 80, protocol: 'tcp' },
    { host_port: 8443, container_port: 443, protocol: 'tcp' },
  ]),
  container('1a2b3c4d5e6f', 'e2e-immich-machine-learning', 'ghcr.io/immich-app/immich-machine-learning:release', 'exited'),
  container('2b3c4d5e6f70', 'e2e-cache', 'redis:7', 'paused', [{ host_port: null, container_port: 6379, protocol: 'tcp' }]),
];
const IMAGES = [
  { id: 'sha256:605c77e624dd', tags: ['nginx:1.27'], size: 192_000_000, created: '2026-10-01T08:00:00Z' },
  {
    id: 'sha256:7a8b9c0d1e2f',
    tags: ['ghcr.io/immich-app/immich-machine-learning:release'],
    size: 1_438_000_000,
    created: '2026-10-02T08:00:00Z',
  },
];
const DOCKER = { '/api/v1/containers': CONTAINERS, '/api/v1/containers/images': IMAGES };

const DATA = `${POOLS}/e2e_data`;
const SUBVOLUMES = [
  { path: DATA, pool: 'e2e_data', volume: null, volumes_error: null },
  { path: `${DATA}/documents`, pool: 'e2e_data', volume: 'documents', volumes_error: null },
];
const SNAPSHOTS = ['nightly_20261107_020000', 'before-upgrade_20261106_181500'].map((name, i) => ({
  id: name,
  name,
  subvolume: DATA,
  path: `${DATA}/.snapshots/${name}`,
  created: `2026-11-0${7 - i}T02:00:00+00:00`,
}));
// The copies that restores made right in the pool (GET /snapshots/copies, ADR-0358).
const COPIES = ['restore_20261107_103000', 'restore_20261106_090000'].map((name, i) => ({
  name,
  pool: 'e2e_data',
  volume: null,
  subvolume: DATA,
  path: `${DATA}/${name}`,
  created: `2026-11-0${7 - i}T10:30:00+00:00`,
}));
const SNAPSHOTS_API = {
  '/api/v1/snapshots/subvolumes': SUBVOLUMES,
  '/api/v1/snapshots/': SNAPSHOTS,
  '/api/v1/snapshots': SNAPSHOTS,
  '/api/v1/snapshots/copies': COPIES,
};

const INSTALLED_APPS = [
  {
    id: 'e2e-jellyfin',
    template_id: 'jellyfin',
    name: 'e2e-jellyfin',
    container_id: 'e2e-jellyfin',
    status: 'running',
    web_port: 8096,
    created_at: '2026-11-01T10:00:00Z',
    pool: 'e2e_media',
    volumes: [
      { host: `${POOLS}/e2e_media/apps/jellyfin/config`, container: '/config', readonly: false },
      { host: `${POOLS}/e2e_media/Movies and TV shows`, container: '/media', readonly: true },
    ],
    data_outside_pools: [],
    runs_in_unos_smb: false,
  },
];

// The Files page: the pools folder holds e2e_data, which holds a folder and files with long names.
const FAMILY = `${DATA}/Family photos and videos 2019-2026`;
function entry(path: string, type: 'file' | 'directory', size: number) {
  const name = path.slice(path.lastIndexOf('/') + 1);
  return {
    name, path, type, size, modified: '2026-11-07T10:15:30+00:00', permissions: 'rwxr-xr-x', owner: 'alice', group: 'unos-smb',
  };
}
function listing(path: string, parent: string | null, items: ReturnType<typeof entry>[]) {
  const total_size = items.reduce((sum, item) => sum + item.size, 0);
  return { path, parent, items, total_size, item_count: items.length };
}
type Folders = Record<string, ReturnType<typeof listing>>;
const FILES: Folders = {
  '': listing(POOLS, null, [entry(DATA, 'directory', 0)]),
  [DATA]: listing(DATA, POOLS, [
    entry(FAMILY, 'directory', 0),
    entry(`${DATA}/Annual report 2026 - final version signed by both parties.pdf`, 'file', 2_516_582),
    entry(`${DATA}/IMG_20261107_101530_HDR_edited_by_the_photo_editor.jpg`, 'file', 7_340_032),
    entry(`${DATA}/nas-configuration-backup-2026-11-07.tar.gz`, 'file', 734_003_200),
  ]),
};
// A folder longer than any screen: 40 photos.
const PHOTOS = Array.from({ length: 40 }, (_, i) =>
  entry(`${DATA}/IMG_20261107_${String(i + 1).padStart(4, '0')}.jpg`, 'file', 4_194_304),
);
const LONG_FOLDER: Folders = { '': FILES[''], [DATA]: listing(DATA, POOLS, PHOTOS) };

/** Answer the Files page's listings from `folders`, by the path asked for ('' for the pools folder). */
function fakeFiles(page: Page, folders: Folders): Promise<void> {
  return page.route(
    (url) => url.pathname === '/api/v1/files/list',
    (route) => {
      const request = route.request();
      const answer = folders[new URL(request.url()).searchParams.get('path') ?? ''];
      return request.method() === 'GET' && answer ? route.fulfill({ json: answer }) : route.abort();
    },
  );
}
/** What the supervisor's check of leftovers of uploads found (ADR-0354): one deep in a folder with a long name. */
const LEFTOVERS = {
  files: [
    { path: `${FAMILY}/Holiday photos 2025 (edited and sorted)/.unos-upload-0123456789abcdef`, size: 734_003_200,
      modified: '2026-11-07T10:15:30+00:00' },
    { path: `${DATA}/.unos-upload-fedcba9876543210`, size: 2_516_582, modified: '2026-11-07T10:15:31+00:00' },
  ],
  checked: '2026-11-07T10:25:30+00:00',
  checking: false,
  problems: [],
};
/** Answer the Files page's listings from FILES, and its check of leftovers of uploads with LEFTOVERS. */
async function fakeFilesWithLeftovers(page: Page): Promise<void> {
  await fakeFiles(page, FILES);
  await serve(page, { '/api/v1/files/upload-leftovers': LEFTOVERS });
}
/** The row of the File Browser's entry at `path`. */
const fileRow = (page: Page, path: string) =>
  page.locator('main div.grid').filter({ has: page.locator(`input[data-path="${path}"]`) });
/** A double-click on its row opens a folder: here e2e_data. */
const openData = (page: Page) => fileRow(page, DATA).dblclick();
const TRASH = {
  items: [
    {
      path: `${DATA}/.unos-trash/20261107T101530Z-1/Holiday photos 2025 (edited and sorted).zip`,
      name: 'Holiday photos 2025 (edited and sorted).zip',
      type: 'file',
      size: 734_003_200,
      original_path: `${FAMILY}/Holiday photos 2025 (edited and sorted).zip`,
      deleted: '2026-11-07T10:15:30+00:00',
      expires: '2026-12-07T10:15:30+00:00',
    },
  ],
  retention_days: 30,
  problems: [],
};

const SAMBA_FAILED_SIGN_IN =
  'Auth: [SMB2,(null)] user [WORKGROUP]\\[alice] at [Sun, 08 Nov 2026 09:58:02.123456 UTC] with [NTLMv2] ' +
  'status [NT_STATUS_WRONG_PASSWORD] workstation [LAPTOP-7] remote host [ipv4:192.168.1.50:50122] ' +
  'mapped to [WORKGROUP]\\[alice]. local host [ipv4:192.168.1.10:445] ';
const LOGS = {
  '/api/v1/logs/sources': {
    sources: [
      { name: 'journal', label: 'System Journal', description: "Everything in the host's systemd journal" },
      { name: 'samba', label: 'Samba', description: 'The SMB file server: smbd, and nmbd where NetBIOS is on' },
    ],
  },
  '/api/v1/logs/entries': {
    entries: [
      { timestamp: '2026-11-08T09:58:02+00:00', facility: '', priority: 'notice', message: SAMBA_FAILED_SIGN_IN, source: 'journal', unit: 'smbd' },
      {
        timestamp: '2026-11-08T09:50:00+00:00', facility: '', priority: 'warning', source: 'journal', unit: 'kernel',
        message: 'EXT4-fs warning (device sda1): ext4_dx_add_entry:2379: Directory index full!',
      },
    ],
    total: 2,
  },
};

const POWER = {
  '/api/v1/power/pending': { action: 'reboot', at: '2026-11-08T23:00:00+00:00' },
  '/api/v1/power/wol': {
    interfaces: [
      { name: 'enp1s0', mac_address: '02:42:ac:11:00:02', supported: true, wol: 'g', enabled: true },
      { name: 'enx00e04c680a1b', mac_address: '00:e0:4c:68:0a:1b', supported: false, wol: '', enabled: false },
    ],
  },
};

const RELEASE = {
  tag_name: 'v1.3.0',
  name: 'UNOS v1.3.0',
  body: 'Shares on every pool.\nA faster dashboard.',
  published_at: '2026-12-01T10:00:00Z',
  html_url: 'https://github.com/und3f1ned/unos/releases/tag/v1.3.0',
  prerelease: false,
  assets: [],
  checksum_sha256: null,
};
const UPDATES = {
  '/api/v1/updates/current': { version: 'v1.2.0-3-g712fc3a', commit_hash: '712fc3a', install_date: null, is_development: false },
  '/api/v1/updates/check': {
    current_version: 'v1.2.0-3-g712fc3a',
    latest_version: 'v1.3.0',
    update_available: true,
    release_info: RELEASE,
    checked_at: '2026-12-02T08:00:00',
  },
};

const BACKUPS = {
  '/api/v1/backup/list': {
    backups: [
      { filename: 'unos_backup_20261107_020000.tar.gz.enc', size: 1_048_576, created: '2026-11-07T02:00:00', version: '1.0', encrypted: true },
      { filename: 'unos_backup_20261101_020000.tar.gz', size: 980_000, created: '2026-11-01T02:00:00', version: '1.0', encrypted: false },
    ],
  },
};

// The off-device copy to a cloud store (ADR-0337), as GET /offsite/settings, /status and /snapshots
// answer: the S3 form, a failed copy with restic's long message, a restore into a long folder and a
// snapshot of a folder whose path has nowhere to break.
const OFFSITE_SNAPSHOT_ID = '4f2a9c1e7b3d5a8f0e6c2b9d1a7f3e5c8b0d4a6f2e9c1b7d3a5f8e0c6b2d9a1f';
const OFFSITE = {
  '/api/v1/offsite/settings': {
    configured: true,
    settings: {
      kind: 's3',
      path: null,
      endpoint: 'https://s3.eu-central-003.backblazeb2.com',
      bucket: 'family-nas-in-the-living-room-copies',
      prefix: 'unos/nasinthelivingroomoftheoldhouse',
      region: 'eu-central-003',
      access_key_id: '003a1b2c3d4e5f6000000000001',
      sources: ['/mnt/pools/data', '/mnt/pools/media/FamilyPhotosAndVideosOfTheWholeFamily2026'],
      limit_upload_kib: 2000,
      limit_download_kib: 0,
      keep_daily: 7,
      keep_weekly: 4,
      keep_monthly: 12,
      check_percent: 2,
    },
    has_password: true,
    has_s3_secret_access_key: true,
    pools: [
      { name: 'data', path: '/mnt/pools/data' },
      { name: 'media', path: '/mnt/pools/media' },
    ],
    pools_base: '/mnt/pools',
    warnings: [],
  },
  '/api/v1/offsite/status': {
    running: null,
    last_copy: {
      started: '2026-11-11T02:00:00+00:00', ended: '2026-11-11T02:41:07+00:00', trigger: 'task', success: false,
      step: 'data',
      error: 'restic backup ended with exit status 1: Fatal: unable to save snapshot: Put "https://s3.eu-central-003.' +
        'backblazeb2.com/family-nas-in-the-living-room-copies/unos/data/4f/4f2a9c1e7b3d5a8f0e6c2b9d1a7f3e5c8b0d4a6f": ' +
        'dial tcp: lookup s3.eu-central-003.backblazeb2.com: no such host',
      summary: { files_new: 1204, files_changed: 37, files_unmodified: 1_048_576, bytes_added: 734_003_200,
        configuration_snapshot: '9d1a7f3e5c8b0d4a6f2e9c1b7d3a5f8e0c6b2d9a1f4f2a9c1e7b3d5a8f0e6c2b', data_snapshot: null },
      check: null,
      restore_test: null,
      note: null,
    },
    last_restore: {
      snapshot_id: OFFSITE_SNAPSHOT_ID, path: '/mnt/pools/media/FamilyPhotosAndVideosOfTheWholeFamily2026',
      folder: '/mnt/pools/media/restored-20261110-210512', started: '2026-11-10T21:05:12+00:00',
      ended: '2026-11-10T21:30:44+00:00', success: true, error: null,
    },
  },
  '/api/v1/offsite/snapshots': {
    snapshots: [
      { id: OFFSITE_SNAPSHOT_ID, short_id: '4f2a9c1e', time: '2026-11-10T02:01:12.123456789+00:00', kind: 'data',
        tags: ['unos-data'], paths: ['/mnt/pools/data', '/mnt/pools/media/FamilyPhotosAndVideosOfTheWholeFamily2026'],
        hostname: 'nas-living-room', size: 1_099_511_627_776, files: 1_048_613 },
      { id: '9d1a7f3e5c8b0d4a6f2e9c1b7d3a5f8e0c6b2d9a1f4f2a9c1e7b3d5a8f0e6c2b', short_id: '9d1a7f3e',
        time: '2026-11-10T02:00:03.5+00:00', kind: 'configuration', tags: ['unos-configuration'],
        paths: ['/unos-configuration.tar.gz'], hostname: 'nas-living-room', size: 1_048_576, files: 1 },
    ],
  },
};

const BACKUP_TASK = {
  id: 'task_e2e_backup',
  name: 'Nightly encrypted backup of the configuration',
  task_type: 'backup',
  enabled: true,
  frequency: 'daily',
  cron_expression: null,
  hour: 2,
  minute: 0,
  day_of_week: null,
  day_of_month: null,
  target_pool: null,
  target_disk: null,
  command: null,
  keep_count: 7,
  last_run: '2026-11-07T01:00:00.104071+00:00',
  next_run: '2026-11-08T02:00:00+01:00',
  last_status: 'success',
  has_backup_key: true,
};
const SCHEDULER = {
  '/api/v1/scheduler/tasks': [
    BACKUP_TASK,
    { ...BACKUP_TASK, id: 'task_e2e_smart', name: 'SMART test', task_type: 'smart_test', frequency: 'weekly',
      day_of_week: 0, keep_count: null, target_disk: '/dev/disk/by-id/ata-WDC_WD40EFPX-68C6CN0_WD-WX12D34E5678',
      last_run: null, last_status: null, has_backup_key: null },
  ],
  '/api/v1/scheduler/timezone': { timezone: 'Europe/Berlin' },
};

// As the server answers them (supervisor/src/security/schemas.py): the stand-in has no ufw and no
// certificate.
const SECURITY = {
  '/api/v1/security/firewall': {
    enabled: true,
    default_incoming: 'deny',
    default_outgoing: 'allow',
    rules: [
      { id: 'rule_1', name: 'SSH from the home network', action: 'allow', direction: 'in', protocol: 'tcp', port: '22',
        source: '192.168.1.0/24', destination: null, enabled: true, comment: null },
      { id: 'rule_2', name: 'SMB', action: 'allow', direction: 'in', protocol: 'tcp', port: '445',
        source: '2001:db8:1234:5678::/64', destination: null, enabled: true, comment: null },
    ],
  },
  '/api/v1/security/certificates/status': { served_by: 'caddy_local_ca' },
  '/api/v1/security/certificates': {
    certificates: [
      { id: 'custom_nas-living-room.home.arpa', type: 'self_signed', domain: 'nas-living-room.home.arpa',
        issuer: 'nas-living-room.home.arpa', valid_from: 'Nov  8 01:47:31 2026 GMT',
        valid_until: 'Nov  8 01:47:31 2027 GMT', is_valid: true, served: false, fingerprint: null },
    ],
    served_by: 'caddy_local_ca',
  },
};

// A new secret of two-factor authentication as POST /auth/totp/setup answers it: pyotp's
// random_base32() has 32 characters.
const TOTP_SECRET = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP';
const TOTP_SETUP = { secret: TOTP_SECRET, qr_uri: `otpauth://totp/UNOS:admin?secret=${TOTP_SECRET}&issuer=UNOS` };

// GET /security/login-history (LoginHistoryResponse of supervisor/src/security/schemas.py), with an
// IPv6 address and a long user name.
const LOGIN_HISTORY = {
  attempts: [
    {
      id: 2, username: 'bob_the_family_photographer', ip_address: '2001:db8:1234:5678:9abc:def0:1234:5678',
      user_agent: 'Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0', success: false,
      failure_reason: 'Incorrect username or password', timestamp: '2026-11-08T09:58:02Z',
    },
    {
      id: 1, username: 'admin', ip_address: '192.168.1.50',
      user_agent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      success: true, failure_reason: null, timestamp: '2026-11-08T09:50:00Z',
    },
  ],
  total: 2,
  failed_count: 1,
  success_count: 1,
};

const NO_SETTINGS = { email_config: null, telegram_config: null, webhook_config: null, discord_config: null, slack_config: null };
const CHANNELS: Channel[] = [
  {
    ...NO_SETTINGS, id: 'notif_mail', name: 'Mail to the administrators of the household', channel: 'email', enabled: true,
    events: [],
    email_config: {
      smtp_host: 'mail.example.com', smtp_port: 587, smtp_user: 'nas@example.com', smtp_password: 'mail-pass',
      smtp_tls: true, smtp_verify_tls: true, from_address: 'nas-living-room@example.com',
      to_addresses: ['administrators-of-the-household@example.com'],
    },
  },
  {
    ...NO_SETTINGS, id: 'notif_hook', name: 'Home Assistant', channel: 'webhook', enabled: true, events: ['disk_warning'],
    webhook_config: {
      url: 'https://homeassistant.home.arpa:8123/api/webhook/unos-nas-living-room-notifications', method: 'POST',
      headers: {}, body_template: '{title}: {message}', content_type: 'text/plain',
    },
  },
];

// One message of the stats stream (supervisor/src/websocket/events.py), with the processes of a
// NAS and their long names.
const STATS = {
  cpu: {
    percent: 12.5, per_core: [10, 15, 20, 5, 12, 18, 9, 11], load_1: 0.5, load_5: 0.4, load_15: 0.3, cores: 8, threads: 8,
    temperature: 45.8,
  },
  memory: {
    total: 8 * 1024 ** 3, used: 2.5 * 1024 ** 3, free: 5.5 * 1024 ** 3, available: 5.5 * 1024 ** 3, percent: 31.25,
    swap_total: 0, swap_used: 0, swap_percent: 0,
  },
  network: {
    bytes_sent: 0, bytes_recv: 0, bytes_sent_rate: 1024, bytes_recv_rate: 1536, packets_sent: 0, packets_recv: 0,
    interfaces: [],
  },
  disk_io: {
    read_bytes: 0, write_bytes: 0, read_rate: 512, write_rate: 3.25 * 1024 ** 2, read_count: 0, write_count: 0, disks: [],
  },
  processes: [
    { pid: 812, name: 'smbd', cpu_percent: 3.5, memory_percent: 0.5, memory_mb: 42, status: 'sleeping', username: 'root' },
    { pid: 913, name: 'containerd-shim-runc-v2', cpu_percent: 1.2, memory_percent: 0.3, memory_mb: 12, status: 'sleeping',
      username: 'root' },
    { pid: 1022, name: 'kworker/u8:2-events_unbound', cpu_percent: 0.4, memory_percent: 0, memory_mb: 0, status: 'idle',
      username: 'root' },
  ],
  timestamp: '2026-11-08T10:00:00',
};

/** Answer the stats stream with STATS once the page has authenticated it. */
async function fakeStatsStream(page: Page): Promise<void> {
  await page.routeWebSocket('**/ws/v1/events', (ws: WebSocketRoute) => {
    ws.onMessage(() => {
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      ws.send(JSON.stringify({ type: 'stats', data: STATS }));
    });
  });
}

// The problems of the health banner above every page of an administrator (supervisor/src/health/
// problems.py, ADR-0332), with a pool's name that has no place to break and a long task name.
const LONG_POOL = 'family_photos_and_videos_of_the_whole_household';
const PROBLEMS = [
  { key: `pool_degraded:${LONG_POOL}`, severity: 'error', kind: 'pool_degraded', subject: LONG_POOL,
    title: `Pool '${LONG_POOL}' is degraded`, detail: '1 device(s) of the pool are missing.',
    since: '2026-11-08T09:00:00+00:00' },
  { key: `pool_low_space:${LONG_POOL}`, severity: 'warning', kind: 'pool_low_space', subject: LONG_POOL,
    title: `Pool '${LONG_POOL}' is 93% full`, detail: '3.72 TB of the 4.00 TB of the pool are used.',
    since: '2026-11-08T09:00:00+00:00' },
  { key: `task_missed:${BACKUP_TASK.id}`, severity: 'warning', kind: 'task_missed', subject: BACKUP_TASK.name,
    title: `Task '${BACKUP_TASK.name}' missed its run`, detail: 'It did not run at 2026-11-08 02:00 CET.',
    since: '2026-11-08T09:00:00+00:00' },
];

/** Answer the stats stream with the problems and STATS once the page has authenticated it. */
async function fakeProblemsStream(page: Page): Promise<void> {
  await page.routeWebSocket('**/ws/v1/events', (ws: WebSocketRoute) => {
    ws.onMessage(() => {
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      ws.send(JSON.stringify({ type: 'problems', data: PROBLEMS }));
      ws.send(JSON.stringify({ type: 'stats', data: STATS }));
    });
  });
}

const USERS = [
  fakeUser(1, 'admin', 'unavailable', { is_admin: true }),
  fakeUser(2, 'alice', 'active', { totp_enabled: true }),
  fakeUser(3, 'bob_the_family_photographer', 'password_needed'),
  fakeUser(4, 'carol', 'off'),
];
const SHARES = [
  fakeShare('family_photos_and_videos', {
    comment: 'Every photo and video of the family since 2019, sorted by year',
    access: accessOf(USERS, [
      { user_id: 2, username: 'alice', level: 'write' },
      { user_id: 3, username: 'bob_the_family_photographer', level: 'read' },
    ]),
  }),
  fakeShare('documents', { access: accessOf(USERS, [{ user_id: 2, username: 'alice', level: 'write' }]) }),
];

/** A page or a part of it, opened on the phone. */
interface View {
  name: string;
  path: string;
  /** Route fakes for what the stand-in lacks. */
  fake?: (page: Page) => Promise<unknown>;
  /** What to do on the page before it is checked, such as choosing a tab. */
  open?: (page: Page) => Promise<void>;
  /** Shown once the page has its data; names from the fakes, so that it holds in every language. */
  shows: (page: Page) => Locator;
  /** Shown whole as the view opens, before any scrolling: a dialog's title. */
  start?: (page: Page) => Locator;
  /** At the end of the view, shown whole once scrolled to: a dialog's last button, a list's last entry. */
  end?: (page: Page) => Locator;
}

/** The title and the last button of a dialog that is named one (role dialog), as Modal's are. */
const dialogTitle = (page: Page) => page.getByRole('dialog').getByRole('heading', { name: /\S/ }).first();
const dialogEnd = (page: Page) => page.getByRole('dialog').getByRole('button').last();

const LONG_FOLDER_VIEW: View = {
  name: 'Files: a long folder',
  path: '/files',
  fake: (page) => fakeFiles(page, LONG_FOLDER),
  open: openData,
  shows: (page) => fileRow(page, PHOTOS[0].path),
  end: (page) => fileRow(page, PHOTOS[PHOTOS.length - 1].path),
};

const VIEWS: View[] = [
  {
    name: 'Dashboard',
    path: '/',
    fake: async (page) => {
      await fakeStorageApi(page);
      await fakeHealthApi(page, { predictions: () => predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2) });
      await serve(page, { ...DOCKER, '/api/v1/system/info': SYSTEM_INFO });
    },
    shows: (page) => page.getByText('e2e-immich-machine-learning'),
  },
  {
    // A long host name with no place to break beside the live stats' time and Refresh (on the
    // stand-in, the workstation's name reached past the screen this way): the header wraps.
    name: 'Dashboard with a long host name',
    path: '/',
    fake: async (page) => {
      await fakeStorageApi(page);
      await fakeHealthApi(page, { predictions: () => predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2) });
      await serve(page, {
        ...DOCKER,
        '/api/v1/system/info': { ...SYSTEM_INFO, hostname: LONG_HOST_NAME },
      });
    },
    // The name and the time come in apart: the page lists the host after all its requests, and
    // the time with the first live stats.
    open: (page) => expect(page.getByRole('heading', { name: LONG_HOST_NAME })).toBeVisible(),
    shows: (page) => page.getByText(/(Updated|Обновлено) \d/),
  },
  {
    // The banner shows above every page of an administrator: here above the dashboard.
    name: 'The health banner',
    path: '/',
    fake: async (page) => {
      await fakeStorageApi(page);
      await fakeHealthApi(page, { predictions: () => predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2) });
      await serve(page, { ...DOCKER, '/api/v1/system/info': SYSTEM_INFO });
      await fakeProblemsStream(page);
    },
    shows: (page) =>
      page.getByRole('region', { name: /^(3 problems need attention|3 проблемы требуют внимания)$/ }),
  },
  {
    name: 'Storage',
    path: '/storage',
    fake: (page) => fakeStorageApi(page, { importable: FAKE_IMPORTABLE }),
    shows: (page) => page.getByText('e2e_frozen').first(),
  },
  {
    // A pool card with a balance in progress: its buttons wrap, its progress and time left (ADR-0379).
    name: 'Storage: a balance in progress',
    path: '/storage',
    fake: (page) =>
      fakeStorageApi(page, {
        balanceStatus: (pool) => ({
          ...noBalance(pool),
          ...(pool === 'e2e_media'
            ? { status: 'running', completed: 123, expected: 4567, considered: 130, seconds_left: 98765 }
            : {}),
        }),
      }),
    shows: (page) => page.getByText(/^(About 27 h 27 min left|Осталось около 27 ч 27 мин)$/),
  },
  {
    name: 'Snapshots',
    path: '/snapshots',
    fake: (page) => serve(page, SNAPSHOTS_API),
    shows: (page) => page.getByText(`${DATA}/restore_20261106_090000`).first(),
  },
  {
    name: 'Shares (SMB)',
    path: '/shares',
    fake: (page) => fakeSmbApi(page, { users: USERS, shares: SHARES }),
    shows: (page) => page.getByText('family_photos_and_videos').first(),
  },
  {
    name: 'Shares (NFS)',
    path: '/shares',
    fake: (page) =>
      fakeSmbApi(page, {
        users: USERS,
        shares: SHARES,
        nfsExports: [fakeNfsExport(FAMILY, '2001:db8:1234:5678::/64', 'read')],
      }),
    open: (page) => page.locator('#shares-tab-nfs').click(),
    shows: (page) => page.getByText('2001:db8:1234:5678::/64').first(),
  },
  {
    name: 'Files',
    path: '/files',
    fake: (page) => fakeFiles(page, FILES),
    open: openData,
    shows: (page) => page.getByText('Annual report 2026 - final version signed by both parties.pdf', { exact: true }),
  },
  {
    name: 'Files: New Folder',
    path: '/files',
    fake: (page) => fakeFiles(page, FILES),
    open: async (page) => {
      await openData(page);
      await page.getByTitle(/^(New Folder|Новая папка)$/).click();
    },
    shows: (page) => page.getByPlaceholder(/^(Folder name|Имя папки)$/),
  },
  LONG_FOLDER_VIEW,
  {
    // The notice of the hidden files that uploads left when the supervisor stopped (ADR-0354).
    name: 'Files: leftovers of uploads',
    path: '/files',
    fake: fakeFilesWithLeftovers,
    open: openData,
    shows: (page) => page.getByRole('region', { name: /^(Leftovers of interrupted uploads|Остатки прерванных загрузок)$/ }),
  },
  {
    name: 'Trash',
    path: '/files/trash',
    fake: (page) => serve(page, { '/api/v1/files/trash': TRASH }),
    shows: (page) => page.getByText('Holiday photos 2025 (edited and sorted).zip').first(),
  },
  {
    name: 'Containers',
    path: '/containers',
    fake: (page) => serve(page, DOCKER),
    shows: (page) => page.getByText('e2e-immich-machine-learning').first(),
  },
  {
    name: 'Containers (images)',
    path: '/containers',
    fake: (page) => serve(page, DOCKER),
    open: (page) => page.locator('#docker-tab-images').click(),
    shows: (page) => page.getByText('sha256:7a8b9c0d1e2f'),
  },
  {
    name: 'App Store',
    path: '/apps',
    fake: (page) => serve(page, { '/api/v1/apps/installed': INSTALLED_APPS }),
    shows: (page) => page.getByText('Jellyfin').first(),
  },
  {
    name: 'App Store (installed)',
    path: '/apps',
    fake: (page) => serve(page, { '/api/v1/apps/installed': INSTALLED_APPS }),
    open: (page) => page.getByRole('button', { name: /^(Installed|Установленные) \(1\)$/ }).click(),
    shows: (page) => page.getByText('e2e-jellyfin').first(),
  },
  {
    name: 'Users',
    path: '/users',
    fake: (page) => fakeSmbApi(page, { users: USERS }),
    shows: (page) => page.getByText('bob_the_family_photographer').first(),
  },
  {
    name: 'Audit Log',
    path: '/audit',
    shows: (page) => page.getByRole('listitem').filter({ hasText: 'admin' }).first(),
  },
  {
    name: 'System: General',
    path: '/system#general',
    fake: (page) => serve(page, { '/api/v1/system/info': SYSTEM_INFO, ...UPDATES }),
    shows: (page) => page.getByText('nas-living-room.home.arpa'),
  },
  {
    name: 'System: Updates',
    path: '/system#updates',
    fake: (page) => serve(page, UPDATES),
    shows: (page) => page.getByText('UNOS v1.3.0').first(),
  },
  {
    name: 'System: Security',
    path: '/system#security',
    fake: (page) => serve(page, SECURITY),
    shows: (page) => page.getByText('SSH from the home network'),
    open: async (page) => {
      // The firewall's rules and the certificates are shown on request.
      for (const name of [/^(Configure|Настроить)$/, /^(Manage|Управление)$/]) {
        await page.locator('main').getByRole('button', { name }).click();
      }
    },
  },
  {
    name: 'System: Security, the sign-in history',
    path: '/system#security',
    fake: (page) => serve(page, { ...SECURITY, '/api/v1/security/login-history': LOGIN_HISTORY }),
    open: (page) => page.locator('main').getByRole('button', { name: /^(View History|Показать историю)$/ }).click(),
    shows: (page) => page.getByText('2001:db8:1234:5678:9abc:def0:1234:5678'),
  },
  {
    name: 'System: Resource Monitor',
    path: '/system#monitor',
    fake: fakeStatsStream,
    shows: (page) => page.getByText('kworker/u8:2-events_unbound'),
  },
  {
    name: 'System: Logs',
    path: '/system#logs',
    fake: (page) => serve(page, LOGS),
    // The filters are open from the start.
    shows: (page) => page.getByText(/NT_STATUS_WRONG_PASSWORD/),
  },
  {
    name: 'System: Disk Health',
    path: '/system#health',
    fake: (page) => fakeHealthApi(page, { predictions: () => predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2) }),
    shows: (page) => page.getByText('E2E HDD 4TB').first(),
  },
  {
    name: 'System: Backup',
    path: '/system#backup',
    fake: (page) => serve(page, BACKUPS),
    shows: (page) => page.getByText('unos_backup_20261107_020000.tar.gz.enc'),
  },
  {
    name: 'System: Backup, an off-device copy to a cloud store',
    path: '/system#backup',
    fake: (page) => serve(page, { ...BACKUPS, ...OFFSITE, ...SCHEDULER }),
    // A snapshot's Restore files opens its form, with the pool and the folder.
    open: (page) => page.getByRole('button', { name: /^(Restore files|Восстановить файлы)$/ }).click(),
    shows: (page) => page.getByText('/mnt/pools/media/restored-20261110-210512'),
    end: (page) => page.getByRole('button', { name: /^(Forget this target|Забыть это хранилище)$/ }),
  },
  {
    name: 'System: Backup, a download that failed',
    path: '/system#backup',
    fake: async (page) => {
      await serve(page, BACKUPS);
      // As the server answers for a backup that has gone meanwhile.
      await page.route(
        (url) => url.pathname.startsWith('/api/v1/backup/download/'),
        (route) => route.fulfill({ status: 404, json: { detail: 'Backup not found' } }),
      );
    },
    // The error names the backup, whose long name has nowhere to break.
    open: (page) =>
      page.getByRole('button', { name: /^(Download|Скачать) unos_backup_20261107_020000\.tar\.gz\.enc$/ }).click(),
    shows: (page) => page.getByText('Backup not found'),
  },
  {
    name: 'System: Scheduler',
    path: '/system#scheduler',
    fake: async (page) => {
      await fakeStorageApi(page);
      await serve(page, SCHEDULER);
    },
    shows: (page) => page.getByText('Nightly encrypted backup of the configuration'),
  },
  {
    name: 'System: Notifications',
    path: '/system#notifications',
    fake: (page) => fakeNotificationsApi(page, CHANNELS),
    shows: (page) => page.getByText('Mail to the administrators of the household').first(),
  },
  {
    name: 'System: Network',
    path: '/system#network',
    fake: (page) => fakeNetworkApi(page),
    // The first interface, eth0, opened: its addresses and its button.
    open: (page) => page.locator('main [aria-expanded="false"]').first().click(),
    shows: (page) => page.getByText('2001:db8:1::10/64').first(),
  },
  {
    name: 'System: Network (routes)',
    path: '/system#network',
    fake: (page) => fakeNetworkApi(page),
    open: (page) => page.locator('main').getByRole('button', { name: /^(Routes|Маршруты)$/ }).click(),
    shows: (page) => page.getByText('2001:db8:1::/64').first(),
  },
  {
    name: 'System: Network (connections)',
    path: '/system#network',
    fake: (page) => fakeNetworkApi(page),
    open: (page) => page.locator('main').getByRole('button', { name: /^(Connections|Подключения)$/ }).click(),
    shows: (page) => page.getByText('192.168.1.20:51515'),
  },
  {
    name: 'System: Power',
    path: '/system#power',
    fake: (page) => serve(page, POWER),
    shows: (page) => page.getByText('enx00e04c680a1b').first(),
  },

  // The dialogs: they fit the width of the screen, and one taller than the screen scrolls, so
  // that its title shows as it opens and its last button once scrolled to its end.
  {
    name: 'Dialog: create a pool',
    path: '/storage',
    fake: (page) => fakeStorageApi(page),
    open: (page) => page.getByRole('button', { name: /^(Create Pool|Создать пул)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: "Dialog: a disk's idle timeout",
    path: '/storage',
    fake: (page) => fakeStorageApi(page),
    open: (page) =>
      page.getByRole('button', { name: /^(Idle timeout of|Тайм-аут простоя) \/dev\/e2e-hd1: / }).click(),
    shows: (page) => page.getByRole('dialog'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    // A full balance: its warning and its confirmation make the dialog tallest (ADR-0379).
    name: 'Dialog: balance a pool',
    path: '/storage',
    fake: (page) => fakeStorageApi(page),
    open: async (page) => {
      await page.getByRole('button', { name: /^(Balance|Балансировка)$/ }).first().click();
      await page.getByRole('dialog').getByRole('combobox').click();
      await page.getByRole('option', { name: /^(Every chunk \(full balance\)|Все блоки \(полная балансировка\))$/ }).click();
    },
    shows: (page) => page.getByRole('dialog').getByRole('checkbox'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: remove leftovers of uploads',
    path: '/files',
    fake: fakeFilesWithLeftovers,
    open: (page) => page.getByRole('button', { name: /^(Remove…|Удалить…)$/ }).click(),
    shows: (page) => page.getByRole('dialog').getByText(LEFTOVERS.files[0].path),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: create a snapshot',
    path: '/snapshots',
    fake: (page) => serve(page, SNAPSHOTS_API),
    open: (page) => page.getByRole('button', { name: /^(Create Snapshot|Создать снимок)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: delete a restored copy',
    path: '/snapshots',
    fake: (page) => serve(page, SNAPSHOTS_API),
    open: (page) =>
      page
        .getByRole('button', {
          name: /^(Delete restored copy|Удалить восстановленную копию) restore_20261107_103000$/,
        })
        .click(),
    shows: (page) => page.getByRole('dialog').getByText(`${DATA}/restore_20261107_103000`),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: add a share',
    path: '/shares',
    fake: (page) => fakeSmbApi(page, { users: USERS, shares: SHARES }),
    open: (page) => page.getByRole('button', { name: /^(Add SMB share|Добавить общую папку SMB)$/ }).click(),
    // Its list of users, each with the SMB state's badge.
    shows: (page) => page.getByRole('dialog').getByText('bob_the_family_photographer'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: create a container',
    path: '/containers',
    fake: (page) => serve(page, DOCKER),
    open: (page) => page.getByRole('button', { name: /^(Create Container|Создать контейнер)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: a container',
    path: '/containers',
    fake: (page) => serve(page, DOCKER),
    open: (page) => page.locator('tr', { hasText: 'e2e-immich-machine-learning' }).click(),
    shows: (page) => page.getByRole('dialog'),
    start: dialogTitle,
    // A drawer without buttons at its end: the container's image ends its Overview.
    end: (page) => page.getByRole('dialog').locator('code').last(),
  },
  {
    name: 'Dialog: an app of the catalog',
    path: '/apps',
    fake: (page) => serve(page, { '/api/v1/apps/installed': INSTALLED_APPS }),
    open: (page) => page.getByRole('button', { name: /Immich/ }).first().click(),
    shows: (page) => page.getByRole('dialog'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    // Its image, ghcr.io/blakeblackshear/frigate:stable, has no hyphen to break at.
    name: 'Dialog: an app with a long image name',
    path: '/apps',
    fake: (page) => serve(page, { '/api/v1/apps/installed': INSTALLED_APPS }),
    open: (page) => page.getByRole('button', { name: /Frigate NVR/ }).first().click(),
    shows: (page) => page.getByRole('dialog').getByText('ghcr.io/blakeblackshear/frigate:stable'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: add a user',
    path: '/users',
    fake: (page) => fakeSmbApi(page, { users: USERS }),
    open: (page) => page.getByRole('button', { name: /^(Add user|Добавить пользователя)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: SMART data of a disk',
    path: '/system#health',
    fake: (page) => fakeHealthApi(page),
    open: (page) => page.getByRole('button', { name: /\/dev\/e2e-sda$/ }).click(),
    shows: (page) => page.getByRole('dialog').getByText('Raw_Read_Error_Rate'),
    start: dialogTitle,
    // No buttons at its end: the table of attributes ends it, its first column at the left.
    end: (page) => page.getByRole('dialog').getByRole('row').last().getByRole('cell').first(),
  },
  {
    name: 'Dialog: restore a backup',
    path: '/system#backup',
    fake: (page) => serve(page, BACKUPS),
    open: (page) => page.getByRole('button', { name: /unos_backup_20261107_020000\.tar\.gz\.enc$/ }).first().click(),
    shows: (page) => page.getByRole('dialog'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: a new task',
    path: '/system#scheduler',
    fake: async (page) => {
      await fakeStorageApi(page);
      await serve(page, SCHEDULER);
    },
    open: (page) => page.getByRole('button', { name: /^(New Task|Новая задача)$/ }).click(),
    shows: (page) => page.locator('#task-name'),
    start: (page) => page.getByRole('heading', { name: /^(Create Task|Новая задача)$/ }),
    end: (page) => page.getByRole('button', { name: /^(Create Task|Создать задачу)$/ }),
  },
  {
    name: 'Dialog: a mail channel',
    path: '/system#notifications',
    fake: (page) => fakeNotificationsApi(page, CHANNELS),
    open: (page) => page.getByRole('button', { name: /^(Add Email|Добавить эл\. почту)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
    start: dialogTitle,
    end: dialogEnd,
  },
  // The dialogs of System > Security. Each comes last in the page, so its button is the last of
  // its name: the one that opened it has the same name.
  {
    name: 'Dialog: a firewall rule',
    path: '/system#security',
    fake: (page) => serve(page, SECURITY),
    open: async (page) => {
      await page.locator('main').getByRole('button', { name: /^(Configure|Настроить)$/ }).click();
      await page.locator('main').getByRole('button', { name: /^(Add Rule|Добавить правило)$/ }).click();
    },
    shows: (page) => page.getByRole('heading', { name: /^(Add Firewall Rule|Новое правило брандмауэра)$/ }),
    start: (page) => page.getByRole('heading', { name: /^(Add Firewall Rule|Новое правило брандмауэра)$/ }),
    end: (page) => page.getByRole('button', { name: /^(Add Rule|Добавить правило)$/ }).last(),
  },
  {
    name: 'Dialog: a certificate',
    path: '/system#security',
    fake: (page) => serve(page, SECURITY),
    open: async (page) => {
      await page.locator('main').getByRole('button', { name: /^(Manage|Управление)$/ }).click();
      await page
        .locator('main')
        .getByRole('button', { name: /^(Generate Self-Signed|Создать самоподписанный)$/ })
        .click();
    },
    shows: (page) =>
      page.getByRole('heading', { name: /^(Generate Self-Signed Certificate|Создание самоподписанного сертификата)$/ }),
    start: (page) =>
      page.getByRole('heading', { name: /^(Generate Self-Signed Certificate|Создание самоподписанного сертификата)$/ }),
    end: (page) => page.getByRole('button', { name: /^(Generate|Создать)$/ }).last(),
  },
  {
    name: 'Dialog: turn on two-factor authentication',
    path: '/system#security',
    fake: async (page) => {
      await serve(page, { ...SECURITY, '/api/v1/auth/totp/status': { enabled: false } });
      // The new secret, without making one on the stand-in.
      await page.route(
        (url) => url.pathname === '/api/v1/auth/totp/setup',
        (route) => (route.request().method() === 'POST' ? route.fulfill({ json: TOTP_SETUP }) : route.abort()),
      );
    },
    open: (page) => page.locator('main').getByRole('button', { name: /^(Enable|Включить)$/ }).click(),
    shows: (page) => page.getByText(TOTP_SECRET),
    start: (page) =>
      page.getByRole('heading', { name: /^(Setup Two-Factor Authentication|Настройка двухфакторной аутентификации)$/ }),
    end: (page) => page.getByRole('button', { name: /^(Enable 2FA|Включить 2FA)$/ }),
  },
  {
    name: 'Dialog: turn off two-factor authentication',
    path: '/system#security',
    fake: (page) =>
      serve(page, {
        ...SECURITY,
        '/api/v1/auth/totp/status': { enabled: true },
        '/api/v1/auth/recovery-codes/status': { remaining: 10, generated: true },
      }),
    open: (page) => page.locator('main').getByRole('button', { name: /^(Disable|Выключить)$/ }).click(),
    shows: (page) =>
      page.getByRole('heading', { name: /^(Disable Two-Factor Authentication|Выключение двухфакторной аутентификации)$/ }),
    start: (page) =>
      page.getByRole('heading', { name: /^(Disable Two-Factor Authentication|Выключение двухфакторной аутентификации)$/ }),
    end: (page) => page.getByRole('button', { name: /^(Disable 2FA|Выключить 2FA)$/ }),
  },
];

/**
 * Open `view` and check that it fits the window: nothing reaches out at the side (tooWide), an
 * open dialog's overlay scrolls no further than the dialog, the view's start shows whole as it
 * opens, and its end once scrolled to as a finger can.
 */
async function checkFits(page: Page, view: View): Promise<void> {
  await view.fake?.(page);
  await page.goto(view.path);
  await view.open?.(page);
  await expect(view.shows(page)).toBeVisible();
  expect(await tooWide(page)).toEqual([]);
  expect(await overscroll(page), 'px that the overlay scrolls past the dialog').toBeLessThanOrEqual(1);
  if (view.start) await expect(view.start(page), 'shown whole as it opens').toBeInViewport({ ratio: WHOLE });
  if (view.end) await expectReachable(view.end(page), 'shown whole once scrolled to');
}

for (const [language, locale] of [
  ['English', 'en-US'],
  ['Russian', 'ru-RU'],
] as const) {
  test.describe(`On a phone, in ${language}`, () => {
    // Without a language chosen in it, the web UI takes the browser's (ADR-0287).
    test.use({ viewport: PHONE, locale });

    for (const view of VIEWS) {
      test(`${view.name} fits the screen`, async ({ authenticatedPage: page }) => {
        await checkFits(page, view);
      });
    }

    test('the sign-in and first-run pages fit the width of the screen', async ({ page }) => {
      await page.goto('/login');
      await expect(page.locator('input[type="password"]')).toBeVisible();
      expect(await tooWide(page)).toEqual([]);
      // The first-run page sends nothing until its form is submitted.
      await page.goto('/setup');
      await expect(page.locator('#confirmPassword')).toBeVisible();
      expect(await tooWide(page)).toEqual([]);
    });
  });
}

/** The menu button of the header bar. */
const menuButton = (page: Page) => page.getByRole('button', { name: 'Menu' });
/** The links of the navigation. */
const navLink = (page: Page, href: string) => page.locator(`aside nav a[href="${href}"]`);

test.describe('The menu on a phone', () => {
  test.use({ viewport: PHONE });

  test('the navigation folds into a menu that the button opens and a click beside it closes', async ({
    authenticatedPage: page,
  }) => {
    await expect(menuButton(page)).toBeVisible();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false');
    // The button names the drawer it opens.
    const controls = await menuButton(page).getAttribute('aria-controls');
    expect(controls).toBeTruthy();
    const drawer = page.locator(`#${controls}`);
    await expect(drawer).toBeHidden();
    await expect(navLink(page, '/storage')).toBeHidden();
    expect(await tooWide(page)).toEqual([]);

    await menuButton(page).click();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'true');
    await expect(drawer).toBeVisible();
    for (const href of ['/', '/storage', '/snapshots', '/shares', '/files', '/containers', '/apps', '/users', '/audit', '/system']) {
      await expect(navLink(page, href)).toBeVisible();
    }
    // The signed-in user and Logout are in the drawer too.
    await expect(drawer.getByText('Administrator', { exact: true })).toBeVisible();
    await expect(drawer.getByRole('button', { name: 'Logout' })).toBeVisible();
    expect(await tooWide(page)).toEqual([]);

    // A click beside the drawer closes it, and reaches nothing of the page under it.
    await page.evaluate(() => {
      const counted = window as unknown as { clicksOnPage: number };
      counted.clicksOnPage = 0;
      document.querySelector('main')?.addEventListener('click', () => counted.clicksOnPage++);
    });
    await page.mouse.click(PHONE.width - 20, PHONE.height / 2);
    await expect(drawer).toBeHidden();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false');
    await expect(menuButton(page)).toBeFocused();
    expect(await page.evaluate(() => (window as unknown as { clicksOnPage: number }).clicksOnPage)).toBe(0);
    await expect(page).toHaveURL('/');

    // iOS Safari makes a tap a click only on an element that listens for clicks itself, or under
    // an ancestor below the body that does: a listener on the window does not count. So the dimmed
    // area beside the drawer listens itself, as Modal's backdrop does, and a click that stays on
    // the element under the finger (one that does not bubble) closes the menu as well.
    await menuButton(page).click();
    await navLink(page, '/').focus();
    await page.evaluate(
      ({ x, y }) => document.elementFromPoint(x, y)?.dispatchEvent(new MouseEvent('click', { bubbles: false })),
      { x: PHONE.width - 20, y: PHONE.height / 2 },
    );
    await expect(drawer).toBeHidden();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false');
    await expect(menuButton(page)).toBeFocused();

    // The button closes it too.
    await menuButton(page).click();
    await expect(drawer).toBeVisible();
    await menuButton(page).click();
    await expect(drawer).toBeHidden();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false');
  });

  test('the keyboard opens the menu, moves through its links, and Escape closes it', async ({
    authenticatedPage: page,
  }) => {
    await menuButton(page).focus();
    await page.keyboard.press('Enter');
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'true');
    await expect(navLink(page, '/')).toBeVisible();
    // The links come right after the button.
    await page.keyboard.press('Tab');
    await expect(navLink(page, '/')).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(navLink(page, '/storage')).toBeFocused();

    await page.keyboard.press('Escape');
    await expect(navLink(page, '/storage')).toBeHidden();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false');
    await expect(menuButton(page)).toBeFocused();

    // Space opens it as well; leaving it with Tab closes it, so that it never covers the focus.
    await page.keyboard.press(' ');
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'true');
    await page.locator('aside').getByRole('button', { name: 'Logout' }).focus();
    await page.keyboard.press('Tab');
    await expect(navLink(page, '/storage')).toBeHidden();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false');
  });

  test('choosing a link opens its page and closes the menu', async ({ authenticatedPage: page }) => {
    // System's General and Updates tabs, without asking the internet for a release.
    await serve(page, { '/api/v1/system/info': SYSTEM_INFO, ...UPDATES });
    await menuButton(page).click();
    await navLink(page, '/storage').click();
    await expect(page).toHaveURL('/storage');
    await expect(page.getByRole('heading', { name: 'Storage', exact: true })).toBeVisible();
    await expect(navLink(page, '/storage')).toBeHidden();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false');

    // The open page is marked in the menu.
    await menuButton(page).click();
    await expect(navLink(page, '/storage')).toHaveClass(/\bnav-item-active\b/);
    // The link of the open page closes the menu as well.
    await navLink(page, '/storage').click();
    await expect(navLink(page, '/storage')).toBeHidden();

    // Back goes to the page before, with the menu closed.
    await menuButton(page).click();
    await navLink(page, '/system').click();
    await expect(page).toHaveURL('/system');
    await menuButton(page).click();
    await page.goBack();
    await expect(page).toHaveURL('/storage');
    await expect(navLink(page, '/system')).toBeHidden();

    // Back between two places of one page, here from System's #updates to #general, opens no
    // other page, so SvelteKit's afterNavigate does not run: the menu closes all the same.
    await page.goto('/system#general');
    const checkForUpdates = page.locator('main').getByRole('link', { name: 'Check for Updates' });
    await checkForUpdates.click();
    await expect(page).toHaveURL('/system#updates');
    await expect(checkForUpdates).toBeHidden();
    await menuButton(page).click();
    await expect(navLink(page, '/system')).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL('/system#general');
    await expect(checkForUpdates).toBeVisible();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false');
    await expect(navLink(page, '/system')).toBeHidden();
  });

  test.describe('a regular user', () => {
    const USER = { username: `e2e_phone_${Date.now().toString(36)}`, password: 'e2e-Phone-Password-1' };
    let account: Account | undefined;

    test.beforeAll(async ({ request }) => {
      account = await createAccount(request, USER);
    });

    test.afterAll(async ({ request }) => {
      await deleteAccount(request, account, USER.username);
    });

    test('finds the Dashboard and the Account page in the menu, and both fit the screen', async ({ page }) => {
      await page.goto('/login');
      await page.getByLabel('Username').fill(USER.username);
      await page.getByLabel('Password').fill(USER.password);
      await page.getByRole('button', { name: 'Sign In' }).click();
      await expect(page).toHaveURL('/');
      await expect(page.locator('main').getByText(USER.username)).toBeVisible();
      expect(await tooWide(page)).toEqual([]);

      await menuButton(page).click();
      await expect(page.locator('aside nav a')).toHaveText(['Dashboard', 'Account']);
      await expect(page.locator('aside').getByText('User', { exact: true })).toBeVisible();
      await navLink(page, '/system#security').click();
      await expect(page).toHaveURL('/system#security');
      await expect(page.getByRole('heading', { name: 'Security Settings' })).toBeVisible();
      await expect(navLink(page, '/system#security')).toBeHidden();
      expect(await tooWide(page)).toEqual([]);
    });
  });
});

// Held sideways a phone is still narrower than md, with only 375 px of height: a dialog taller
// than that scrolls, a long folder too, and the menu reaches its last link and Logout.
test.describe('On a phone held sideways', () => {
  test.use({ viewport: PHONE_SIDEWAYS });

  for (const view of VIEWS.filter((view) => view.end)) {
    test(`${view.name} fits the screen`, async ({ authenticatedPage: page }) => {
      await checkFits(page, view);
    });
  }

  test('the menu reaches its last link and Logout', async ({ authenticatedPage: page }) => {
    await menuButton(page).click();
    await expect(navLink(page, '/')).toBeInViewport({ ratio: WHOLE });
    await expectReachable(navLink(page, '/system'), 'the last link');
    await expectReachable(page.locator('#layout-sidebar').getByRole('button', { name: 'Logout' }), 'Logout');
  });
});

test.describe('From the md breakpoint up', () => {
  test.use({ viewport: { width: 768, height: 1024 } });

  test('the sidebar stays beside the page, without the header bar', async ({ authenticatedPage: page }) => {
    // Not even hidden: the page's first button is Logout, as before.
    await expect(menuButton(page)).toHaveCount(0);
    await expect(page.locator('button').first()).toBeVisible();
    await expect(navLink(page, '/storage')).toBeVisible();
    await expect(page.locator('aside').getByText('Administrator', { exact: true })).toBeVisible();
    await navLink(page, '/storage').click();
    await expect(page).toHaveURL('/storage');
    await expect(navLink(page, '/storage')).toBeVisible();
  });

  // From md the File Browser's list fills the rest of the window and scrolls inside it.
  test(`${LONG_FOLDER_VIEW.name} fits the window`, async ({ authenticatedPage: page }) => {
    await checkFits(page, LONG_FOLDER_VIEW);
  });

  test.describe('at the size of the other specs', () => {
    test.use({ viewport: DESKTOP });

    test(`${LONG_FOLDER_VIEW.name} fits the window`, async ({ authenticatedPage: page }) => {
      await checkFits(page, LONG_FOLDER_VIEW);
    });
  });
});
