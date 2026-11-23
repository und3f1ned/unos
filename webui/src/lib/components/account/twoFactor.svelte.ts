import { messageOf } from '$lib/errors';
import {
	getRecoveryCodesStatus,
	getSecurityKeys,
	getTOTPStatus,
	type RecoveryCodesStatus,
	type SecurityKey,
	type TOTPSetupResponse,
	type TOTPStatus
} from '$lib/api/auth';
import { settle, type Settled } from '$lib/pages/read';

/**
 * The account's two-factor authentication: its status, the recovery codes' count and the security
 * keys while it is on, and the setup or the turning off in progress, which the card of the Account
 * page's Sign-in and security and its dialogs share. The page creates it and gives it what its load
 * read ($lib/pages/account), so that the tab shows it again at once when it is drawn again.
 */
export class TwoFactor {
	// Until the status has loaded, the card offers neither Enable nor Disable: a status that could
	// not be loaded is reported, not shown as off.
	enabled = $state(false);
	// Every account must have two-factor authentication: turning it off says what follows.
	required = $state(false);
	statusLoaded = $state(false);
	statusLoading = $state(false);
	statusError = $state('');
	// While it is on: the recovery codes' count and the security keys as last read, or why they could
	// not be; null until they are read.
	codes = $state.raw<Settled<RecoveryCodesStatus> | null>(null);
	keys = $state.raw<Settled<SecurityKey[]> | null>(null);
	// A setup, an enable or a disable is waiting for the server.
	loading = $state(false);
	setupData = $state<TOTPSetupResponse | null>(null);
	// The code typed to confirm the setup.
	code = $state('');
	error = $state('');
	setupOpen = $state(false);
	disableOpen = $state(false);
	// The code typed to turn it off.
	disableCode = $state('');
	// The otpauth:// URI contains the TOTP secret, so the QR code is rendered in
	// the browser and never sent to a third-party service.
	qrDataUrl = $state('');

	/**
	 * What the page's load read: the status, and the codes and the keys when it is on. A status that
	 * could not be read leaves what is shown, the codes and the keys with it.
	 */
	apply(status: Settled<TOTPStatus>, codes: Settled<RecoveryCodesStatus> | null, keys: Settled<SecurityKey[]> | null) {
		if (!status.ok) {
			this.statusError = status.error;
			return;
		}
		this.enabled = status.value.enabled;
		this.required = status.value.required;
		this.statusLoaded = true;
		this.statusError = '';
		this.codes = codes;
		this.keys = keys;
	}

	async loadStatus() {
		this.statusLoading = true;
		try {
			const status = await getTOTPStatus();
			this.enabled = status.enabled;
			this.required = status.required;
			this.statusLoaded = true;
			this.statusError = '';
		} catch (e) {
			this.statusError = messageOf(e);
		} finally {
			this.statusLoading = false;
		}
	}

	/** The card's Retry of the status: the codes and the keys too, when it is on and they are not read. */
	async retry() {
		await this.loadStatus();
		if (this.enabled && (this.codes === null || this.keys === null)) await this.loadParts();
	}

	/** Turned on or off on the page: the codes and the keys are new, read now while it is on. */
	turned(on: boolean) {
		this.enabled = on;
		this.codes = null;
		this.keys = null;
		if (on) void this.loadParts();
	}

	/** The tab that shows the dialogs has gone: a setup or a turning off in progress ends with it. */
	closeDialogs() {
		this.setupOpen = false;
		this.setupData = null;
		this.qrDataUrl = '';
		this.code = '';
		this.disableOpen = false;
		this.disableCode = '';
		this.error = '';
	}

	async loadParts() {
		const [codes, keys] = await Promise.all([settle(getRecoveryCodesStatus()), settle(getSecurityKeys())]);
		if (!this.enabled) return;
		this.codes = codes;
		this.keys = keys;
	}
}
