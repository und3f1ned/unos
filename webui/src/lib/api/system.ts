import { api } from './client';

export interface SystemInfo {
  hostname: string;
  kernel: string;
  os: string;
  architecture: string;
  /** When the host booted, in seconds since the epoch. */
  boot_time: number;
}

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

export interface PCIeDevice {
  slot: string;
  class_name: string;
  vendor: string;
  device: string;
  revision: string | null;
  driver: string | null;
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

export async function getSystemInfo(): Promise<SystemInfo> {
  return api.get<SystemInfo>('/system/info');
}

export async function getSystemStats(): Promise<SystemStats> {
  return api.get<SystemStats>('/system/stats');
}

export async function getPCIeDevices(): Promise<PCIeDevice[]> {
  return api.get<PCIeDevice[]>('/system/pcie');
}

export async function getTemperatures(): Promise<TemperatureGroup[]> {
  return api.get<TemperatureGroup[]>('/system/temperatures');
}
