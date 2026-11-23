import type { StandbyConditionId, StandbyStatus, StandbySync } from '$lib/api/standby';
import type { Translate } from '$lib/i18n';
import { formatSize } from '$lib/format';

// The words of System > Standby (ADR-0471) that come from the server's values: each through a map
// from the value to a catalog key.

export const CONDITIONS: Record<StandbyConditionId, string> = {
	profile: 'standby.condition.profile',
	loader: 'standby.condition.loader',
	emmc_root: 'standby.condition.emmc_root',
	sd_card: 'standby.condition.sd_card',
	docker_root: 'standby.condition.docker_root',
	host_tools: 'standby.condition.host_tools'
};

const REASONS: Record<string, string> = {
	marked: 'standby.reason.marked',
	tries: 'standby.reason.tries',
	emmc_unreadable: 'standby.reason.emmc_unreadable',
	emmc_boot_failed: 'standby.reason.emmc_boot_failed'
};

const PARTS: Record<string, string> = {
	system: 'standby.part.system',
	volumes: 'standby.part.volumes',
	database: 'standby.part.database',
	images: 'standby.part.images',
	boot: 'standby.part.boot',
	fixups: 'standby.part.fixups'
};

export type StandbyMark = { key: string; variant: 'success' | 'warning' | 'error' | 'neutral' };

/** The standby's state in one badge: the worst of what holds it back, else ready. */
export function standbyState(status: StandbyStatus): StandbyMark {
	if (status.root === 'card') return { key: 'standby.state.onCard', variant: 'error' };
	if (status.card_present === false) return { key: 'standby.state.cardMissing', variant: 'error' };
	if (status.in_boot_path === false) return { key: 'standby.state.notInBootPath', variant: 'error' };
	if (status.standby_ran) return { key: 'standby.state.ran', variant: 'warning' };
	if (status.last_sync?.result === 'failed') return { key: 'standby.state.syncFailed', variant: 'warning' };
	if (status.generation === null) return { key: 'standby.state.noCopy', variant: 'neutral' };
	return { key: 'standby.state.ready', variant: 'success' };
}

export function reasonText(t: Translate, reason: string | null): string {
	return t(
		reason !== null && Object.prototype.hasOwnProperty.call(REASONS, reason)
			? REASONS[reason]
			: 'standby.reason.unknown'
	);
}

export function partsText(t: Translate, parts: string[]): string {
	return parts.map((part) => (Object.prototype.hasOwnProperty.call(PARTS, part) ? t(PARTS[part]) : part)).join(', ');
}

/** What a sync did, in a few words. */
export function syncText(t: Translate, locale: string, sync: StandbySync): string {
	if (sync.result === 'failed') return t('standby.sync.failed');
	if (sync.result === 'unchanged') return t('standby.sync.unchanged');
	const parts = partsText(t, sync.parts);
	return sync.mib === null
		? t('standby.sync.wroteUnknown', { parts })
		: t('standby.sync.wrote', { size: formatSize(sync.mib * 1024 * 1024, locale, t), parts });
}
