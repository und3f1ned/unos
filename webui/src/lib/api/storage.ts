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
  raid_level: 'single' | 'raid0' | 'raid1' | 'raid1c3' | 'raid1c4' | 'raid10';
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

export interface ScrubStatus {
  pool: string;
  status: 'running' | 'finished' | 'aborted' | 'idle';
  duration: number | null;
  bytes_scrubbed: number | null;
  total_bytes: number | null;
  rate: number | null;
  errors: number;
  last_run: string | null;
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

export async function startScrub(poolName: string): Promise<ScrubStatus> {
  return api.post<ScrubStatus>(`/storage/pools/${poolName}/scrub`, {});
}

export async function getScrubStatus(poolName: string): Promise<ScrubStatus> {
  return api.get<ScrubStatus>(`/storage/pools/${poolName}/scrub`);
}

export async function cancelScrub(poolName: string): Promise<ScrubStatus> {
  return api.delete<ScrubStatus>(`/storage/pools/${poolName}/scrub`);
}
