import { getNfsExports, getNfsStatus, type NfsExport, type NfsServiceStatus } from '$lib/api/shares';
import { messageOf } from './errors';

/** The NFS exports and the status of the NFS server as the page last read them. */
export class NfsExports {
	exports = $state<NfsExport[]>([]);
	error = $state('');
	status = $state<NfsServiceStatus | null>(null);
	statusError = $state('');

	/** The exports and the status, at once. */
	async load() {
		await Promise.all([this.loadExports(), this.loadStatus()]);
	}

	async loadExports() {
		try {
			this.exports = await getNfsExports();
			this.error = '';
		} catch (e) {
			this.error = messageOf(e);
		}
	}

	async loadStatus() {
		try {
			this.status = await getNfsStatus();
			this.statusError = '';
		} catch (e) {
			this.status = null;
			this.statusError = messageOf(e);
		}
	}
}
