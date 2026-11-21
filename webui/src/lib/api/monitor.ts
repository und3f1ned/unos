import { api } from './client';

// System > Resource Monitor's history (supervisor/src/monitor/router.py, ADR-0399): a point every
// interval_seconds, an hour of them, kept in the supervisor's RAM since it started. The tab reads it
// once when it opens and adds each stats message of /ws/v1/events to it.

export interface MonitorHistoryPoint {
	/** Seconds before the answer, on the supervisor's boot-time clock: no time of its wall clock. */
	age_seconds: number;
	cpu_percent: number;
	memory_percent: number;
	/** Bytes per second: the host's interfaces that System > Network lists, and its physical disks. */
	net_recv_rate: number;
	net_sent_rate: number;
	disk_read_rate: number;
	disk_write_rate: number;
}

export interface MonitorHistory {
	interval_seconds: number;
	/** Oldest first. */
	points: MonitorHistoryPoint[];
}

export const getMonitorHistory = () => api.get<MonitorHistory>('/monitor/history');
