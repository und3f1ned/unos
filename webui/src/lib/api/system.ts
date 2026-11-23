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

/** Where Docker keeps its data (ADR-0486, ADR-0487). */
export interface DockerData {
	data_root: string;
	/** containerd's root when Docker keeps its images in containerd's image store; null otherwise. */
	containerd_root: string | null;
	/** The pool that holds both; null when one is not on a pool. */
	pool: string | null;
	/** The pool that a move of Docker's data goes to while it runs. */
	moving_to: string | null;
}

/** Where Docker keeps its data: on a pool, or not. Administrators only. */
export async function getDockerData(): Promise<DockerData> {
	return api.get<DockerData>('/system/docker-data');
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
