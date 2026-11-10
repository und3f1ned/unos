import { writable } from 'svelte/store';
import { getToken, handleUnauthorized } from '$lib/api/client';

// The parts of the stats message of /ws/v1/events (supervisor/src/websocket/events.py)
export interface CpuStats {
  percent: number;
  per_core: number[];
  load_1: number;
  load_5: number;
  load_15: number;
  cores: number;
  threads: number;
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

export interface NetworkInterface {
  name: string;
  ipv4: string | null;
  speed: number;
  is_up: boolean;
  bytes_sent: number;
  bytes_recv: number;
  bytes_sent_rate: number;
  bytes_recv_rate: number;
}

export interface NetworkStats {
  bytes_sent: number;
  bytes_recv: number;
  bytes_sent_rate: number;
  bytes_recv_rate: number;
  packets_sent: number;
  packets_recv: number;
  interfaces: NetworkInterface[];
}

export interface DiskIO {
  name: string;
  read_bytes: number;
  write_bytes: number;
  read_count: number;
  write_count: number;
}

export interface DiskIOStats {
  read_bytes: number;
  write_bytes: number;
  read_rate: number;
  write_rate: number;
  read_count: number;
  write_count: number;
  disks: DiskIO[];
}

export interface ProcessInfo {
  pid: number;
  name: string;
  cpu_percent: number;
  memory_percent: number;
  memory_mb: number;
  status: string;
  username: string;
}

// The stats message of /ws/v1/events (supervisor/src/websocket/events.py)
export interface WebSocketStats {
  temperatures: Record<string, number>;
  cpu: CpuStats;
  memory: MemoryStats;
  network: NetworkStats;
  disk_io: DiskIOStats;
  processes: ProcessInfo[];
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

let ws: WebSocket | null = null;
let wsToken: string | null = null;
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
  wsToken = null;
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
 * Open the stats stream. Keeps a single connection: a call while a socket for
 * the same token is open or connecting does nothing; a different token (new
 * login) replaces the old socket.
 */
export function connectWebSocket() {
  const token = getToken();
  if (!token || typeof window === 'undefined') return;

  if (
    ws &&
    wsToken === token &&
    (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)
  ) {
    return;
  }

  clearReconnect();
  closeSocket();
  wsForbidden.set(false);

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  // No token in the URL: URLs end up in proxy and server logs.
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws/v1/events`);
  ws = socket;
  wsToken = token;
  let authenticatedAt = 0;

  socket.onopen = () => {
    socket.send(JSON.stringify({ type: 'auth', token }));
  };

  socket.onmessage = (event) => {
    let message: { type?: string; data?: WebSocketStats };
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (message.type === 'auth_ok') {
      authenticatedAt = Date.now();
      wsConnected.set(true);
    } else if (message.type === 'stats' && message.data) {
      if (!authenticatedAt) authenticatedAt = Date.now();
      wsConnected.set(true);
      stats.set(message.data);
    }
  };

  // No onerror handler: an error event is always followed by this close event.
  socket.onclose = (event) => {
    if (ws !== socket) return;
    ws = null;
    wsToken = null;
    wsConnected.set(false);

    if (event.code === WS_CLOSE_UNAUTHORIZED) {
      // Token expired or the account is gone: same as an HTTP 401.
      handleUnauthorized(token);
      return;
    }
    if (event.code === WS_CLOSE_FORBIDDEN) {
      wsForbidden.set(true);
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
  stats.set(null);
}

// Utility functions
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export function formatBytesPerSecond(bytes: number): string {
  if (bytes === 0) return '0 B/s';
  const k = 1024;
  const sizes = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}
