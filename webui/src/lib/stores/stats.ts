import { writable, derived } from 'svelte/store';

// Extended types for WebSocket stats
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

export interface HistoryPoint {
  timestamp: string;
  value: number;
}

export interface NetworkHistoryPoint {
  timestamp: string;
  sent: number;
  recv: number;
}

export interface DiskIOHistoryPoint {
  timestamp: string;
  read: number;
  write: number;
}

export interface StatsHistory {
  cpu: HistoryPoint[];
  memory: HistoryPoint[];
  network: NetworkHistoryPoint[];
  disk_io: DiskIOHistoryPoint[];
}

// Full WebSocket stats payload
export interface WebSocketStats {
  // Basic stats (backward compatible)
  cpu_percent: number;
  cpu_count: number;
  memory_total: number;
  memory_used: number;
  memory_percent: number;
  disk_total: number;
  disk_used: number;
  disk_percent: number;
  network_sent: number;
  network_recv: number;
  temperatures: Record<string, number>;

  // Extended stats
  cpu: CpuStats;
  memory: MemoryStats;
  network: NetworkStats;
  disk_io: DiskIOStats;
  processes: ProcessInfo[];
  history: StatsHistory;
  timestamp: string;
}

// Legacy SystemStats interface for backward compatibility
export interface SystemStats {
  cpu_percent: number;
  cpu_count: number;
  memory_total: number;
  memory_used: number;
  memory_percent: number;
  disk_total: number;
  disk_used: number;
  disk_percent: number;
  network_sent: number;
  network_recv: number;
  temperatures: Record<string, number>;
}

export interface TemperatureSensor {
  label: string;
  current: number;
  high: number | null;
  critical: number | null;
}

export interface TemperatureGroup {
  name: string;
  sensors: TemperatureSensor[];
}

// Main stores
export const stats = writable<WebSocketStats | null>(null);
export const wsConnected = writable(false);
export const temperatures = writable<TemperatureGroup[]>([]);

// Derived stores for specific data
export const cpuStats = derived(stats, $stats => $stats?.cpu ?? null);
export const memoryStats = derived(stats, $stats => $stats?.memory ?? null);
export const networkStats = derived(stats, $stats => $stats?.network ?? null);
export const diskIOStats = derived(stats, $stats => $stats?.disk_io ?? null);
export const processes = derived(stats, $stats => $stats?.processes ?? []);
export const statsHistory = derived(stats, $stats => $stats?.history ?? null);

// Derived store for network interfaces with rates
export const networkInterfaces = derived(stats, $stats => $stats?.network?.interfaces ?? []);

let ws: WebSocket | null = null;
let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;

export function connectWebSocket() {
  const token = localStorage.getItem('token');
  if (!token) return;

  // Clear any pending reconnect
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/v1/events?token=${token}`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    wsConnected.set(true);
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'stats') {
      stats.set(data.data);
    }
  };

  ws.onclose = () => {
    wsConnected.set(false);
    ws = null;
    // Reconnect after 3 seconds
    reconnectTimeout = setTimeout(connectWebSocket, 3000);
  };

  ws.onerror = () => {
    ws?.close();
  };
}

export function disconnectWebSocket() {
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
  }
  ws?.close();
  ws = null;
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
