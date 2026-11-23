import { getWolStatus, type WolInterface } from '$lib/api/power';
import { settle, type Settled } from '$lib/pages/read';

/**
 * The host's network interfaces and their Wake-on-LAN. The page creates it and gives it what its
 * load read (apply); Retry and a change read them here.
 */
export class WakeOnLan {
	interfaces = $state<WolInterface[]>([]);
	loading = $state(true);
	error = $state('');
	// The interface whose Wake-on-LAN is being changed.
	busy = $state<string | null>(null);

	async load() {
		this.apply(await settle(getWolStatus().then((answer) => answer.interfaces)));
	}

	apply(read: Settled<WolInterface[]>) {
		if (read.ok) this.interfaces = read.value;
		this.error = read.ok ? '' : read.error;
		this.loading = false;
	}
}
