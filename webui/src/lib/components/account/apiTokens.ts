// The words of the API tokens of the signed-in account (ADR-0239, ADR-0392), which the card, its
// list and its dialogs share.
import type { ApiToken, TokenArea } from '$lib/api/auth';
import type { Translate } from '$lib/i18n';
import { apiTime } from '$lib/time';

/** The page's $t: a key's text in the language of the page. */

export const AREA_LABELS: Record<TokenArea, string> = {
	apps: 'tokens.area.apps',
	audit: 'tokens.area.audit',
	auth: 'tokens.area.auth',
	backup: 'tokens.area.backup',
	containers: 'tokens.area.containers',
	files: 'tokens.area.files',
	groups: 'tokens.area.groups',
	health: 'tokens.area.health',
	logs: 'tokens.area.logs',
	monitor: 'tokens.area.monitor',
	network: 'tokens.area.network',
	notifications: 'tokens.area.notifications',
	offsite: 'tokens.area.offsite',
	power: 'tokens.area.power',
	scheduler: 'tokens.area.scheduler',
	security: 'tokens.area.security',
	shares: 'tokens.area.shares',
	snapshots: 'tokens.area.snapshots',
	storage: 'tokens.area.storage',
	system: 'tokens.area.system',
	updates: 'tokens.area.updates'
};

// i18n-ignore: a path of the API, the same in every language
export const pathOf = (area: TokenArea) => `/api/v1/${area}`;

/** The names of `areas`, in the language of `t`. */
export function areaNames(areas: readonly TokenArea[], t: Translate): string {
	return areas.map((area) => t(AREA_LABELS[area])).join(', ');
}

/** A token's access: full, reading only, or change access to some areas. */
export function accessOf(token: { read_only: boolean; areas: TokenArea[] | null }, t: Translate): string {
	if (!token.read_only && token.areas === null) return t('tokens.access.full');
	return token.read_only ? t('tokens.access.read') : t('tokens.access.change');
}

export function expired(token: ApiToken): boolean {
	const expires = apiTime(token.expires_at);
	return expires !== null && expires.getTime() < Date.now();
}
