import { api } from './client';

export interface SystemInfo {
  hostname: string;
  kernel: string;
  os: string;
  architecture: string;
  /** When the host booted, in seconds since the epoch. */
  boot_time: number;
}

export interface PCIeDevice {
  slot: string;
  class_name: string;
  vendor: string;
  device: string;
  revision: string | null;
  driver: string | null;
}

export async function getSystemInfo(): Promise<SystemInfo> {
  return api.get<SystemInfo>('/system/info');
}

export async function getPCIeDevices(): Promise<PCIeDevice[]> {
  return api.get<PCIeDevice[]>('/system/pcie');
}
