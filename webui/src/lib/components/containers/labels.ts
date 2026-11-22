// The words and looks of a container that the Containers page and its drawer share (ADR-0255).
import type { Container } from '$lib/api/containers';
import type { Translate } from '$lib/i18n';

/** What a container's own buttons do on the page and in its drawer. */
export type QuickAction = 'start' | 'stop' | 'restart' | 'remove';

// Docker's words that the server passes on: keys of the catalog, by value. A container's state
// and status (State.Status) and a port's protocol.
export const STATES: Record<string, string> = {
	created: 'containers.state.created',
	running: 'containers.state.running',
	paused: 'containers.state.paused',
	restarting: 'containers.state.restarting',
	removing: 'containers.state.removing',
	exited: 'containers.state.exited',
	dead: 'containers.state.dead'
};
export const PROTOCOLS: Record<string, string> = {
	tcp: 'containers.protocol.tcp',
	udp: 'containers.protocol.udp',
	sctp: 'containers.protocol.sctp'
};

/** The label of `value` in the language of the page; a value without one as it comes. */
export function labelOf(labels: Record<string, string>, value: string, t: Translate): string {
	return Object.prototype.hasOwnProperty.call(labels, value) ? t(labels[value]) : value;
}

/**
 * The badge of a container's state: green while it runs, neutral when it is stopped or was never
 * started (a normal state, not an error), yellow while paused, blue while it restarts, red for a
 * dead one (webui.ux-apps).
 */
export function stateVariant(state: string): 'success' | 'error' | 'warning' | 'info' | 'neutral' {
	switch (state) {
		case 'running':
			return 'success';
		case 'paused':
			return 'warning';
		case 'restarting':
			return 'info';
		case 'dead':
			return 'error';
		default:
			return 'neutral';
	}
}

/**
 * The groups of the state filter, which split every container once: running, stopped (exited, as
 * the dashboard counts it) and every other state (paused, restarting, created, removing, dead),
 * so that the counts add up to All.
 */
export type StateGroup = 'running' | 'stopped' | 'other';
export type StateFilter = 'all' | StateGroup;

export function groupOf(state: string): StateGroup {
	if (state === 'running') return 'running';
	if (state === 'exited') return 'stopped';
	return 'other';
}

/** The number of containers in each group, and all of them. */
export function countByGroup(containers: Container[]): Record<StateFilter, number> {
	const counts = { all: containers.length, running: 0, stopped: 0, other: 0 };
	for (const container of containers) counts[groupOf(container.state)]++;
	return counts;
}

/** The app of the App Store that made the container, by the label UNOS gives it (ADR-0381). */
export function appOf(container: Container): string | null {
	return container.labels?.['unos.app'] ?? null;
}

/** The address of the container's first port published on the host, on this host's name. */
export function webUrl(container: Container): string | null {
	const webPort = container.ports.find((p) => p.host_port);
	if (webPort) {
		return `http://${window.location.hostname}:${webPort.host_port}`;
	}
	return null;
}
