import type { Certificate } from '$lib/api/security';

// The labels of the values that the server answers as words (Certificate of $lib/api/security):
// keys of the catalog, by value.
export const CERTIFICATE_TYPES: Record<string, string> = {
	self_signed: 'account.certs.selfSigned',
	custom: 'account.certs.custom'
};
// What browsers get (served_by of CertificateStatus), as the web server answered the NAS (ADR-0404).
export const SERVED_BY: Record<string, string> = {
	caddy_local_ca: 'account.certs.caddyLocalCa',
	stored_certificate: 'account.certs.storedCertificate',
	unknown: 'account.certs.servedUnknown'
};
export const SERVED_BADGES: Record<string, 'info' | 'success' | 'warning'> = {
	caddy_local_ca: 'info',
	stored_certificate: 'success',
	unknown: 'warning'
};

/** The stored certificate of `id` among `listed`. */
export function storedCertificate(listed: Certificate[], id: string | null | undefined): Certificate | undefined {
	return id ? listed.find((cert) => cert.id === id) : undefined;
}
