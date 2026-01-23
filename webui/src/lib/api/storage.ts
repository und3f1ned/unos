import { api } from './client';

export interface Disk {
  name: string;
  path: string;
  model: string | null;
  serial: string | null;
  size: number;
  rotational: boolean;
  mountpoint: string | null;
  type: string;
}

export interface SmartData {
  device: string;
  healthy: boolean;
  temperature: number | null;
  power_on_hours: number | null;
  reallocated_sectors: number | null;
  raw_data: Record<string, unknown> | null;
}

export interface Pool {
  name: string;
  uuid: string | null;
  devices: string[];
  total_size: number;
  used_size: number;
  mountpoint: string | null;
  raid_level: string | null;
}

export interface PoolCreate {
  name: string;
  devices: string[];
  raid_level: string;
}

export interface Volume {
  name: string;
  pool: string;
  path: string;
  used: number | null;
  quota: number | null;
}

export interface VolumeCreate {
  name: string;
  quota: number | null;
}

export async function getDisks(): Promise<Disk[]> {
  return api.get<Disk[]>('/storage/disks');
}

export async function getDiskSmart(name: string): Promise<SmartData> {
  return api.get<SmartData>(`/storage/disks/${name}/smart`);
}

export async function getPools(): Promise<Pool[]> {
  return api.get<Pool[]>('/storage/pools');
}

export async function getPool(name: string): Promise<Pool> {
  return api.get<Pool>(`/storage/pools/${name}`);
}

export async function createPool(pool: PoolCreate): Promise<Pool> {
  return api.post<Pool>('/storage/pools', pool);
}

export async function deletePool(name: string): Promise<void> {
  return api.delete(`/storage/pools/${name}`);
}

export async function getVolumes(poolName: string): Promise<Volume[]> {
  return api.get<Volume[]>(`/storage/pools/${poolName}/volumes`);
}

export async function createVolume(poolName: string, volume: VolumeCreate): Promise<Volume> {
  return api.post<Volume>(`/storage/pools/${poolName}/volumes`, volume);
}

export async function deleteVolume(poolName: string, volumeName: string): Promise<void> {
  return api.delete(`/storage/pools/${poolName}/volumes/${volumeName}`);
}
