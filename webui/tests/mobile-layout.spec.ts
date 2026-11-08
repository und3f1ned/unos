import type { Locator, Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import { FAKE_IMPORTABLE, fakeStorageApi } from './storage-fakes';
import { POOLS, accessOf, fakeNfsExport, fakeShare, fakeSmbApi, fakeUser } from './shares-fakes';
import { HEALTHY_HDD_2, REPLACE_SOON, fakeHealthApi, predictionsAnswer } from './health-fakes';
import { fakeNetworkApi } from './network-fakes';
import { fakeNotificationsApi, type Channel } from './notifications-fakes';

// The web UI on a phone (feature webui.mobile-layout, ADR-0301). At 375 x 812, a small phone,
// every page of the navigation, the views behind its tabs, each tab of System, the main dialogs,
// a regular user's Account page, sign-in and first run fit the width of the screen, in English
// and in Russian, whose texts are longer: neither the page nor a part of it scrolls sideways,
// except a table in its own frame (the class table-frame), and nothing is cut off at the side.
// Below Tailwind's md breakpoint the sidebar folds into a menu behind a button of the header bar.
// The data that the stand-in lacks (disks, btrfs, Docker, the host's network, journal and power)
// comes from route fakes, with names and paths as long as a NAS has them. Nothing here changes
// the stand-in but the regular account that a test of the menu creates and deletes. The other
// specs run at the desktop size of playwright.config.ts, where the sidebar stays.

const PHONE = { width: 375, height: 812 };

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

const SYSTEM_INFO = {
  hostname: 'nas-living-room.home.arpa',
  kernel: '6.12.48+deb13-arm64',
  os: 'Debian GNU/Linux 13 (trixie)',
  architecture: 'aarch64',
  // When the host started (psutil.boot_time()): 3 days and 4 hours ago.
  uptime: Date.now() / 1000 - (3 * 24 + 4) * 3600,
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
const FILES: Record<string, ReturnType<typeof listing>> = {
  '': listing(POOLS, null, [entry(DATA, 'directory', 0)]),
  [DATA]: listing(DATA, POOLS, [
    entry(FAMILY, 'directory', 0),
    entry(`${DATA}/Annual report 2026 - final version signed by both parties.pdf`, 'file', 2_516_582),
    entry(`${DATA}/IMG_20261107_101530_HDR_edited_by_the_photo_editor.jpg`, 'file', 7_340_032),
    entry(`${DATA}/nas-configuration-backup-2026-11-07.tar.gz`, 'file', 734_003_200),
  ]),
};
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
  '/api/v1/security/certificates/status': {
    has_certificate: true, type: 'self_signed', domain: 'nas-living-room.home.arpa',
    valid_until: 'Nov  8 01:47:31 2027 GMT', is_valid: true, auto_renew: false,
  },
  '/api/v1/security/certificates': {
    certificates: [
      { id: 'custom_nas', type: 'self_signed', domain: 'nas-living-room.home.arpa', issuer: 'nas-living-room.home.arpa',
        valid_from: 'Nov  8 01:47:31 2026 GMT', valid_until: 'Nov  8 01:47:31 2027 GMT', is_valid: true,
        auto_renew: false, fingerprint: null },
    ],
    active_certificate: 'custom_nas',
  },
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
  cpu: { percent: 12.5, per_core: [10, 15, 20, 5, 12, 18, 9, 11], load_1: 0.5, load_5: 0.4, load_15: 0.3, cores: 8, threads: 8 },
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
  history: { cpu: [], memory: [], network: [], disk_io: [] },
  temperatures: {},
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
    name: 'Storage',
    path: '/storage',
    fake: (page) => fakeStorageApi(page, { importable: FAKE_IMPORTABLE }),
    shows: (page) => page.getByText('e2e_frozen').first(),
  },
  {
    name: 'Snapshots',
    path: '/snapshots',
    fake: (page) =>
      serve(page, {
        '/api/v1/snapshots/subvolumes': SUBVOLUMES,
        '/api/v1/snapshots/': SNAPSHOTS,
        '/api/v1/snapshots': SNAPSHOTS,
      }),
    shows: (page) => page.getByText('before-upgrade_20261106_181500').first(),
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
    fake: (page) =>
      page.route(
        (url) => url.pathname === '/api/v1/files/list',
        (route) => {
          const request = route.request();
          const path = new URL(request.url()).searchParams.get('path') ?? '';
          const answer = FILES[path];
          return request.method() === 'GET' && answer ? route.fulfill({ json: answer }) : route.abort();
        },
      ),
    // A double-click on its row opens a folder.
    open: (page) => page.locator('main div.grid').filter({ has: page.locator(`input[data-path="${DATA}"]`) }).dblclick(),
    shows: (page) => page.getByText('Annual report 2026 - final version signed by both parties.pdf', { exact: true }),
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

  // The dialogs: they fit the screen and scroll on their own.
  {
    name: 'Dialog: create a pool',
    path: '/storage',
    fake: (page) => fakeStorageApi(page),
    open: (page) => page.getByRole('button', { name: /^(Create Pool|Создать пул)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
  },
  {
    name: 'Dialog: create a snapshot',
    path: '/snapshots',
    fake: (page) =>
      serve(page, {
        '/api/v1/snapshots/subvolumes': SUBVOLUMES,
        '/api/v1/snapshots/': SNAPSHOTS,
        '/api/v1/snapshots': SNAPSHOTS,
      }),
    open: (page) => page.getByRole('button', { name: /^(Create Snapshot|Создать снимок)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
  },
  {
    name: 'Dialog: add a share',
    path: '/shares',
    fake: (page) => fakeSmbApi(page, { users: USERS, shares: SHARES }),
    open: (page) => page.getByRole('button', { name: /^(Add SMB share|Добавить общую папку SMB)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
  },
  {
    name: 'Dialog: create a container',
    path: '/containers',
    fake: (page) => serve(page, DOCKER),
    open: (page) => page.getByRole('button', { name: /^(Create Container|Создать контейнер)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
  },
  {
    name: 'Dialog: a container',
    path: '/containers',
    fake: (page) => serve(page, DOCKER),
    open: (page) => page.locator('tr', { hasText: 'e2e-immich-machine-learning' }).click(),
    shows: (page) => page.getByRole('dialog'),
  },
  {
    name: 'Dialog: an app of the catalog',
    path: '/apps',
    fake: (page) => serve(page, { '/api/v1/apps/installed': INSTALLED_APPS }),
    open: (page) => page.getByRole('button', { name: /Immich/ }).first().click(),
    shows: (page) => page.getByRole('dialog'),
  },
  {
    name: 'Dialog: add a user',
    path: '/users',
    fake: (page) => fakeSmbApi(page, { users: USERS }),
    open: (page) => page.getByRole('button', { name: /^(Add user|Добавить пользователя)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
  },
  {
    name: 'Dialog: SMART data of a disk',
    path: '/system#health',
    fake: (page) => fakeHealthApi(page),
    open: (page) => page.getByRole('button', { name: /\/dev\/e2e-sda$/ }).click(),
    shows: (page) => page.getByRole('dialog').getByText('Raw_Read_Error_Rate'),
  },
  {
    name: 'Dialog: restore a backup',
    path: '/system#backup',
    fake: (page) => serve(page, BACKUPS),
    open: (page) => page.getByRole('button', { name: /unos_backup_20261107_020000\.tar\.gz\.enc$/ }).first().click(),
    shows: (page) => page.getByRole('dialog'),
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
  },
  {
    name: 'Dialog: a mail channel',
    path: '/system#notifications',
    fake: (page) => fakeNotificationsApi(page, CHANNELS),
    open: (page) => page.getByRole('button', { name: /^(Add Email|Добавить эл\. почту)$/ }).click(),
    shows: (page) => page.getByRole('dialog'),
  },
];

for (const [language, locale] of [
  ['English', 'en-US'],
  ['Russian', 'ru-RU'],
] as const) {
  test.describe(`On a phone, in ${language}`, () => {
    // Without a language chosen in it, the web UI takes the browser's (ADR-0287).
    test.use({ viewport: PHONE, locale });

    for (const view of VIEWS) {
      test(`${view.name} fits the width of the screen`, async ({ authenticatedPage: page }) => {
        await view.fake?.(page);
        await page.goto(view.path);
        await view.open?.(page);
        await expect(view.shows(page)).toBeVisible();
        expect(await tooWide(page)).toEqual([]);
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
});
