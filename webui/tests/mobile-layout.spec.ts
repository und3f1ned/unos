import type { Locator, Page, WebSocketRoute } from '@playwright/test';
import { test, expect, E2E_ADMIN } from './fixtures';
import { apiLogin, createAccount, deleteAccount, type Account } from './accounts';
import { FAKE_IMPORTABLE, fakeStorageApi, fakeVolumesWithUse, noBalance, noRemoval } from './storage-fakes';
import {
  NO_SNAPSHOTS,
  POOLS,
  accessOf,
  fakeGroup,
  fakeNfsExport,
  fakeShare,
  fakeSmbApi,
  fakeUser,
  groupAccessOf,
} from './shares-fakes';
import { HEALTHY_HDD_2, REPLACE_SOON, fakeHealthApi, predictionsAnswer } from './health-fakes';
import { fakeNetworkApi } from './network-fakes';
import { fakeIscsiApi, fakeTarget } from './iscsi-fakes';
import { fakeNotificationsApi, type Channel } from './notifications-fakes';
import { installedImmich } from './apps-fakes';

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

// Audit entries whose values have no place to break, as long as the server takes and keeps them:
// an API token's name of 64 characters (API_TOKEN_NAME_CHARS of supervisor/src/auth/schemas.py),
// a name tried at sign-in of 128 (MAX_USERNAME_CHARS of supervisor/src/audit/router.py) and a
// client's full IPv6 address. As supervisor/src/auth/router.py and log_action write them
// (ADR-0189): the token's name in the resource and in the details, the browser, and the hash chain.
const LONG_TOKEN_NAME = 'nightlycopyofthefamilyphotosandvideostotheoffsitediskinthegarage';
const LONG_SIGN_IN_NAME = 'administratorofthenasinthelivingroomoftheoldhouseatthecornerhome'.repeat(2);
const LONG_ADDRESS = 'fd7a:115c:a1e0:4b2d:8c3f:9e61:d5a8:7b19';
/** The n-th entry of the chain (1 the oldest), written at 09:4n:12 of the server's day. */
const auditEntry = (n: number, entry: Record<string, unknown>) => ({
  id: `audit_20261117094${n}12345678`,
  timestamp: `2026-11-17T09:4${n}:12.345678+00:00`,
  severity: 'info',
  ip_address: LONG_ADDRESS,
  user_agent: 'Mozilla/5.0 (Android 15; Mobile; rv:144.0) Gecko/144.0 Firefox/144.0',
  resource_type: null,
  resource_id: null,
  details: null,
  success: true,
  prev_hash: String(n - 1).repeat(64),
  hash: String(n).repeat(64),
  chain: 'ok',
  ...entry,
});
const LONG_AUDIT_ENTRIES = [
  auditEntry(3, {
    action: 'other',
    username: 'admin',
    resource_type: 'api_token',
    resource_id: LONG_TOKEN_NAME,
    details: `Deleted API token: ${LONG_TOKEN_NAME}`,
  }),
  auditEntry(2, {
    action: 'other',
    username: 'admin',
    resource_type: 'api_token',
    resource_id: LONG_TOKEN_NAME,
    details: `Created API token: ${LONG_TOKEN_NAME}`,
  }),
  auditEntry(1, {
    action: 'login_failed',
    severity: 'warning',
    username: LONG_SIGN_IN_NAME,
    details: 'Incorrect username or password',
    success: false,
  }),
];

const SYSTEM_INFO = {
  hostname: 'nas-living-room.home.arpa',
  kernel: '6.12.48+deb13-arm64',
  os: 'Debian GNU/Linux 13 (trixie)',
  architecture: 'aarch64',
  // When the host started: 3 days and 4 hours ago, in UTC with its offset.
  boot_time: new Date(Date.now() - (3 * 24 + 4) * 3600 * 1000).toISOString(),
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
// Docker's reason for a container that it cannot stop, as the containers API passes it on (409).
const CONTAINER_CONFLICT =
  'Cannot stop container 2b3c4d5e6f70: cannot kill container: 2b3c4d5e6f70a1b2c3d4e5f6: tried to kill container, but did not receive an exit event';

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

const INSTALLED_JELLYFIN = {
  id: 'e2e-jellyfin',
  template_id: 'jellyfin',
  name: 'e2e-jellyfin',
  container_id: 'e2e-jellyfin',
  status: 'running',
  web_port: 8096,
  created_at: '2026-11-01T10:00:00Z',
  pool: 'e2e_media',
  volumes: [
    { host: `${POOLS}/e2e_media/apps/jellyfin/config`, container: '/config', readonly: false, companion: null },
    { host: `${POOLS}/e2e_media/Movies and TV shows`, container: '/media', readonly: true, companion: null },
  ],
  data_outside_pools: [],
  runs_in_unos_smb: false,
  devices: [],
  health: null,
  companions: [],
  missing: [],
  // Its limits and its use against them, with bars (ADR-0390).
  resources: {
    memory_limit: 2 ** 30,
    cpu_limit: 2,
    memory_used: 700 * 2 ** 20,
    cpu_used: 0.4,
    sampled_at: '2026-11-17T10:00:00+00:00',
  },
  version: null,
  update_to: null,
  previous: null,
};
// An app of several containers whose machine learning stopped: its card names its containers and
// warns about the one that does not run (ADR-0381).
const INSTALLED_IMMICH = installedImmich(DATA, { companions: { 'machine-learning': { status: 'exited' } } });
const INSTALLED_APPS = [INSTALLED_JELLYFIN, INSTALLED_IMMICH];
// Immich with an update to the catalog's newer images, and what the update does, with long image
// names and folders (GET /apps/installed/{id}/update, ADR-0408).
const UPDATABLE_APPS = [INSTALLED_JELLYFIN, { ...INSTALLED_IMMICH, update_to: 'v3.5.0' }];
const UPDATE_PLAN = {
  template_id: 'immich', name: 'Immich', version_from: 'v3.4.1', version_to: 'v3.5.0', platform: 'linux/arm64',
  containers: INSTALLED_IMMICH.companions.map((c) => ({
    name: c.container, companion: c.name, image_from: c.image, image_to: `${c.image.split('@')[0]}:v3.5.0@sha256:${'c'.repeat(64)}`,
    changed: true, built_from: '2026-10-01T00:00:00Z', built_to: '2026-11-15T00:00:00Z',
  })),
  download_bytes: 900 * 2 ** 20, system_disk_bytes: 2 * 2 ** 30, system_disk_free_bytes: 20 * 2 ** 30,
  copies: [{
    container: 'immich-database', folder: `${DATA}/apps/immich/postgres/data`,
    copy_folder: `${DATA}/apps/immich/postgres/data.previous`, bytes: 2 ** 30, pool: 'e2e_data', pool_free_bytes: 2 ** 40,
  }],
};
// What the limits of a container may be on a NAS of 8 GiB and 8 CPUs (GET /apps/limits, ADR-0390).
const LIMIT_BOUNDS = { memory_min: 64 * 2 ** 20, memory_max: 8 * 2 ** 30, cpu_min: 0.1, cpu_max: 8 };

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
  '/api/v1/power/config': {
    wake_on_lan: true, auto_shutdown: true, shutdown_hour: 23, shutdown_minute: 0, auto_wakeup: true,
    wakeup_hour: 7, wakeup_minute: 0, idle_shutdown: true, idle_timeout_minutes: 45,
  },
  '/api/v1/power/schedule': {
    timezone: 'America/Argentina/ComodRivadavia',
    shutdown: {
      enabled: true, time: '23:00', next: '2026-11-09T02:00:00Z', state: 'postponed',
      held_by: [{ kind: 'scrub', subject: '/mnt/pools/archive-of-the-family-photographs', amount: null, detail: null }],
    },
    wake: { enabled: true, time: '07:00', next: null, rtc: { available: false, reason: 'The RTC rtc0 has no wake alarm: /sys/class/rtc/rtc0/wakealarm does not exist', alarm: null } },
    idle: {
      enabled: true, minutes: 45, idle: false, since: null, shutdown_at: null,
      found: [{ kind: 'smb_client', subject: 'fd12:3456:789a:1:2a0:c9ff:fe12:3456', amount: null, detail: null }],
    },
  },
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
    checked_at: '2026-12-02T08:00:00Z',
  },
};

const BACKUPS = {
  '/api/v1/backup/list': {
    backups: [
      { filename: 'unos_backup_20261107_020000.tar.gz.enc', size: 1_048_576, created: '2026-11-07T02:00:00Z', version: '1.0', encrypted: true },
      { filename: 'unos_backup_20261101_020000.tar.gz', size: 980_000, created: '2026-11-01T02:00:00Z', version: '1.0', encrypted: false },
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

// As the server answers them (supervisor/src/security/schemas.py): the stand-in has no ufw, and a
// stored certificate that is chosen but that the web server did not load (ADR-0404), so that the card
// shows its warning and Use Caddy's local CA.
const CERTIFICATE_STATUS = {
  served_by: 'caddy_local_ca', served_id: null, chosen_id: 'custom_nas-living-room.home.arpa', fingerprint: null,
};
const SECURITY = {
  // The host's ufw with a change that waits to be kept (ADR-0416): the countdown and Keep show too.
  '/api/v1/security/firewall': {
    installed: true,
    enabled: true,
    default_incoming: 'deny',
    default_outgoing: 'allow',
    ipv6: true,
    revert_in: 90,
    revert_seconds: 120,
    rules: [
      { id: 'fw-000000000001', number: 1, family: 'ipv4', action: 'allow', direction: 'in', route: false, log: false,
        protocol: 'tcp', port: '22,80,443,445,5357', destination: null, source_port: null, source: '192.168.0.0/16',
        interface: null, app: null, comment: 'UNOS: web UI, SSH and SMB', unos: true },
      { id: 'fw-000000000002', number: 2, family: 'ipv4', action: 'allow', direction: 'in', route: false, log: false,
        protocol: 'tcp', port: '22', destination: null, source_port: null, source: '192.168.1.0/24', interface: null,
        app: null, comment: 'SSH from the home network', unos: false },
      { id: 'fw-000000000003', number: 3, family: 'ipv6', action: 'allow', direction: 'in', route: false, log: false,
        protocol: 'tcp', port: '445', destination: null, source_port: null, source: '2001:db8:1234:5678::/64',
        interface: null, app: null, comment: 'SMB', unos: false },
    ],
  },
  '/api/v1/security/certificates/status': CERTIFICATE_STATUS,
  '/api/v1/security/certificates': {
    certificates: [
      { id: 'custom_nas-living-room.home.arpa', type: 'self_signed', domain: 'nas-living-room.home.arpa',
        issuer: 'nas-living-room.home.arpa', valid_from: '2026-11-08T01:47:31Z',
        valid_until: '2027-11-08T01:47:31Z', is_valid: true, expires_soon: false, served: false, chosen: true,
        fingerprint: null },
      { id: 'custom_backup-of-the-garage-nas.home.arpa', type: 'custom', domain: 'backup-of-the-garage-nas.home.arpa',
        issuer: 'Let us encrypt the garage', valid_from: '2026-11-08T01:47:31Z',
        valid_until: '2026-11-28T01:47:31Z', is_valid: true, expires_soon: true, served: false, chosen: false,
        fingerprint: null },
    ],
    status: CERTIFICATE_STATUS,
  },
};

// The Account page's API tokens as GET /auth/tokens answers them (APITokenResponse of
// supervisor/src/auth/schemas.py, ADR-0392): a long name with many areas, a read-only token of
// every area that is disabled and expired, and a scope that cannot be read. Times in UTC without a zone.
const EVERY_AREA = [
  'apps', 'audit', 'auth', 'backup', 'containers', 'files', 'groups', 'health', 'logs', 'monitor', 'network',
  'notifications', 'offsite', 'power', 'scheduler', 'security', 'shares', 'snapshots', 'storage', 'system', 'updates',
];
// The security keys of an account with two-factor authentication (SecurityKeyInfo of
// supervisor/src/auth/security_keys.py, ADR-0409): a name of 64 characters (NAME_CHARS) with no place
// to break, and the address of a long host name where it works.
const LONG_KEY_NAME = 'yubikeyfivenfconthekeyringofthefamilyintheoldhouseatthecorner064';
const SECURITY_KEYS = {
  ...SECURITY,
  '/api/v1/auth/totp/status': { enabled: true, required: false },
  '/api/v1/auth/recovery-codes/status': { remaining: 10, generated: true },
  '/api/v1/auth/security-keys': [
    { id: 1, name: LONG_KEY_NAME, origin: `https://${LONG_HOST_NAME}:8443`, created_at: '2026-11-01T09:00:00Z',
      last_used_at: '2026-11-16T03:00:00Z', usable: true },
    { id: 2, name: 'Spare key', origin: null, created_at: null, last_used_at: null, usable: false },
  ],
};

const API_TOKENS = {
  ...SECURITY,
  '/api/v1/auth/tokens': [
    { id: 1, name: 'nightly_backup_of_the_family_photos_to_the_garage_nas', token_prefix: 'unos_AbCdEfG',
      is_active: true, read_only: false, areas: EVERY_AREA.slice(0, 20), scope_known: true,
      created_at: '2026-11-01T09:00:00Z', last_used_at: '2026-11-16T03:00:00Z', expires_at: '2027-11-01T09:00:00Z' },
    { id: 2, name: 'home-assistant', token_prefix: 'unos_IjKlMnO', is_active: false, read_only: true, areas: null,
      scope_known: true, created_at: '2026-01-01T09:00:00Z', last_used_at: null, expires_at: '2026-02-01T09:00:00Z' },
    { id: 3, name: 'restored_from_another_installation', token_prefix: 'unos_QrStUvW', is_active: true,
      read_only: true, areas: [], scope_known: false, created_at: '2026-10-01T09:00:00Z', last_used_at: null,
      expires_at: null },
  ],
};
// POST /auth/tokens answers the new token once: 48 characters without a place to break.
const NEW_API_TOKEN = {
  id: 4, name: 'nightly_backup_of_the_family_photos_to_the_garage_nas', token: `unos_${'x'.repeat(43)}`,
  token_prefix: 'unos_xxxxxxx', read_only: false, areas: EVERY_AREA.slice(0, 20), expires_at: null,
};
const tokensRegion = (page: Page) => page.getByRole('region', { name: /^(API Tokens|API-токены)$/ });
/** The dialog of a new token, with every area listed and the warning of the areas whose changes reach beyond them. */
async function openNewTokenDialog(page: Page): Promise<void> {
  await tokensRegion(page).getByRole('button', { name: /^(New Token|Новый токен)$/ }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('radio', { name: /^(Read and change|Чтение и изменение)/ }).check();
  await dialog.getByRole('radio', { name: /^(Only these areas|Только эти области)/ }).check();
  await dialog.getByRole('checkbox', { name: /^(Containers|Контейнеры)/ }).check();
  // Back up to the title, which the ticks scrolled away on a phone held sideways.
  await dialog.getByRole('heading').first().scrollIntoViewIfNeeded();
}

// A new secret of two-factor authentication as POST /auth/totp/setup answers it: pyotp's
// random_base32() has 32 characters.
const TOTP_SECRET = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP';
const TOTP_SETUP = { secret: TOTP_SECRET, qr_uri: `otpauth://totp/UNOS:admin?secret=${TOTP_SECRET}&issuer=UNOS` };

// Two-factor authentication for every account: the administrator's card with the
// requirement on, and the signed-in account as GET /auth/me answers it while it must set it up.
const TWO_FACTOR_REQUIRED = {
  ...SECURITY,
  '/api/v1/auth/totp/status': { enabled: true, required: true },
  '/api/v1/auth/recovery-codes/status': { remaining: 10, generated: true },
  '/api/v1/security/two-factor-requirement': {
    enabled: true, grace_days: 14, enabled_at: '2026-11-10T09:00:00Z', enabled_by: 'administrator_of_the_household',
  },
};
function meToSetUp(setupOnly: boolean) {
  return {
    '/api/v1/auth/me': {
      id: 1, username: 'admin', is_admin: true, totp_enabled: false, created_at: '2026-11-01T09:00:00Z',
      two_factor_required: { by: '2026-11-24T09:00:00Z', setup_only: setupOnly },
    },
  };
}

/** The setup page of an account that must set up two-factor authentication, with a new secret. */
async function fakeTwoFactorSetup(page: Page, setupOnly: boolean): Promise<void> {
  await serve(page, meToSetUp(setupOnly));
  await page.route(
    (url) => url.pathname === '/api/v1/auth/totp/setup',
    (route) => (route.request().method() === 'POST' ? route.fulfill({ json: TOTP_SETUP }) : route.abort()),
  );
}

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
    bytes_sent_rate: 1024, bytes_recv_rate: 1536,
    interfaces: [{ name: 'eth0', bytes_sent: 4096, bytes_recv: 8192, bytes_sent_rate: 1024, bytes_recv_rate: 1536 }],
  },
  disk_io: { read_rate: 512, write_rate: 3.25 * 1024 ** 2, disks: [{ name: 'sda' }] },
  // The host's processes (ADR-0399), with a user of a long name, as an account of the host can have.
  processes: [
    { pid: 812, name: 'smbd', cpu_percent: 3.5, memory_bytes: 42 * 1024 ** 2, username: 'root' },
    { pid: 913, name: 'containerd-shim-runc-v2', cpu_percent: 1.2, memory_bytes: 12 * 1024 ** 2, username: 'root' },
    { pid: 1022, name: 'kworker/u8:2-events_unbound', cpu_percent: 0.4, memory_bytes: 0, username: 'root' },
    { pid: 4194304, name: 'immich-microservices', cpu_percent: 142.5, memory_bytes: 1.5 * 1024 ** 3,
      username: 'bob_the_family_photographer' },
  ],
  timestamp: '2026-11-08T10:00:00+00:00',
};

// The Resource Monitor's history (supervisor/src/monitor/router.py, ADR-0399): an hour of points.
const MONITOR_HISTORY = {
  interval_seconds: 10,
  points: Array.from({ length: 360 }, (_, i) => ({
    age_seconds: 3600 - i * 10, cpu_percent: (i * 7) % 100, memory_percent: 40 + (i % 20),
    net_recv_rate: i * 1024, net_sent_rate: (360 - i) * 1024, disk_read_rate: i * 4096, disk_write_rate: 2 * 1024 ** 3,
  })),
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
    // The badges of every protocol option (ADR-0380) wrap on a phone, and so does the reason of
    // one that smb.conf leaves out, with its long path.
    encryption_required: true,
    time_machine: true,
    previous_versions: true,
    not_rendered: { previous_versions: NO_SNAPSHOTS },
    access: accessOf(USERS, [
      { user_id: 2, username: 'alice', level: 'write' },
      { user_id: 3, username: 'bob_the_family_photographer', level: 'read' },
    ]),
  }),
  fakeShare('documents', {
    access: accessOf(USERS, [{ user_id: 2, username: 'alice', level: 'write' }]),
    // A group's line with its members wraps on a phone.
    group_access: groupAccessOf(
      [fakeGroup(1, 'family_and_friends_of_the_house', USERS, [2, 3])],
      [{ group: 'family_and_friends_of_the_house', level: 'read' }],
    ),
  }),
];

/** A page or a part of it, opened on the phone. */
// Shares > iSCSI: a target with a long name, two initiators of which one is logged in.
const ISCSI_TARGET = fakeTarget('family-backups-of-every-laptop', {
  initiators: ['iqn.1991-05.com.microsoft:desktop-of-the-family-living-room', 'iqn.1993-08.org.debian:01:5f3c0a1b2c3d'],
  sessions: ['iqn.1991-05.com.microsoft:desktop-of-the-family-living-room'],
  size_gib: 2048,
});

async function fakeIscsiTab(page: Page) {
  await fakeSmbApi(page, { users: USERS, shares: SHARES });
  await fakeIscsiApi(page, { targets: [ISCSI_TARGET] });
}

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

/**
 * Wait until `target` has ended its own transitions, such as the 150 ms in which Select's list
 * flies in, as a finger taps an option where it rests. Playwright's click on an element that
 * still moves tries again, and the try that finds it at rest scrolls it, by the try's number, to
 * the end, the center or the start of each box that scrolls it: in a dialog taller than the
 * screen the overlay is left scrolled down, its title out of view, after the list has closed.
 */
async function settled(target: Locator): Promise<void> {
  await expect
    .poll(() => target.evaluate((element) => element.getAnimations().length), { message: 'its transitions have ended' })
    .toBe(0);
}

const LONG_FOLDER_VIEW: View = {
  name: 'Files: a long folder',
  path: '/files',
  fake: (page) => fakeFiles(page, LONG_FOLDER),
  open: openData,
  shows: (page) => fileRow(page, PHOTOS[0].path),
  end: (page) => fileRow(page, PHOTOS[PHOTOS.length - 1].path),
};

/**
 * The dashboard while its widgets are arranged (webui.dashboard-layout, ADR-0395): every widget has
 * its toolbar of a handle and three buttons, two cards are hidden and listed under Hidden widgets
 * with a button each to show them. The layout is the administrator's, kept on the server, so the
 * test that opens this view puts it back (`resetLayout`).
 */
const ARRANGING_VIEW: View = {
  name: 'Dashboard: arranging the widgets',
  path: '/',
  fake: async (page) => {
    await fakeStorageApi(page);
    await fakeHealthApi(page, { predictions: () => predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2) });
    await serve(page, { ...DOCKER, '/api/v1/system/info': SYSTEM_INFO });
  },
  open: async (page) => {
    await page.getByRole('button', { name: /^(Customize|Настроить)$/ }).click();
    // The toolbar's last button hides the widget.
    for (const id of ['disk-io', 'network']) await page.locator(`main [data-widget="${id}"] [data-toolbar] button`).last().click();
    await expect(page.locator('main [data-widget="network"]')).toHaveCount(0);
  },
  shows: (page) => page.getByRole('region', { name: /^(Hidden widgets|Скрытые виджеты)$/ }).getByRole('button').first(),
  end: (page) => page.getByText('e2e-immich-machine-learning'),
};

async function resetLayout(page: Page) {
  const token = await apiLogin(page.request, E2E_ADMIN);
  await page.request.delete('/api/v1/auth/me/dashboard-layout', { headers: { Authorization: `Bearer ${token}` } });
}

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
    // A pool card with a disk removal in progress: its long path, its progress and the bytes left (ADR-0411).
    name: 'Storage: a disk removal in progress',
    path: '/storage',
    fake: (page) =>
      fakeStorageApi(page, {
        removalStatus: (pool) => ({
          ...noRemoval(pool),
          ...(pool === 'e2e_unread'
            ? { status: 'running', devid: 2, device: '/dev/disk-with-a-rather-long-name-e2e6', left: 3e11, total: 9e11 }
            : {}),
        }),
      }),
    shows: (page) => page.getByRole('progressbar', { name: /^(Removal progress|Ход удаления)$/ }),
  },
  {
    name: 'Snapshots',
    path: '/snapshots',
    fake: (page) => serve(page, SNAPSHOTS_API),
    shows: (page) => page.getByText(`${DATA}/restore_20261106_090000`).first(),
  },
  {
    // With the card of the network interfaces: smbd's long IPv6 addresses, and a chosen USB adapter
    // that is not on the host (ADR-0391).
    name: 'Shares (SMB)',
    path: '/shares',
    fake: (page) =>
      fakeSmbApi(page, { users: USERS, shares: SHARES, interfaces: { chosen: ['enP4p65s0', 'enx00e04c680001'] } }),
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
    // A target with a long IQN, two initiators and a session (ADR-0417).
    name: 'Shares (iSCSI)',
    path: '/shares',
    fake: fakeIscsiTab,
    open: (page) => page.locator('#shares-tab-iscsi').click(),
    shows: (page) => page.getByText(ISCSI_TARGET.iqn),
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
    // Several containers selected, and the results of an action on them, one line each with a long
    // name or a long reason (ADR-0389): the selection's bar and the lines wrap.
    name: 'Containers: an action on several',
    path: '/containers',
    fake: async (page) => {
      await serve(page, DOCKER);
      await page.route(
        (url) => /^\/api\/v1\/containers\/[^/]+\/stop$/.test(url.pathname),
        (route) => {
          const id = new URL(route.request().url()).pathname.split('/')[4];
          const found = CONTAINERS.find((c) => c.id === id)!;
          return found.name === 'e2e-cache'
            ? route.fulfill({ status: 409, json: { detail: CONTAINER_CONFLICT } })
            : route.fulfill({ json: { ...found, state: 'exited', status: 'exited' } });
        },
      );
    },
    open: async (page) => {
      await page.getByRole('checkbox', { name: /^(Select all shown|Выбрать все показанные)$/ }).check();
      await page.getByRole('button', { name: /^(Stop selected|Остановить выбранные)$/ }).click();
    },
    shows: (page) => page.getByRole('listitem').filter({ hasText: CONTAINER_CONFLICT }),
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
    open: (page) => page.getByRole('button', { name: /^(Installed|Установленные) \(2\)$/ }).click(),
    shows: (page) => page.getByText('immich-machine-learning').first(),
  },
  {
    name: 'Users',
    path: '/users',
    // With a group whose members and shares wrap.
    fake: (page) => fakeSmbApi(page, { users: USERS, groups: [fakeGroup(1, 'family_and_friends', USERS, [2, 3])] }),
    shows: (page) => page.getByText('bob_the_family_photographer').first(),
  },
  {
    name: 'Audit Log',
    path: '/audit',
    shows: (page) => page.getByRole('listitem').filter({ hasText: 'admin' }).first(),
  },
  {
    // An API token's name of 64 characters, a name tried at sign-in of 128 and an IPv6 address:
    // each breaks within its entry (webui.audit-log-long-token-name).
    name: 'Audit Log with long names',
    path: '/audit',
    fake: (page) =>
      serve(page, {
        '/api/v1/audit/logs': LONG_AUDIT_ENTRIES,
        '/api/v1/audit/logs/count': { count: LONG_AUDIT_ENTRIES.length },
      }),
    // Every entry shows, each with the address.
    open: async (page) => {
      await expect(page.getByText(`Deleted API token: ${LONG_TOKEN_NAME}`)).toBeVisible();
      await expect(page.getByText(LONG_SIGN_IN_NAME, { exact: true })).toBeVisible();
      await expect(page.getByText(LONG_ADDRESS, { exact: true })).toHaveCount(LONG_AUDIT_ENTRIES.length);
    },
    shows: (page) => page.getByText(`Created API token: ${LONG_TOKEN_NAME}`),
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
    name: 'System: Security, two-factor authentication for every account',
    path: '/system#security',
    fake: (page) => serve(page, TWO_FACTOR_REQUIRED),
    shows: (page) => page.getByText(/administrator_of_the_household/),
    end: (page) => page.getByRole('button', { name: /^(Stop requiring|Не требовать)$/ }),
  },
  {
    name: 'Two-factor setup, asked during the grace period',
    path: '/two-factor-setup',
    fake: (page) => fakeTwoFactorSetup(page, false),
    shows: (page) => page.getByRole('link', { name: /^(Later|Позже)$/ }),
  },
  {
    name: 'Two-factor setup, the one page after the grace period',
    path: '/two-factor-setup',
    fake: (page) => fakeTwoFactorSetup(page, true),
    open: (page) => page.getByRole('button', { name: /^(Set up now|Настроить сейчас)$/ }).click(),
    shows: (page) => page.getByText(TOTP_SECRET),
    end: (page) => page.getByRole('button', { name: /^(Sign out|Выйти)$/ }),
  },
  {
    name: 'System: Security, API tokens',
    path: '/system#security',
    fake: (page) => serve(page, API_TOKENS),
    shows: (page) => page.getByText('nightly_backup_of_the_family_photos_to_the_garage_nas'),
    end: (page) => tokensRegion(page).getByRole('button', { name: /^(Delete Token|Удалить токен)$/ }).last(),
  },
  {
    name: 'System: Security, security keys',
    path: '/system#security',
    fake: (page) => serve(page, SECURITY_KEYS),
    shows: (page) => page.getByText(LONG_KEY_NAME),
    end: (page) => page.getByRole('button', { name: /^(Add a security key|Добавить ключ безопасности)$/ }),
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
    fake: async (page) => {
      await fakeStatsStream(page);
      await serve(page, { '/api/v1/monitor/history': MONITOR_HISTORY });
    },
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
    // Each volume's use against its quota, and one volume's use over time.
    name: "Dialog: a pool's volumes and their use",
    path: '/storage',
    fake: async (page) => {
      const gib = 1024 ** 3;
      await fakeStorageApi(page);
      await fakeVolumesWithUse(
        page,
        {
          e2e_media: [
            { name: 'documents-of-the-whole-family-since-2009', used: 9.6 * gib, quota: 10 * gib, usage: 'counted' },
            { name: 'photos', used: 3 * gib, quota: null, usage: 'counted' },
          ],
        },
        {
          'e2e_media/documents-of-the-whole-family-since-2009': {
            used: 9.6 * gib,
            quota: 10 * gib,
            usage: 'counted',
            points: [
              { time: '2026-11-01T10:00:00+00:00', used: 2 * gib, quota: 10 * gib },
              { time: '2026-11-17T11:15:00+00:00', used: 9.6 * gib, quota: 10 * gib },
            ],
          },
        },
      );
    },
    open: async (page) => {
      await page
        .locator('.card-nested')
        .filter({ has: page.locator('h3', { hasText: /^e2e_media$/ }) })
        .getByRole('button', { name: /^(Volumes|Тома)$/ })
        .click();
      await page
        .getByRole('dialog')
        .getByRole('button', {
          name: /^(Use over time of documents-of-the-whole-family-since-2009|Заполнение тома documents-of-the-whole-family-since-2009 во времени)$/,
        })
        .click();
    },
    shows: (page) => page.getByRole('dialog').getByRole('figure'),
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
      await settled(page.getByRole('listbox'));
      await page.getByRole('option', { name: /^(Every chunk \(full balance\)|Все блоки \(полная балансировка\))$/ }).click();
    },
    shows: (page) => page.getByRole('dialog').getByRole('checkbox'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    // A disk with data selected: its contents, the erase to confirm and the path to type (ADR-0411).
    name: 'Dialog: add a disk to a pool',
    path: '/storage',
    fake: (page) =>
      fakeStorageApi(page, {
        poolDevices: (pool) => ({
          pool, profiles: ['raid1c3'], members: [], removal: noRemoval(pool),
          candidates: [{
            name: 'e2e-hd11', path: '/dev/e2e-hd11', model: 'A Disk With A Long Model Name', serial: 'E2E-SERIAL-0011',
            wwn: null, size: 2e12, has_data: true, data_description: 'gpt partition table with 3 partitions',
          }],
        }),
      }),
    open: async (page) => {
      await page.getByRole('button', { name: /^(Add Disk|Добавить диск)$/, disabled: false }).first().click();
      await page.getByRole('dialog').getByRole('radio').check();
    },
    shows: (page) => page.getByRole('dialog').getByRole('checkbox'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    // A disk that cannot leave, with the server's reason, and one selected (ADR-0411).
    name: 'Dialog: remove a disk from a pool',
    path: '/storage',
    fake: (page) =>
      fakeStorageApi(page, {
        poolDevices: (pool) => ({
          pool, profiles: ['raid1'], candidates: [], removal: noRemoval(pool),
          members: [
            { devid: 1, path: '/dev/e2e-hd6', size: 1e12, used: 2e11, model: 'E2E Disk', serial: 'E2E6', problem: null },
            {
              devid: 2, path: '/dev/e2e-hd7', size: 1e12, used: 2e11, model: 'E2E Disk', serial: 'E2E7',
              problem:
                "the other disks cannot hold the pool's chunks: they need 3.2 GiB of disk space with room for the " +
                'chunk being moved, and the disks left give 1.0 GiB for its profile. A balance frees the space of ' +
                'partly empty chunks; or add a disk first',
            },
          ],
        }),
      }),
    open: async (page) => {
      await page.getByRole('button', { name: /^(Remove Disk|Удалить диск)$/, disabled: false }).first().click();
      await page.getByRole('dialog').getByRole('radio').first().check();
    },
    shows: (page) => page.getByRole('dialog').getByRole('textbox'),
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
    name: 'Dialog: add an iSCSI target',
    path: '/shares',
    fake: fakeIscsiTab,
    open: async (page) => {
      await page.locator('#shares-tab-iscsi').click();
      await page.getByRole('button', { name: /^(Add target|Добавить цель)$/ }).click();
    },
    shows: (page) => page.getByRole('dialog').getByRole('combobox'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    // The typed confirmation, and the initiators whose sessions end.
    name: 'Dialog: delete an iSCSI target',
    path: '/shares',
    fake: fakeIscsiTab,
    open: async (page) => {
      await page.locator('#shares-tab-iscsi').click();
      await page.locator(`li[data-target="${ISCSI_TARGET.name}"]`).getByRole('button', { name: /^(Delete|Удалить)$/ }).click();
    },
    shows: (page) => page.getByRole('dialog').getByText(ISCSI_TARGET.path),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    // One question for several shares names each of them and its folder (ADR-0389).
    name: 'Dialog: delete several shares',
    path: '/shares',
    fake: (page) => fakeSmbApi(page, { users: USERS, shares: SHARES }),
    open: async (page) => {
      await page.getByRole('checkbox', { name: /^(Select all|Выбрать все)$/ }).check();
      await page.getByRole('button', { name: /^(Delete selected|Удалить выбранные)$/ }).click();
    },
    shows: (page) => page.getByRole('dialog').getByText(`${POOLS}/tank/family_photos_and_videos`),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    // The host's interfaces with their addresses, one chosen that the host lacks (ADR-0391).
    name: 'Dialog: the network interfaces of SMB',
    path: '/shares',
    fake: (page) =>
      fakeSmbApi(page, { users: USERS, shares: SHARES, interfaces: { chosen: ['enP4p65s0', 'enx00e04c680001'] } }),
    open: (page) => page.getByRole('button', { name: /^(Choose interfaces…|Выбрать интерфейсы…)$/ }).click(),
    shows: (page) => page.getByRole('dialog').getByText('2001:db8:1234:5678:9abc:def0:1234:5678/64'),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: restart SMB',
    path: '/shares',
    fake: (page) =>
      fakeSmbApi(page, { users: USERS, shares: SHARES, interfaces: { chosen: ['enP4p65s0'], running: false } }),
    open: (page) => page.getByRole('button', { name: /^(Restart SMB…|Перезапустить SMB…)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
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
    // An app of several containers to install: its containers and their pinned images.
    name: 'Dialog: an app of the catalog',
    path: '/apps',
    fake: (page) => serve(page, { '/api/v1/apps/installed': [INSTALLED_JELLYFIN] }),
    open: (page) => page.getByRole('button', { name: /Immich/ }).first().click(),
    shows: (page) => page.getByRole('dialog').getByText('immich-machine-learning', { exact: true }),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    // The variables of every container of an app of several containers, none of them editable.
    name: 'Dialog: the variables of an app of several containers',
    path: '/apps',
    fake: (page) => serve(page, { '/api/v1/apps/installed': [INSTALLED_JELLYFIN] }),
    open: async (page) => {
      await page.getByRole('button', { name: /Immich/ }).first().click();
      await page.getByRole('dialog').getByRole('button', { name: /^(Environment|Окружение) \(\d+\)$/ }).click();
    },
    shows: (page) => page.getByRole('dialog').getByLabel('MACHINE_LEARNING_MODEL_REVISION'),
    // Opened on a tab that the click scrolled to: the view above checks the title as the dialog opens.
    end: dialogEnd,
  },
  {
    // The folders of an installed app of several containers, per container, with long paths.
    name: 'Dialog: the folders of an installed app of several containers',
    path: '/apps',
    fake: (page) => serve(page, { '/api/v1/apps/installed': INSTALLED_APPS }),
    open: async (page) => {
      await page.getByRole('button', { name: /Immich/ }).first().click();
      await page.getByRole('dialog').getByRole('button', { name: /^(Volumes|Тома) \(\d+\)$/ }).click();
    },
    shows: (page) => page.getByRole('dialog').getByRole('group', { name: 'immich-machine-learning' }),
    // Opened on a tab that the click may scroll to, as the view of the variables.
    end: dialogEnd,
  },
  {
    // The limits of one container of an app, with the bounds of the NAS in its hints (ADR-0390).
    name: 'Dialog: the limits of a container',
    path: '/apps',
    fake: (page) => serve(page, { '/api/v1/apps/installed': INSTALLED_APPS, '/api/v1/apps/limits': LIMIT_BOUNDS }),
    open: async (page) => {
      await page.getByRole('button', { name: /^(Installed|Установленные) \(2\)$/ }).click();
      await page.getByRole('listitem', { name: 'immich-machine-learning', exact: true }).getByRole('button').click();
    },
    shows: (page) => page.getByRole('dialog').getByRole('textbox').last(),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    // The update of an app to the catalog's newer images: its containers, the system disk and the
    // copy of its database on the pool (ADR-0408).
    name: 'Dialog: the update of an app',
    path: '/apps',
    fake: (page) => serve(page, {
      '/api/v1/apps/installed': UPDATABLE_APPS, '/api/v1/apps/installed/e2e-immich-id/update': UPDATE_PLAN,
    }),
    open: async (page) => {
      await page.getByRole('button', { name: /^(Installed|Установленные) \(2\)$/ }).click();
      await page.getByRole('button', { name: /^(Update to|Обновить до) v3\.5\.0$/ }).click();
    },
    shows: (page) => page.getByRole('dialog').getByText(/data\.previous/),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    // Its image, pinned by tag and digest (ADR-0410), has no hyphen to break at in its 64 hex digits.
    name: 'Dialog: an app with a long image name',
    path: '/apps',
    fake: (page) => serve(page, { '/api/v1/apps/installed': INSTALLED_APPS }),
    open: (page) => page.getByRole('button', { name: /Frigate NVR/ }).first().click(),
    shows: (page) =>
      page
        .getByRole('dialog')
        .getByText(
          'ghcr.io/blakeblackshear/frigate:0.18.2@sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a',
        ),
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
    // Its list of members, each with the SMB state's badge.
    name: 'Dialog: a new group',
    path: '/users',
    fake: (page) => fakeSmbApi(page, { users: USERS, groups: [fakeGroup(1, 'family_and_friends', USERS, [2, 3])] }),
    open: (page) => page.getByRole('button', { name: /^(New group|Новая группа)$/ }).click(),
    shows: (page) => page.getByRole('dialog').getByText('bob_the_family_photographer'),
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
    name: 'Dialog: turning the firewall on',
    path: '/system#security',
    fake: (page) => serve(page, { ...SECURITY, '/api/v1/security/firewall': {
      ...SECURITY['/api/v1/security/firewall'], enabled: false, revert_in: null } }),
    open: async (page) => {
      const firewall = page.locator('main div.card').filter({
        has: page.getByRole('heading', { name: /^(Firewall|Брандмауэр)$/ }) });
      await firewall.getByRole('button', { name: /^(Configure|Настроить)$/ }).click();
      await firewall.getByRole('button', { name: /^(Enable|Включить)$/ }).click();
    },
    shows: (page) => page.getByRole('dialog'),
    start: (page) => page.getByRole('heading', { name: /^(Turn the firewall on\?|Включить брандмауэр\?)$/ }),
    end: (page) => page.getByRole('dialog').getByRole('button', { name: /^(Turn on|Включить)$/ }),
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
    name: 'Dialog: serving a certificate',
    path: '/system#security',
    fake: (page) => serve(page, SECURITY),
    open: async (page) => {
      await page.locator('main').getByRole('button', { name: /^(Manage|Управление)$/ }).click();
      await page
        .locator('main')
        .getByRole('button', { name: /^(Serve|Отдавать) backup-of-the-garage-nas\.home\.arpa$/ })
        .click();
    },
    shows: (page) => page.getByRole('heading', { name: /^(Serve this certificate\?|Отдавать этот сертификат\?)$/ }),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: a new API token',
    path: '/system#security',
    fake: (page) => serve(page, API_TOKENS),
    open: openNewTokenDialog,
    shows: (page) => page.getByRole('dialog').getByRole('checkbox', { name: /^(Updates|Обновления)/ }),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: the new API token, shown once',
    path: '/system#security',
    fake: async (page) => {
      await serve(page, API_TOKENS);
      await page.route(
        (url) => url.pathname === '/api/v1/auth/tokens',
        (route) => (route.request().method() === 'POST' ? route.fulfill({ json: NEW_API_TOKEN }) : route.fallback()),
      );
    },
    open: async (page) => {
      await openNewTokenDialog(page);
      const dialog = page.getByRole('dialog');
      await dialog.getByLabel(/^(Name|Название)/).fill(NEW_API_TOKEN.name);
      await dialog.getByRole('button', { name: /^(Create Token|Создать токен)$/ }).click();
    },
    shows: (page) => page.getByRole('dialog').getByLabel(/^(The new token|Новый токен)$/),
    start: dialogTitle,
    end: dialogEnd,
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
    name: 'Dialog: add a security key',
    path: '/system#security',
    fake: (page) => serve(page, SECURITY_KEYS),
    open: (page) =>
      page.locator('main').getByRole('button', { name: /^(Add a security key|Добавить ключ безопасности)$/ }).click(),
    shows: (page) => page.getByRole('dialog').getByLabel(/^(Current password|Текущий пароль)/),
    start: dialogTitle,
    end: dialogEnd,
  },
  {
    name: 'Dialog: remove a security key',
    path: '/system#security',
    fake: (page) => serve(page, SECURITY_KEYS),
    open: (page) =>
      page.locator('main').getByRole('button', { name: new RegExp(`^(Remove|Удалить) ${LONG_KEY_NAME}$`) }).click(),
    shows: (page) => page.getByRole('dialog').getByText(LONG_KEY_NAME),
    start: dialogTitle,
    end: dialogEnd,
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
  {
    // The command palette, which the button of the header bar opens on a phone (ADR-0393), with
    // every page and action of an administrator: its list scrolls on its own to its last option.
    name: 'Dialog: the command palette',
    path: '/',
    fake: async (page) => {
      await fakeStorageApi(page);
      await fakeHealthApi(page, { predictions: () => predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2) });
      await serve(page, { ...DOCKER, '/api/v1/system/info': SYSTEM_INFO });
    },
    open: (page) =>
      page.getByRole('button', { name: /^(Open the command palette|Открыть палитру команд)$/ }).click(),
    shows: (page) => page.getByRole('dialog').getByRole('option').first(),
    start: dialogTitle,
    end: (page) => page.getByRole('dialog').getByRole('option').last(),
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

    test(`${ARRANGING_VIEW.name} fits the screen`, async ({ authenticatedPage: page }) => {
      try {
        await checkFits(page, ARRANGING_VIEW);
      } finally {
        await resetLayout(page);
      }
    });

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
    // Not even hidden: the page's first button is Logout, as before, and the button of the command
    // palette is the phone's alone (ADR-0393).
    await expect(menuButton(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Open the command palette' })).toHaveCount(0);
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
