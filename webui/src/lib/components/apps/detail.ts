// The pure parts of an app's details and its install form (AppDetailModal.svelte): ports, data
// folders and variables, as the dialog shows them and as the install request sends them.
import {
	isSetByUnos,
	isUserOrGroup,
	poolHostPath,
	UNOS_SMB_IDS,
	type AppContainer,
	type AppPool,
	type AppTemplate,
	type AppVolume,
	type EnvVar,
	type InstalledApp,
	type PortMapping,
	type VolumeMapping
} from '$lib/api/apps';
import type { Translate } from './size';

export type DetailTab = 'overview' | 'ports' | 'volumes' | 'environment';

// The apps whose database and jobs keep the pool's hard disks awake while they run (ADR-0381, ADR-0415).
export const DISK_AWAKE: ReadonlySet<string> = new Set(['immich', 'bookstack', 'paperless-ngx', 'linkwarden']);

/** Whether `template` runs in the host's network, where Docker discards its port bindings. */
export function isInHostNetwork(template: AppTemplate): boolean {
	return template.network_mode === 'host';
}

/** The host port of `port`: the container port itself for an app in the host's network. */
export function hostPortOf(template: AppTemplate, port: PortMapping): number {
	return isInHostNetwork(template) ? port.container : port.host;
}

export function isValidPort(value: string | undefined): boolean {
	if (value === undefined || !/^\d{1,5}$/.test(value.trim())) return false;
	const port = Number(value);
	return port >= 1 && port <= 65535;
}

/** Host path shown for a volume: the override, else the path on the selected pool. */
export function effectiveHostPath(
	volume: VolumeMapping,
	pool: AppPool | null,
	overrides: Record<string, string>
): string {
	const override = overrides[volume.container];
	if (volume.on_pool && override !== undefined) return override;
	return pool ? poolHostPath(volume, pool.path) : volume.host;
}

/** A folder or file on the selected pool, with where each of the app's containers mounts it. */
export interface DataLocation {
	host: string;
	/** A password file that UNOS generates (ADR-0381). */
	secret: boolean;
	mounts: { container: string; path: string; readonly: boolean }[];
}

/**
 * Where the app's data goes on `pool`: each source once, with its mounts. Only the main
 * container's folders take an override; a password file has a fixed path.
 */
export function locationsOf(
	ofApp: AppContainer[],
	pool: AppPool,
	overrides: Record<string, string>
): DataLocation[] {
	const byHost = new Map<string, DataLocation>();
	for (const container of ofApp) {
		for (const volume of container.spec.volumes) {
			if (!volume.on_pool) continue;
			const host =
				container.companion === null && !volume.generated_secret
					? effectiveHostPath(volume, pool, overrides)
					: poolHostPath(volume, pool.path);
			const location = byHost.get(host) ?? { host, secret: volume.generated_secret, mounts: [] };
			location.mounts.push({ container: container.name, path: volume.container, readonly: volume.readonly });
			byHost.set(host, location);
		}
	}
	return [...byHost.values()];
}

/**
 * The folders of an installed app per container, as its containers really use them: the main
 * container's first, then each companion's in the catalog's order.
 */
export function volumesPerContainer(installed: InstalledApp): { name: string; volumes: AppVolume[] }[] {
	const groups = [
		{ companion: null as string | null, name: installed.name },
		...installed.companions.map((companion) => ({ companion: companion.name as string | null, name: companion.container }))
	];
	return groups
		.map((group) => ({
			name: group.name,
			volumes: installed.volumes.filter((volume) => volume.companion === group.companion)
		}))
		.filter((group) => group.volumes.length > 0);
}

/** Whether an installed app's folder is a password file that UNOS generated (ADR-0381). */
export function isGeneratedSecret(template: AppTemplate, volume: AppVolume): boolean {
	const spec =
		volume.companion === null ? template : template.companions.find((companion) => companion.name === volume.companion);
	return spec?.volumes.some((mapping) => mapping.generated_secret && mapping.container === volume.container) ?? false;
}

/** The host ports that the form changed, by container port. */
export function changedPorts(template: AppTemplate, portValues: Record<number, string>): Record<number, number> {
	const result: Record<number, number> = {};
	// The server refuses to move a port of an app in the host's network: none is sent.
	if (isInHostNetwork(template)) return result;
	for (const port of template.ports) {
		const value = Number(portValues[port.container]);
		if (value !== port.host) result[port.container] = value;
	}
	return result;
}

/** The variables that the form changed. */
export function changedEnv(template: AppTemplate, envValues: Record<string, string>): Record<string, string> {
	const result: Record<string, string> = {};
	for (const env of template.environment) {
		// The server sets PUID and PGID (the template's values, or the group's with the option) and
		// the wiring between the app's containers.
		if (isSetByUnos(env)) continue;
		const value = envValues[env.name] ?? env.value;
		if (value !== env.value) result[env.name] = value;
	}
	return result;
}

/** A variable as UNOS sets it: PUID and PGID the group's in unos-smb, otherwise the template's value. */
export function setByUnosValue(env: EnvVar, inGroup: boolean): string {
	return inGroup && isUserOrGroup(env.name) ? String(UNOS_SMB_IDS[env.name]) : env.value;
}

/** The hint of a variable that UNOS sets, in the language of the page (`t`). */
export function setByUnosHint(
	template: AppTemplate,
	env: EnvVar,
	installed: boolean,
	inGroup: boolean,
	t: Translate
): string {
	// The wiring between an app's containers, and every variable of a companion.
	if (!isUserOrGroup(env.name)) return t('apps.detail.setByUnos');
	if (installed) {
		return t(inGroup ? 'apps.detail.setByUnosIn' : 'apps.detail.setByUnosOutside', { name: template.name });
	}
	if (!template.can_run_in_unos_smb) return t('apps.detail.setByUnos');
	return t(env.name === 'PGID' ? 'apps.detail.setByUnosOptionGroup' : 'apps.detail.setByUnosOption', {
		id: UNOS_SMB_IDS[env.name],
		value: env.value
	});
}
