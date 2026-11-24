import { api } from './client';

// The SD card standby of a system on an eMMC (ADR-0471): /system/standby, an administrator's.

export type StandbyConditionId = 'profile' | 'loader' | 'emmc_root' | 'sd_card' | 'host_tools';

export interface StandbyCondition {
	id: StandbyConditionId;
	met: boolean;
	/** What the server found, in English: the reason when it is not met. */
	detail: string;
}

export interface StandbyCardInfo {
	device: string | null;
	model: string | null;
	serial: string | null;
	size: number | null;
}

export interface StandbySync {
	at: string;
	trigger: string;
	result: 'wrote' | 'unchanged' | 'failed';
	mib: number | null;
	parts: string[];
	generation: number | null;
	error: string | null;
	/** The first changes that the comparison of the system found, as rsync itemizes them. */
	changes: string[];
}

export interface StandbyRun {
	at: string;
	generation: number | null;
	reason: string | null;
}

export interface StandbyProblem {
	kind: string;
	severity: 'error' | 'warning';
}

/** GET /system/standby. */
export interface StandbyStatus {
	available: boolean;
	unavailable_reason: string | null;
	conditions: StandbyCondition[];
	profile: string | null;
	armed: boolean;
	/** Not set up, but a card holds the standby's partitions: a setup that stopped midway, or a card that was
	 * not in its slot when the standby was removed. */
	leftover: boolean;
	card: StandbyCardInfo | null;
	card_present: boolean | null;
	in_boot_path: boolean | null;
	root: 'emmc' | 'card' | null;
	boot_reason: string | null;
	generation: number | null;
	marked_failed: boolean;
	drill_pending: boolean;
	syncing: boolean;
	/** The daily sync's time on the NAS's clock, HH:MM. */
	sync_time: string;
	next_sync_at: string | null;
	last_sync: StandbySync | null;
	last_complete_at: string | null;
	standby_ran: StandbyRun | null;
	switch_ready: boolean;
	problems: StandbyProblem[];
	/** The critical apps and their copies (ADR-0498). */
	apps: StandbyApp[];
	/** The card's space by part; null when it is not read. */
	space: StandbySpace | null;
}

export interface StandbyAppMove {
	at: string;
	reason: 'pool_failed' | 'pool_read_only' | 'drill' | 'planned' | string;
	pool: string;
	/** Whether it runs from its copy; false: it had none and stays stopped. */
	has_copy: boolean;
}

export interface StandbyApp {
	app: string;
	pool: string | null;
	interval_hours: number;
	generation: number | null;
	synced_at: string | null;
	mib_per_sync: number | null;
	data_bytes: number | null;
	images_bytes: number | null;
	error: string | null;
	moved: StandbyAppMove | null;
	/** For an app that runs from the card: why it cannot return to its pool yet; null when it can. */
	return_blocked: string | null;
}

export interface StandbySpace {
	size: number | null;
	free: number | null;
	reserve: number | null;
	system: number | null;
	unos_images: number | null;
	unos_volumes: number | null;
	loaded_images: number | null;
}

export interface StandbyAppsAction {
	dry_run: boolean;
	apps: { app: string; has_copy: boolean; age: string | null; error: string | null }[];
	message: string;
}

export interface StandbyAction {
	dry_run: boolean;
	plan: string[];
	message: string;
	sync: StandbySync | null;
}

export type DrillKind = 'mark' | 'emmc_unreadable' | 'emmc_root_missing';

export function getStandby(): Promise<StandbyStatus> {
	return api.get<StandbyStatus>('/system/standby');
}

export function armStandby(model: string): Promise<StandbyAction> {
	return api.post<StandbyAction>('/system/standby/arm', { model });
}

export function removeStandby(model: string): Promise<StandbyAction> {
	return api.delete<StandbyAction>('/system/standby', { model });
}

export function syncStandby(): Promise<StandbyAction> {
	return api.post<StandbyAction>('/system/standby/sync', {});
}

export function setStandbySyncTime(syncTime: string): Promise<void> {
	return api.put<void>('/system/standby/settings', { sync_time: syncTime });
}

export function startDrill(kind: DrillKind): Promise<StandbyAction> {
	return api.post<StandbyAction>('/system/standby/drill', { kind });
}

export function returnToEmmc(): Promise<StandbyAction> {
	return api.post<StandbyAction>('/system/standby/return', {});
}

export function discardStandby(model: string): Promise<StandbyAction> {
	return api.post<StandbyAction>('/system/standby/discard', { model });
}

/** Move the critical apps to the card: every one as a drill, or those of `pool` before its unmount or removal. */
export function moveCriticalApps(reason: 'drill' | 'planned', pool: string | null = null): Promise<StandbyAppsAction> {
	return api.post<StandbyAppsAction>('/system/standby/apps/move', { reason, pool });
}

/** Return the apps that run from the card to their pool, discarding the card's changes; the card's model typed. */
export function returnCriticalApps(model: string, apps: string[] | null = null): Promise<StandbyAppsAction> {
	return api.post<StandbyAppsAction>('/system/standby/apps/return', { model, apps });
}
