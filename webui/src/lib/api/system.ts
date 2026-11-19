import { api } from './client';

export interface SystemInfo {
  hostname: string;
  kernel: string;
  os: string;
  architecture: string;
  /** When the host booted: ISO 8601 with its offset (ADR-0401). */
  boot_time: string;
}

export async function getSystemInfo(): Promise<SystemInfo> {
  return api.get<SystemInfo>('/system/info');
}

/** The host's time zone, "Unknown" when timedatectl cannot tell (ADR-0331). */
export async function getTimezone(): Promise<{ timezone: string }> {
  return api.get<{ timezone: string }>('/system/timezone');
}

/** The time zones the host knows; none when timedatectl cannot list them. */
export async function getTimezones(): Promise<{ timezones: string[] }> {
  return api.get<{ timezones: string[] }>('/system/timezones');
}

/** Set the host's time zone; the Scheduler follows it (ADR-0264). */
export async function setTimezone(timezone: string): Promise<{ status: string; timezone: string }> {
  return api.put<{ status: string; timezone: string }>('/system/timezone', { timezone });
}
