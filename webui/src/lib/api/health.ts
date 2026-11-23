import { api } from './client';

export interface SmartAttribute {
	id: number;
	name: string;
	value: number;
	worst: number;
	thresh: number;
	raw_value: string;
	status: 'ok' | 'warning' | 'critical';
}

export interface SmartInfo {
	device: string;
	model: string;
	serial: string;
	firmware: string;
	capacity: string;
	temperature: number | null;
	power_on_hours: number | null;
	power_cycle_count: number | null;
	/**
	 * STANDBY: the disk sleeps, and its SMART data was not read so as not to wake it (ADR-0378).
	 * NO_SMART: an SD card or eMMC, which has no SMART (storage.free-mmc-as-data-disk).
	 */
	health_status: 'PASSED' | 'FAILED' | 'UNKNOWN' | 'STANDBY' | 'NO_SMART';
	/** ATA attributes; NVMe disks have none. */
	attributes: SmartAttribute[];
	/**
	 * The numbers of an NVMe disk's SMART/Health Information log, by smartctl's
	 * names and in its order (critical_warning, available_spare, ...); null for
	 * other disks.
	 */
	nvme_health: Record<string, number> | null;
	self_test_status: string | null;
	last_test_result: string | null;
}

export type PredictionStatus = 'healthy' | 'watch' | 'monitor_closely' | 'replace_soon';

/** The failure prediction of a connected disk from its SMART history (ADR-0235). */
export interface DiskPrediction {
	/** smartctl's serial number, as SmartInfo.serial reports it. */
	serial: string;
	/** The disk's path now, as lsblk lists it. */
	device: string;
	risk_score: number;
	status: PredictionStatus;
	/** The reasons of the score. */
	risk_factors: string[];
	recommendation: string;
	data_points: number;
}

export interface HealthOverview {
	total_disks: number;
	healthy: number;
	warning: number;
	failed: number;
	unknown: number;
	/** Disks that sleep, whose SMART data was not read (ADR-0378). */
	standby: number;
	/** SD cards and eMMC, which have no SMART (storage.free-mmc-as-data-disk). */
	no_smart: number;
	average_temperature: number | null;
}

/** A problem of the system disk that its last reads show (supervisor/src/health/system_disk.py). */
export interface SystemDiskProblem {
	kind: 'emmc_worn' | 'system_disk_errors' | 'system_disk_failed';
	severity: 'warning' | 'error';
}

/** The eMMC that holds the root filesystem: its wear and its errors (ADR-0471). */
export interface EmmcHealth {
	/** /dev/mmcblk0 */
	device: string;
	model: string | null;
	/** Bytes. */
	size: number;
	/** The root filesystem's device, /dev/mmcblk0p1. */
	root: string;
	filesystem: string;
	/** 1 to 10: each a tenth of the life used (1: 0 to 10%); 11 past it; 0 not defined; null not read. */
	life_time_a: number | null;
	life_time_b: number | null;
	/** 1 normal, 2 warning, 3 urgent, 0 not defined; null not read. */
	pre_eol: number | null;
	/** mmc: read with mmc extcsd read; sysfs: the kernel's values of the boot; null: not read. */
	wear_source: 'mmc' | 'sysfs' | null;
	wear_read_at: string | null;
	/** The host has no mmc-utils. */
	mmc_missing: boolean;
	/** Why mmc, or also sysfs, gave no wear: English, technical. */
	wear_error: string | null;
	/** Since the supervisor started; null until read. */
	io_errors: number | null;
	/** ext4's errors on the root since the supervisor started; null for another filesystem or until read. */
	ext4_errors: number | null;
	read_only: boolean | null;
	direct_read_failed: boolean;
	checked_at: string | null;
	/** What the last pass could not read: English, technical. */
	check_error: string | null;
}

/** GET /health/system-disk: the eMMC of the root filesystem, or why there is none. */
export interface SystemDiskHealth {
	emmc: EmmcHealth | null;
	/** Why emmc is null. */
	reason: 'not_emmc' | 'no_disk' | 'unreadable' | null;
	/** With not_emmc: the root's disk. */
	system_disk: string | null;
	/** With no_disk and unreadable: English, technical. */
	detail: string | null;
	problems: SystemDiskProblem[];
}

/** The wear and the errors of an eMMC system disk, as the server's last reads found them. */
export async function getSystemDiskHealth(): Promise<SystemDiskHealth> {
	return api.get<SystemDiskHealth>('/health/system-disk');
}

export async function getDiskHealth(): Promise<{ disks: SmartInfo[] }> {
	return api.get<{ disks: SmartInfo[] }>('/health/disks');
}

export async function getDiskSmart(device: string): Promise<SmartInfo> {
	const encodedDevice = encodeURIComponent(device);
	return api.get<SmartInfo>('/health/disk/' + encodedDevice);
}

export async function startSmartTest(
	device: string,
	testType: string = 'short'
): Promise<{ status: string; message: string }> {
	const encodedDevice = encodeURIComponent(device);
	return api.post<{ status: string; message: string }>(
		'/health/disk/' + encodedDevice + '/test?test_type=' + testType,
		{}
	);
}

export async function getHealthOverview(): Promise<HealthOverview> {
	return api.get<HealthOverview>('/health/overview');
}

/**
 * The failure predictions of the disks connected now. Reads the stored SMART
 * history only: no disk is woken. HTTP 500 when the host's disks cannot be listed.
 */
export async function getDiskPredictions(): Promise<{ predictions: DiskPrediction[] }> {
	return api.get<{ predictions: DiskPrediction[] }>('/health/smart/predictions');
}

/** The predictions that mark a disk "Replace soon" (ADR-0235). */
export function replaceSoon(predictions: DiskPrediction[]): DiskPrediction[] {
	return predictions.filter((prediction) => prediction.status === 'replace_soon');
}

export function getHealthColor(status: string): string {
	switch (status) {
		case 'PASSED':
			return 'text-status-success';
		case 'FAILED':
			return 'text-status-error';
		default:
			return 'text-status-warning';
	}
}

export function getHealthBgColor(status: string): string {
	switch (status) {
		case 'PASSED':
			return 'bg-status-success';
		case 'FAILED':
			return 'bg-status-error';
		default:
			return 'bg-status-warning';
	}
}

export function getAttributeStatusColor(status: string): string {
	switch (status) {
		case 'ok':
			return 'text-status-success';
		case 'warning':
			return 'text-status-warning';
		case 'critical':
			return 'text-status-error';
		default:
			return 'text-content-secondary';
	}
}
