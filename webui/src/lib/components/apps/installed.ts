// What the App Store says of an installed app: its state, health, containers and address.
import type { InstalledApp } from '$lib/api/apps';

/** The installed instance of a catalog's app, if any. */
export function installedOf(installed: InstalledApp[], templateId: string): InstalledApp | undefined {
	return installed.find((i) => i.template_id === templateId);
}

/** The state of an installed app: incomplete while Docker lacks one of its containers (ADR-0381). */
export function stateOf(app: InstalledApp): { key: string; variant: 'success' | 'neutral' | 'warning' } {
	if (app.missing.length > 0) return { key: 'apps.state.incomplete', variant: 'warning' };
	if (app.status === 'running') return { key: 'apps.state.running', variant: 'success' };
	return { key: 'apps.state.stopped', variant: 'neutral' };
}

// Docker's health of a running app's main container: its word, and its badge.
export const HEALTH: Record<string, { key: string; variant: 'info' | 'success' | 'error' }> = {
	starting: { key: 'apps.health.starting', variant: 'info' },
	healthy: { key: 'apps.health.healthy', variant: 'success' },
	unhealthy: { key: 'apps.health.unhealthy', variant: 'error' }
};

/** The names of the containers of an installed app that Docker has, its main container first. */
export function containerNames(app: InstalledApp): string[] {
	return [...(app.status === 'missing' ? [] : [app.name]), ...app.companions.map((c) => c.container)];
}

/** The names of the companions that do not run while the app's main container runs. */
export function stoppedCompanions(app: InstalledApp): string[] {
	if (app.status !== 'running') return [];
	return app.companions.filter((c) => c.status !== 'running').map((c) => c.container);
}

/** The address of the app's web interface on this host's name, if it has one. */
export function webUrl(app: InstalledApp): string | null {
	if (app.web_port) {
		return `http://${window.location.hostname}:${app.web_port}`;
	}
	return null;
}
