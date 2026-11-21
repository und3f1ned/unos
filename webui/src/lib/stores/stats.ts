import { writable } from 'svelte/store';
import { currentSession, currentSessionKey, handleSetupRequired, handleUnauthorized } from '$lib/api/client';
import { problems, problemsKnown, type Problem } from '$lib/stores/problems';
import { scrubs, type ScrubUpdate } from '$lib/stores/scrubs';
import { balances, type BalanceUpdate } from '$lib/stores/balances';
import { diskRemovals, type DiskRemovalUpdate } from '$lib/stores/diskRemovals';
import { repairJob } from '$lib/stores/repair';
import { appInstalls } from '$lib/stores/appInstalls';
import { updateProgress } from '$lib/stores/updates';
import type { RepairResult } from '$lib/api/shares';
import type { AppInstallJob } from '$lib/api/apps';
import type { UpdateProgress } from '$lib/api/updates';

// The parts of the stats message of /ws/v1/events (supervisor/src/websocket/events.py)
export interface CpuStats {
  percent: number;
  per_core: number[];
  load_1: number;
  load_5: number;
  load_15: number;
  cores: number;
  threads: number;
  // The CPU's or SoC's temperature in °C, which the server chooses among the host's sensors;
  // null without sensors (ADR-0346).
  temperature: number | null;
}

export interface MemoryStats {
  total: number;
  used: number;
  free: number;
  available: number;
  percent: number;
  swap_total: number;
  swap_used: number;
  swap_percent: number;
}

// An interface of the host that System > Network lists, with its counters and rates; only
// administrators get them (ADR-0388).
export interface NetworkInterface {
  name: string;
  bytes_sent: number;
  bytes_recv: number;
  bytes_sent_rate: number;
  bytes_recv_rate: number;
}

// The host's traffic: the sums of the rates of its interfaces (ADR-0388).
export interface NetworkStats {
  bytes_sent_rate: number;
  bytes_recv_rate: number;
  interfaces: NetworkInterface[];
}

// A block device of the host, disk or partition (ADR-0360); only administrators get them (ADR-0399).
export interface DiskIO {
  name: string;
}

// The host's physical disks: the sums of their rates, without zram, loop, device mapper and md
// devices (ADR-0388); `disks` lists every block device but loop and RAM devices (ADR-0360).
export interface DiskIOStats {
  read_rate: number;
  write_rate: number;
  disks: DiskIO[];
}

// A process of the host, one of the ten that use the most CPU; only administrators get them
// (ADR-0399).
export interface ProcessInfo {
  pid: number;
  name: string;
  // The share of one core since the last listing, up to 10 s before.
  cpu_percent: number;
  // The resident memory.
  memory_bytes: number;
  // Its effective user as the host names it, or the uid where the host has no name for it.
  username: string;
}

// The stats message of /ws/v1/events (supervisor/src/websocket/events.py)
export interface WebSocketStats {
  cpu: CpuStats;
  memory: MemoryStats;
  network: NetworkStats;
  disk_io: DiskIOStats;
  processes: ProcessInfo[];
  // The seconds since the sample before, over which the rates and the CPU shares are taken: 0 for
  // the first sample and after a gap, whose rates and shares are 0 (ADR-0388, ADR-0399).
  sample_seconds: number;
  timestamp: string;
}

// Main stores
export const stats = writable<WebSocketStats | null>(null);
export const wsConnected = writable(false);

// Close codes of the WebSocket auth handshake (supervisor/src/websocket/events.py).
export const WS_CLOSE_UNAUTHORIZED = 4401;
export const WS_CLOSE_FORBIDDEN = 4403;

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
// A connection that stayed up this long resets the backoff; one that is
// dropped right after the handshake keeps backing off.
const STABLE_CONNECTION_MS = 10000;

/** The server refused the stream for this account (close code 4403). */
export const wsForbidden = writable(false);

/**
 * The first attempt of this session has ended: the socket was up, refused, or closed before it was
 * up. Until then the page is connecting, and says so instead of "disconnected", and a stats card
 * waits as a skeleton (ADR-0430); after a failed attempt the page says it is not connected. False
 * again after a sign-out.
 */
export const wsSettled = writable(false);

let ws: WebSocket | null = null;
// The session (currentSession()) that the socket was opened in.
let wsSession: number | null = null;
let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
let reconnectAttempts = 0;

function clearReconnect() {
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
  }
}

/** Close the current socket without triggering its reconnect logic. */
function closeSocket() {
  const socket = ws;
  ws = null;
  wsSession = null;
  if (!socket) return;
  socket.onopen = null;
  socket.onmessage = null;
  socket.onerror = null;
  socket.onclose = null;
  if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
    socket.close(1000);
  }
}

function scheduleReconnect() {
  clearReconnect();
  const backoff = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.min(reconnectAttempts, 10));
  reconnectAttempts += 1;
  // Jitter keeps many open tabs from reconnecting in lockstep after a restart.
  const delay = backoff + Math.random() * backoff * 0.2;
  reconnectTimeout = setTimeout(() => {
    reconnectTimeout = null;
    connectWebSocket();
  }, delay);
}

/**
 * Open the stats stream of the signed-in account. Keeps a single connection: a
 * call while a socket of the same session is open or connecting does nothing;
 * after a sign-in or a sign-out (currentSession()) it replaces the old socket.
 */
export function connectWebSocket() {
  if (typeof window === 'undefined') return;
  const session = currentSession();

  if (
    ws &&
    wsSession === session &&
    (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)
  ) {
    return;
  }

  clearReconnect();
  closeSocket();
  wsForbidden.set(false);

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  // No credential in the URL: URLs end up in proxy and server logs.
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws/v1/events`);
  ws = socket;
  wsSession = session;
  let authenticatedAt = 0;

  socket.onopen = () => {
    // No token: the server takes the session cookie of the handshake, from this origin only and with
    // the session key (ADR-0397).
    socket.send(JSON.stringify({ type: 'auth', key: currentSessionKey() }));
  };

  socket.onmessage = (event) => {
    let message: { type?: string; data?: unknown };
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (message.type === 'auth_ok') {
      authenticatedAt = Date.now();
      wsConnected.set(true);
      wsSettled.set(true);
      // A new socket, maybe to a supervisor that restarted: the scrubs the last one sent need not
      // run any more, and this one sends those that its supervisor saw running (ADR-0367); the
      // balances likewise (ADR-0379), the removals of disks (ADR-0411), and the installs of apps
      // (ADR-0384).
      scrubs.set([]);
      balances.set([]);
      diskRemovals.set([]);
      appInstalls.set([]);
      updateProgress.set(null);
    } else if (message.type === 'stats' && message.data) {
      if (!authenticatedAt) authenticatedAt = Date.now();
      wsConnected.set(true);
      wsSettled.set(true);
      stats.set(message.data as WebSocketStats);
    } else if (message.type === 'problems' && Array.isArray(message.data)) {
      // Administrators only: the problems that are still there (ADR-0332).
      problems.set(message.data as Problem[]);
      problemsKnown.set(true);
    } else if (message.type === 'scrubs' && Array.isArray(message.data)) {
      // Administrators only: the scrubs that run, until they end (ADR-0367).
      scrubs.set(message.data as ScrubUpdate[]);
    } else if (message.type === 'balances' && Array.isArray(message.data)) {
      // Administrators only: the balances that run, until they end (ADR-0379).
      balances.set(message.data as BalanceUpdate[]);
    } else if (message.type === 'disk_removals' && Array.isArray(message.data)) {
      // Administrators only: the removals of disks from pools that run, until they end (ADR-0411).
      diskRemovals.set(message.data as DiskRemovalUpdate[]);
    } else if (message.type === 'repair') {
      // Administrators only, right after auth_ok and on each change: the running or last repair of
      // a share's permissions, null when none has run since the supervisor started (ADR-0363).
      repairJob.set((message.data ?? null) as RepairResult | null);
    } else if (message.type === 'app_installs' && Array.isArray(message.data)) {
      // Administrators only: the running install of an app and the last of each app (ADR-0384).
      appInstalls.set(message.data as AppInstallJob[]);
    } else if (message.type === 'update' && message.data) {
      // Administrators only: each step of the update or rollback of UNOS (ADR-0439).
      updateProgress.set(message.data as UpdateProgress);
    }
  };

  // No onerror handler: an error event is always followed by this close event.
  socket.onclose = (event) => {
    if (ws !== socket) return;
    ws = null;
    wsSession = null;
    wsConnected.set(false);
    problemsKnown.set(false);
    wsSettled.set(true);

    if (event.code === WS_CLOSE_UNAUTHORIZED) {
      // The session ended or the account is gone: same as an HTTP 401.
      handleUnauthorized(session);
      return;
    }
    if (event.code === WS_CLOSE_FORBIDDEN) {
      wsForbidden.set(true);
      // The events socket is forbidden to an account only once its grace period to set up
      // two-factor authentication has ended: the layout loads the account again and leads to the setup.
      handleSetupRequired();
      return;
    }
    if (authenticatedAt && Date.now() - authenticatedAt >= STABLE_CONNECTION_MS) {
      reconnectAttempts = 0;
    }
    scheduleReconnect();
  };
}

export function disconnectWebSocket() {
  clearReconnect();
  reconnectAttempts = 0;
  closeSocket();
  wsConnected.set(false);
  wsForbidden.set(false);
  wsSettled.set(false);
  stats.set(null);
  problems.set([]);
  problemsKnown.set(false);
  scrubs.set([]);
  balances.set([]);
  diskRemovals.set([]);
  repairJob.set(null);
  appInstalls.set([]);
  updateProgress.set(null);
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}
