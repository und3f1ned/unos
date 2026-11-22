import type { PowerFinding } from '$lib/api/power';

// What System > Power shows of the plan the server made of the power settings (ADR-0407): the keys
// of the catalog for each finding and state, the findings grouped, and the NAS's time zone.

// The text of each finding: keys of the catalog.
export const FOUND: Record<PowerFinding['kind'], string> = {
	web_session: 'power.found.web_session',
	login_session: 'power.found.login_session',
	smb_client: 'power.found.smb_client',
	nfs_client: 'power.found.nfs_client',
	disk_activity: 'power.found.disk_activity',
	task: 'power.found.task',
	task_due: 'power.found.task_due',
	backup: 'power.found.backup',
	restore: 'power.found.restore',
	scrub: 'power.found.scrub',
	balance: 'power.found.balance',
	disk_replace: 'power.found.disk_replace',
	app_install: 'power.found.app_install',
	app_update: 'power.found.app_update',
	app_rollback: 'power.found.app_rollback',
	offsite: 'power.found.offsite',
	share_repair: 'power.found.share_repair',
	update: 'power.found.update',
	wake_alarm: 'power.found.wake_alarm',
	unreadable: 'power.found.unreadable'
};
export const FOUND_WITHOUT_SUBJECT: Partial<Record<PowerFinding['kind'], string>> = {
	scrub: 'power.found.scrubAny',
	balance: 'power.found.balanceAny',
	disk_replace: 'power.found.disk_replaceAny'
};
// Findings whose text counts them itself ("2 login sessions of root"); the others that come more
// than once get "(2 times)".
export const COUNTED: ReadonlySet<PowerFinding['kind']> = new Set(['login_session']);

export const STATES: Record<string, string> = {
	planned: 'power.schedule.state.planned',
	announced: 'power.schedule.state.announced',
	postponed: 'power.schedule.state.postponed',
	issued: 'power.schedule.state.issued'
};

/** Each distinct finding once, with how many times the server listed it, in the server's order. */
export function grouped(findings: PowerFinding[]): { finding: PowerFinding; count: number }[] {
	const seen = new Map<string, { finding: PowerFinding; count: number }>();
	for (const finding of findings) {
		const key = JSON.stringify([finding.kind, finding.subject, finding.detail, finding.amount]);
		const entry = seen.get(key);
		if (entry) entry.count += 1;
		else seen.set(key, { finding, count: 1 });
	}
	return [...seen.values()];
}
