import { api } from './client';

/**
 * Catalog host paths that start with this placeholder live on the storage pool
 * chosen at install time; the server replaces it with the pool's mount point.
 */
export const POOL_PLACEHOLDER = '{pool}';

/**
 * PUID and PGID of an app installed to run in the group unos-smb (ADR-0274): the user of
 * such apps, which no other app has (SMB_APP_UID of supervisor/src/shares/constants.py),
 * and the group id of unos-smb (SMB_GROUP_GID). The server sets both; UMASK becomes 007.
 */
export const UNOS_SMB_IDS: Readonly<Record<'PUID' | 'PGID', number>> = { PUID: 80000, PGID: 70000 };

/** The variables of the user and group of an app's processes, which only the server sets (ADR-0274). */
export function isUserOrGroup(name: string): name is 'PUID' | 'PGID' {
	return name === 'PUID' || name === 'PGID';
}

/**
 * Whether only the server sets a variable of an app's main container: PUID and PGID, and the wiring
 * between the app's containers (`editable` false, ADR-0381), whose override the server refuses.
 */
export function isSetByUnos(env: Pick<EnvVar, 'name' | 'editable'>): boolean {
	return isUserOrGroup(env.name) || env.editable === false;
}

export interface PortMapping {
	host: number;
	container: number;
	protocol: string;
}

export interface VolumeMapping {
	host: string;
	container: string;
	readonly: boolean;
	/** The source is a single file, not a directory. */
	is_file: boolean;
	/** Owner the server gives a folder it creates (images that run as a non-root user). */
	uid?: number | null;
	gid?: number | null;
	/** The source lives on the selected pool; otherwise it is a fixed host path. */
	on_pool: boolean;
	/**
	 * A password file that the server creates at the install, never shows and never replaces
	 * (ADR-0381); its host path cannot be changed.
	 */
	generated_secret: boolean;
	/** The template host path of the data folder that was set up with that password. */
	secret_for: string | null;
}

export interface EnvVar {
	name: string;
	value: string;
	description: string;
	required: boolean;
	/** Password, token or key: mask it; placeholders like "changeme" are rejected. */
	secret: boolean;
	/** False for wiring that the server sets, such as the name of a companion; an override is refused (ADR-0381). */
	editable: boolean;
}

/**
 * Hardware of a SoC that a container of an app gets when it is installed on a host with that SoC
 * and the nodes of `requires` (ADR-0382): the device nodes the host has of `devices`, Docker's
 * masks without `unmasked_paths`, and `image` in place of the container's own.
 */
export interface HardwareProfile {
	id: string;
	/** A name of the SoC in the host's device tree. */
	soc: string;
	devices: string[];
	/**
	 * The nodes without which the container cannot use the hardware, each a device of `devices` or
	 * a path inside a device folder of it; a container without them did not get it.
	 */
	requires: string[];
	unmasked_paths: string[];
	apparmor_unconfined: boolean;
	/** The image the container runs where the profile applies; the catalog's template names it already. */
	image: string | null;
}

/** What the server needs to create one container of an app (ADR-0381): the main container's or a companion's. */
export interface ContainerSpec {
	image: string;
	volumes: VolumeMapping[];
	environment: EnvVar[];
	/** The hardware of this host's SoC that the container gets; empty on any other host. */
	hardware: HardwareProfile[];
	/** Container paths kept in RAM, with Docker's tmpfs options. */
	tmpfs: Record<string, string>;
	shm_size: string | null;
	/** Seconds Docker gives the container to stop; null: 10. */
	stop_timeout: number | null;
	/** The platforms its image publishes ("linux/arm64"), recorded from the registry; empty when none is recorded (ADR-0403). */
	platforms: string[];
}

/**
 * A companion container of an app (ADR-0381): it publishes no port and takes no override, and is
 * the container `<app id>-<name>` on the app's network, where it answers to `name`.
 */
export interface Companion extends ContainerSpec {
	name: string;
}

/** An app of the catalog: its main container's spec, with its companions'. */
export interface AppTemplate extends ContainerSpec {
	id: string;
	name: string;
	description: string;
	icon: string;
	category: string;
	version: string;
	website: string;
	source?: string;
	github?: string;
	documentation?: string;
	min_memory?: string;
	recommended_memory?: string;
	/** The main container's ports; companions publish none. */
	ports: PortMapping[];
	/**
	 * "host" for an app in the host's network: Docker discards its port bindings, so the app binds
	 * its container ports on the host itself, and the server refuses an override that moves one
	 * (ADR-0381). Null: a network of Docker's.
	 */
	network_mode: string | null;
	/**
	 * The app can be installed to run in the group unos-smb: its image takes PUID, PGID and
	 * UMASK (linuxserver.io), and it has a folder besides its own data. Only such an app is
	 * given a share's folder.
	 */
	can_run_in_unos_smb: boolean;
	/** Created and started in this order before the main container, and stopped after it. */
	companions: Companion[];
	/** The companions that must be ready before the main container starts. */
	depends_on: string[];
	/** The platform of the host ("linux/arm64"); null when the server cannot name it (ADR-0403). */
	host_platform: string | null;
	/** False when an image of one of its containers publishes no build for the host's platform: the server refuses its install (409). */
	available: boolean;
	/** About what the images that its install pulls on this host take on the system disk, in bytes (ADR-0415). */
	system_disk_bytes: number | null;
}

/** A container of an app: its Docker name, its companion's name (null for the main container) and its spec. */
export interface AppContainer {
	name: string;
	companion: string | null;
	spec: ContainerSpec;
}

/**
 * The containers of `app`, the main container first: it is named after the app, a companion
 * `<app id>-<name>` (ADR-0381).
 */
export function appContainers(app: AppTemplate): AppContainer[] {
	return [
		{ name: app.id, companion: null, spec: app },
		...app.companions.map((companion) => ({
			name: `${app.id}-${companion.name}`,
			companion: companion.name,
			spec: companion
		}))
	];
}

/**
 * A container's limits as Docker keeps them and its use as UNOS last read it from the kernel
 * (ADR-0390): bytes and CPUs; null for no limit, and for a use not read (a container that does
 * not run, or before the first read).
 */
export interface ContainerResources {
	memory_limit: number | null;
	cpu_limit: number | null;
	memory_used: number | null;
	/** The CPUs it used on average between the last two reads. */
	cpu_used: number | null;
	sampled_at: string | null;
}

/** What the limits of a container may be on this NAS (GET /apps/limits). */
export interface LimitBounds {
	memory_min: number;
	/** The memory of the NAS: a memory limit must be below it. */
	memory_max: number;
	cpu_min: number;
	cpu_max: number;
}

/** The limits of one container after a change: null for none. */
export interface ContainerLimits {
	container: string;
	memory_limit: number | null;
	cpu_limit: number | null;
}

/** A companion container of an installed app, as Docker reports it (ADR-0381). */
export interface CompanionContainer {
	/** The companion's name in the app, and its container's name and id. */
	name: string;
	container: string;
	container_id: string;
	status: string;
	/** Docker's health of the container (starting, healthy, unhealthy); null without a health check. */
	health: string | null;
	image: string;
	/** The host's device nodes passed into the container (ADR-0382). */
	devices: string[];
	resources: ContainerResources;
}

/**
 * An installed app (ADR-0381): the fields of one container are its main container's; while Docker
 * lacks that, `status` is "missing" and `container_id` is a companion's, which names the app too.
 */
export interface InstalledApp {
	id: string;
	template_id: string;
	name: string;
	container_id: string;
	/** The main container's status (Docker's), or "missing". */
	status: string;
	web_port: number | null;
	created_at: string;
	/** Pool chosen at install time; null for an app without data on a pool. */
	pool: string | null;
	/** The bind mounts of the app's containers with the host paths they really use. */
	volumes: AppVolume[];
	/** Data folders that are not on a mounted pool, as their pool is not mounted. The server refuses to start such an app. */
	data_outside_pools: string[];
	/** The app runs in the group unos-smb (PUID 80000, PGID 70000): its container says so. */
	runs_in_unos_smb: boolean;
	/** The host's device nodes passed into the main container (ADR-0382), as Docker keeps them. */
	devices: string[];
	/** Docker's health of the main container; null without a health check. */
	health: string | null;
	/** The app's companion containers that Docker has, in the catalog's order. */
	companions: CompanionContainer[];
	/** The names of the app's containers that Docker lacks: such an app does not start. */
	missing: string[];
	/** The main container's limits and use (ADR-0390). */
	resources: ContainerResources;
	/** The catalog's version that the main container was created for; null before the label (ADR-0408). */
	version: string | null;
	/** The catalog's version that the app can be updated to; null: no update (ADR-0408). */
	update_to: string | null;
	/** The earlier version that the app's last update kept, until a rollback or its removal. */
	previous: AppEarlierVersion | null;
}

/** The earlier version of an app that its last update kept, stopped (ADR-0408). */
export interface AppEarlierVersion {
	version: string | null;
	/** Its containers, named `<name>.previous`, and their images. */
	containers: string[];
	images: string[];
	/** The copies of the app's database made before the update that are there. */
	copies: string[];
}

/** One container of an app's update (ContainerUpdate of supervisor/src/apps/schemas.py). */
export interface ContainerUpdate {
	name: string;
	companion: string | null;
	/** The image it runs now; null for a container that the update adds. */
	image_from: string | null;
	/** The catalog's image it gets; null for a container that the catalog no longer has. */
	image_to: string | null;
	changed: boolean;
	built_from: string | null;
	built_to: string | null;
}

/** A data folder that an update copies on its pool before the new version starts. */
export interface DatabaseCopy {
	container: string;
	folder: string;
	copy_folder: string;
	bytes: number;
	pool: string;
	pool_free_bytes: number;
}

/** What an update of an installed app would do (GET /apps/installed/{id}/update, ADR-0408). */
export interface AppUpdatePlan {
	template_id: string;
	name: string;
	version_from: string | null;
	version_to: string;
	platform: string;
	containers: ContainerUpdate[];
	/** What the pulls download, what they write to the system disk and what it has free now. */
	download_bytes: number;
	system_disk_bytes: number;
	system_disk_free_bytes: number | null;
	copies: DatabaseCopy[];
}

/** A hardware profile of one container of an app: `container` is its companion's name, or null for the main container. */
export interface ContainerProfile {
	container: string | null;
	profile: HardwareProfile;
}

/** The hardware profiles of every container of `app`, the main container's first (ADR-0382). */
export function allProfiles(app: AppTemplate): ContainerProfile[] {
	return [
		...app.hardware.map((profile) => ({ container: null, profile })),
		...app.companions.flatMap((companion) =>
			companion.hardware.map((profile) => ({ container: companion.name, profile }))
		)
	];
}

/**
 * Whether an app's container got the hardware of `profile`: every node it requires, as a device it
 * got or inside a device folder it got (ADR-0382: the NPU's link in /dev/dri). The container is an
 * installed app's main container, the answer of its install, or one of their companions.
 */
export function hasHardware(container: Pick<InstalledApp, 'devices'>, profile: HardwareProfile): boolean {
	return profile.requires.every((node) =>
		container.devices.some((device) => node === device || node.startsWith(`${device}/`))
	);
}

/** An installed app or the answer of its install: its main container's state and devices, and its companions. */
export type InstalledContainers = Pick<InstalledApp, 'status' | 'devices' | 'companions'>;

/** The container of an installed app that `entry` is for; null when Docker lacks it. */
function containerOf(installed: InstalledContainers, entry: ContainerProfile): Pick<InstalledApp, 'devices'> | null {
	if (entry.container === null) return installed.status === 'missing' ? null : installed;
	return installed.companions.find((companion) => companion.name === entry.container) ?? null;
}

/**
 * The hardware profiles of `app` whose container in `installed` got the hardware, and those whose
 * container did not (ADR-0382). A container that Docker lacks is in neither: the app is incomplete.
 */
export function installedHardware(
	app: AppTemplate,
	installed: InstalledContainers
): { got: ContainerProfile[]; missing: ContainerProfile[] } {
	const got: ContainerProfile[] = [];
	const missing: ContainerProfile[] = [];
	for (const entry of allProfiles(app)) {
		const container = containerOf(installed, entry);
		if (container !== null) (hasHardware(container, entry.profile) ? got : missing).push(entry);
	}
	return { got, missing };
}

/** The hardware profiles of `app` whose container in `installed` did not get the hardware (ADR-0382). */
export function missingHardware(app: AppTemplate, installed: InstalledContainers): ContainerProfile[] {
	return installedHardware(app, installed).missing;
}

/** A mounted storage pool that can hold app data. */
export interface AppPool {
	name: string;
	path: string;
	free_bytes: number;
	total_bytes: number;
}

/** A bind mount with the host path the server actually used. */
export interface AppVolume {
	host: string;
	container: string;
	readonly: boolean;
	/** The companion whose container has the mount; null: the main container's. */
	companion: string | null;
}

/** An install: one instance of each app, named after its template (ADR-0381). */
export interface AppInstallRequest {
	template_id: string;
	/** Pool for the app's data; omitted = first mounted pool by name. */
	pool?: string;
	/** The overrides change the main container only; companions take none (ADR-0381). */
	port_overrides?: Record<number, number>;
	/** Container path -> host path inside a mounted pool; never a generated password file's. */
	volume_overrides?: Record<string, string>;
	/** Values of the main container's variables; never those that the server sets (isSetByUnos). */
	env_overrides?: Record<string, string>;
	/**
	 * Run the app in the group unos-smb (PUID 80000, PGID 70000, UMASK 007); only with
	 * can_run_in_unos_smb.
	 */
	run_in_unos_smb?: boolean;
}

export interface AppInstallResponse {
	id: string;
	name: string;
	container_id: string;
	status: string;
	web_url: string | null;
	/** Pool holding the app's data, or null if it stores nothing on a pool. */
	pool: string | null;
	volumes: AppVolume[];
	/** The host's device nodes passed into the main container (ADR-0382). */
	devices: string[];
	/** Docker's health of the main container right after its start ("starting"); null without a health check. */
	health: string | null;
	/** The app's companion containers, in the order they were started. */
	companions: CompanionContainer[];
}

/** The pull of one image of an install job, as Docker's pull stream reports it (ADR-0384). */
export interface ImagePull {
	/** The reference as the install pulls it: name:tag@sha256:... for a pinned image. */
	image: string;
	/** present: a pinned image that Docker has, not pulled. */
	state: 'waiting' | 'present' | 'pulling' | 'pulled' | 'failed';
	/** The layers that Docker named so far, and those it has. */
	layers: number;
	layers_done: number;
	/** The bytes of the layers whose download size Docker reported so far, and of them downloaded. */
	bytes_total: number;
	bytes_done: number;
}

/** One container of an install job, in the start order (ADR-0384). */
export interface ContainerStart {
	/** Docker's name. */
	name: string;
	/** The companion's name in the app; null for the main container. */
	companion: string | null;
	/** failed: its create, start or readiness failed; removed: by the clean-up of a failed install. */
	state: 'waiting' | 'created' | 'starting' | 'running' | 'failed' | 'removed';
}

/**
 * An app's install, which runs in the background (ADR-0384): POST /apps/install answers it at once,
 * running, and /ws/v1/events sends administrators each new state and its end (`appInstalls` of
 * $lib/stores/appInstalls). Kept in the supervisor's memory: the running one and the last of each app.
 */
export interface AppInstallJob {
	id: string;
	template_id: string;
	/** The app's name in the catalog. */
	name: string;
	/** An install, an update to the catalog's newer images or a rollback to the earlier version (ADR-0408). */
	kind: 'install' | 'update' | 'rollback';
	/** cancelled: an administrator cancelled it, and what it made was removed. */
	state: 'running' | 'succeeded' | 'failed' | 'cancelled';
	/**
	 * The step reached: the checks before any pull, the pulls, the creates, the starts, and the
	 * removal of what a failed or cancelled install made; an update also stops the app, copies its
	 * database and, after a failure, restores the earlier version.
	 */
	step: 'checking' | 'pulling' | 'stopping' | 'copying' | 'creating' | 'starting' | 'cleaning' | 'restoring';
	started_at: string;
	finished_at: string | null;
	/** Each image once, listed once the checks passed. */
	images: ImagePull[];
	containers: ContainerStart[];
	/** What the install made, once it succeeded. */
	result: AppInstallResponse | null;
	/** Why it failed or what its cancel did (the server's message), and the HTTP status of a failure. */
	error: string | null;
	error_status: number | null;
}

export async function getAppCatalog(category?: string, query?: string): Promise<AppTemplate[]> {
	const params = new URLSearchParams();
	if (category) params.append('category', category);
	if (query) params.append('query', query);
	const qs = params.toString();
	return api.get<AppTemplate[]>(`/apps/catalog${qs ? '?' + qs : ''}`);
}

export async function getAppTemplate(appId: string): Promise<AppTemplate> {
	return api.get<AppTemplate>(`/apps/catalog/${appId}`);
}

/** Mounted storage pools sorted by name; the first one is the server's default. */
export async function getAppPools(): Promise<AppPool[]> {
	return api.get<AppPool[]>('/apps/pools');
}

export async function getInstalledApps(): Promise<InstalledApp[]> {
	return api.get<InstalledApp[]>('/apps/installed');
}

/** Start an install: the job, running at its checks (202); its progress comes over /ws/v1/events. */
export async function installApp(req: AppInstallRequest): Promise<AppInstallJob> {
	return api.post<AppInstallJob>('/apps/install', req);
}

/** An install job as the server keeps it now; ApiError 404 once it is gone (the supervisor restarted). */
export async function getInstallJob(id: string): Promise<AppInstallJob> {
	return api.get<AppInstallJob>(`/apps/install/jobs/${encodeURIComponent(id)}`);
}

/** Forget an ended install job (Dismiss): the App Store no longer shows it. */
export async function dismissInstallJob(id: string): Promise<void> {
	await api.delete(`/apps/install/jobs/${encodeURIComponent(id)}`);
}

/**
 * Cancel the running install job: it stops at its next step, also during a pull, and what it made
 * is removed; the events socket then sends it cancelled (ADR-0384).
 */
export async function cancelInstallJob(id: string): Promise<void> {
	await api.delete(`/apps/install/jobs/${encodeURIComponent(id)}`);
}

export async function uninstallApp(containerId: string): Promise<void> {
	return api.delete(`/apps/installed/${containerId}`);
}

/** What an update of the app would do; the server reads the registries for it (ADR-0408). */
export async function getUpdatePlan(containerId: string): Promise<AppUpdatePlan> {
	return api.get<AppUpdatePlan>(`/apps/installed/${encodeURIComponent(containerId)}/update`);
}

/** Start the update of the app to the catalog's newer images: its job, which the events socket follows. */
export async function updateApp(containerId: string): Promise<AppInstallJob> {
	return api.post<AppInstallJob>(`/apps/installed/${encodeURIComponent(containerId)}/update`);
}

/** Start the rollback of the app to the earlier version that its update kept: its job. */
export async function rollbackApp(containerId: string): Promise<AppInstallJob> {
	return api.post<AppInstallJob>(`/apps/installed/${encodeURIComponent(containerId)}/rollback`);
}

/** Remove the earlier version that the app's update kept, and the copy of its database. */
export async function removeEarlierVersion(containerId: string): Promise<void> {
	await api.delete(`/apps/installed/${encodeURIComponent(containerId)}/previous`);
}

export async function startApp(containerId: string): Promise<void> {
	return api.post(`/apps/installed/${containerId}/start`);
}

export async function stopApp(containerId: string): Promise<void> {
	return api.post(`/apps/installed/${containerId}/stop`);
}

export async function restartApp(containerId: string): Promise<void> {
	return api.post(`/apps/installed/${containerId}/restart`);
}

export async function getLimitBounds(): Promise<LimitBounds> {
	return api.get<LimitBounds>('/apps/limits');
}

/**
 * Set the limits of one container of an app, named by its name or id; null removes a limit
 * (ADR-0390). Docker gets them at once, and UNOS keeps them for the app's next install.
 */
export async function setContainerLimits(
	container: string,
	limits: { memory_limit: number | null; cpu_limit: number | null }
): Promise<ContainerLimits> {
	return api.put<ContainerLimits>(`/apps/installed/${encodeURIComponent(container)}/limits`, limits);
}

/** Host path a catalog volume gets on a pool; fixed host paths are returned unchanged. */
export function poolHostPath(volume: VolumeMapping, poolPath: string): string {
	if (!volume.on_pool) return volume.host;
	return poolPath + volume.host.slice(POOL_PLACEHOLDER.length);
}

/** Collapse "." and ".." segments of an absolute path; null if it is not absolute. */
function normalizePath(path: string): string | null {
	if (!path.startsWith('/')) return null;
	const parts: string[] = [];
	for (const part of path.split('/')) {
		if (part === '' || part === '.') continue;
		if (part === '..') parts.pop();
		else parts.push(part);
	}
	return '/' + parts.join('/');
}

/**
 * Whether `path` lies inside one of the pools. This is only a lexical pre-check
 * for the form; the server resolves symlinks and has the final say.
 */
export function isInsidePools(path: string, pools: AppPool[]): boolean {
	const normalized = normalizePath(path.trim());
	if (normalized === null) return false;
	return pools.some((pool) => normalized === pool.path || normalized.startsWith(pool.path + '/'));
}

/** Readable text for a failed request, with a fallback for empty or opaque messages. */
export function describeApiError(e: unknown, fallback: string): string {
	const message = e instanceof Error ? e.message.trim() : '';
	// An error object stringified into the message carries no information.
	if (!message || message.includes('[object Object]')) return fallback;
	return message;
}
