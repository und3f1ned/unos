import { api } from "./client";

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

export interface NetworkStats {
  bytes_sent: number;
  bytes_recv: number;
  bytes_sent_rate: number;
  bytes_recv_rate: number;
  packets_sent: number;
  packets_recv: number;
  interfaces: NetworkInterface[];
}

export interface NetworkInterface {
  name: string;
  ipv4: string | null;
  speed: number;
  is_up: boolean;
  bytes_sent: number;
  bytes_recv: number;
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

export interface DiskIO {
  name: string;
  read_bytes: number;
  write_bytes: number;
  read_count: number;
  write_count: number;
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

export interface AllStats {
  cpu: CpuStats;
  memory: MemoryStats;
  network: NetworkStats;
  disk_io: DiskIOStats;
  timestamp: string;
}

export async function getCpuStats(): Promise<CpuStats> {
  return api.get<CpuStats>("/monitor/cpu");
}

export async function getMemoryStats(): Promise<MemoryStats> {
  return api.get<MemoryStats>("/monitor/memory");
}

export async function getNetworkStats(): Promise<NetworkStats> {
  return api.get<NetworkStats>("/monitor/network");
}

export async function getDiskIOStats(): Promise<DiskIOStats> {
  return api.get<DiskIOStats>("/monitor/disk-io");
}

export async function getTopProcesses(sortBy: string = "cpu", limit: number = 10): Promise<ProcessInfo[]> {
  return api.get<ProcessInfo[]>("/monitor/processes?sort_by=" + sortBy + "&limit=" + limit);
}

export async function getCpuHistory(): Promise<HistoryPoint[]> {
  return api.get<HistoryPoint[]>("/monitor/history/cpu");
}

export async function getMemoryHistory(): Promise<HistoryPoint[]> {
  return api.get<HistoryPoint[]>("/monitor/history/memory");
}

export async function getNetworkHistory(): Promise<NetworkHistoryPoint[]> {
  return api.get<NetworkHistoryPoint[]>("/monitor/history/network");
}

export async function getDiskIOHistory(): Promise<DiskIOHistoryPoint[]> {
  return api.get<DiskIOHistoryPoint[]>("/monitor/history/disk-io");
}

export async function getAllStats(): Promise<AllStats> {
  return api.get<AllStats>("/monitor/all");
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

export function formatBytesPerSecond(bytes: number): string {
  if (bytes === 0) return "0 B/s";
  const k = 1024;
  const sizes = ["B/s", "KB/s", "MB/s", "GB/s"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}
