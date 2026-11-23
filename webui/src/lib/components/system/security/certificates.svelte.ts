import { get } from 'svelte/store';
import { getCertificateStatus, getCertificates, type Certificate, type CertificateStatus } from '$lib/api/security';
import { t } from '$lib/i18n';
import { settle } from '$lib/pages/read';
import type { CertificatesRead } from '$lib/pages/system/security';

/**
 * The web server's certificates (ADR-0404): what browsers get, the stored certificates, and the
 * serving or generation in progress, which the card of System > Security and its dialogs share.
 * The page creates it and gives it what its load read (apply); the card reads again after a change.
 */
export class Certificates {
	status = $state<CertificateStatus | null>(null);
	list = $state<Certificate[]>([]);
	// Until the stored certificates have loaded, the card does not say that there are none.
	loaded = $state(false);
	loading = $state(false);
	// Why the last load of the certificates failed: reset by every load.
	loadError = $state('');
	// Why a delete or a serving failed; why a generation failed, shown in its dialog. What an action
	// did is a toast (ADR-0432).
	error = $state('');
	generateError = $state('');
	generateOpen = $state(false);

	async load() {
		this.loading = true;
		this.loadError = '';
		const status = await settle(getCertificateStatus());
		this.apply({ status, list: status.ok ? await settle(getCertificates()) : null });
		this.loading = false;
	}

	/** The status, and then the stored certificates, as read. */
	apply(read: CertificatesRead) {
		if (read.status.ok) this.status = read.status.value;
		if (read.list?.ok) {
			this.list = read.list.value.certificates;
			// The later answer of the two.
			this.status = read.list.value.status;
			this.loaded = true;
			this.loadError = '';
			return;
		}
		// No list is known: not the one of an earlier load, and not "none".
		this.list = [];
		this.loaded = false;
		const failed = read.list ?? read.status;
		this.loadError = get(t)('account.certs.loadFailed', { error: failed.ok ? '' : failed.error });
	}

	openGenerate() {
		this.generateError = '';
		this.generateOpen = true;
	}
}
