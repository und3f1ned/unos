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

/** The badge of a container's state. */
export function stateVariant(
	state: string
): 'success' | 'error' | 'warning' | 'info' | 'neutral' | 'purple' | 'cyan' | 'orange' {
	switch (state) {
		case 'running':
			return 'success';
		case 'exited':
			return 'error';
		case 'paused':
			return 'warning';
		case 'restarting':
			return 'info';
		default:
			return 'neutral';
	}
}

/** The address of the container's first port published on the host, on this host's name. */
export function webUrl(container: Container): string | null {
	const webPort = container.ports.find((p) => p.host_port);
	if (webPort) {
		return `http://${window.location.hostname}:${webPort.host_port}`;
	}
	return null;
}
