// System > Security's reads (routes/system/security/+page.ts, ADR-0431): whether the administrator
// has two-factor authentication (turning the requirement on or off takes a code of it), the
// requirement of every account, the host's firewall, and the certificates HTTPS serves: their
// status first and then the stored ones, as the card read them (ADR-0404).
import { getTOTPStatus, type TOTPStatus } from '$lib/api/auth';
import {
	getCertificateStatus,
	getCertificates,
	getFirewallStatus,
	getTwoFactorRequirement,
	type CertificateListResponse,
	type CertificateStatus,
	type FirewallStatus,
	type TwoFactorRequirement
} from '$lib/api/security';
import type { PageReader } from '$lib/pages/load';
import { settle, type Settled } from '$lib/pages/read';

export type CertificatesRead = {
	status: Settled<CertificateStatus>;
	/** null when the status could not be read: the list was not asked for then. */
	list: Settled<CertificateListResponse> | null;
};

export type SecuritySnapshot = {
	twoFactor: Settled<TOTPStatus>;
	requirement: Settled<TwoFactorRequirement>;
	firewall: Settled<FirewallStatus>;
	/** When the firewall answered (Date.now()): a change that waits to be kept is undone revert_in after it. */
	firewallAt: number;
	certificates: CertificatesRead;
};

async function readCertificates(): Promise<CertificatesRead> {
	const status = await settle(getCertificateStatus());
	return { status, list: status.ok ? await settle(getCertificates()) : null };
}

async function readSecurity(): Promise<SecuritySnapshot> {
	let firewallAt = 0;
	const [twoFactor, requirement, firewall, certificates] = await Promise.all([
		settle(getTOTPStatus()),
		settle(getTwoFactorRequirement()),
		settle(getFirewallStatus().finally(() => (firewallAt = Date.now()))),
		readCertificates()
	]);
	return { twoFactor, requirement, firewall, firewallAt, certificates };
}

export const securityReader: PageReader<SecuritySnapshot> = {
	key: 'system:security',
	// Remote access and the apps' names (/network/...) add and remove the firewall's rules.
	areas: ['auth', 'security', 'network'],
	adminOnly: true,
	read: readSecurity
};
