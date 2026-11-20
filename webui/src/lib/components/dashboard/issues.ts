// What the dashboard knows to be wrong besides the problems that the events socket sends
// (ADR-0332): the disks whose SMART trend says replace soon (ADR-0235), a failed check of them, and
// the pools whose health is not healthy where the server reports no problem for the pool. The
// dashboard lists them with the socket's in one banner and counts them all in its header, so that
// it never says "No problems" beside a pool in error (ADR-0432).
import type { DiskPrediction } from '$lib/api/health';
import type { Pool } from '$lib/api/storage';
import type { Translate } from '$lib/i18n';
import type { Problem } from '$lib/stores/problems';

/** A problem that a page adds to the socket's, in the words of the page. */
export type ExtraIssue = {
	key: string;
	severity: 'warning' | 'error';
	text: string;
	/** A second, muted line: the server's reasons. */
	detail?: string;
	/** Where it is dealt with. */
	href?: string;
};

// A pool's health that is not healthy, with its severity and text.
const POOL_HEALTH: Record<string, { severity: 'warning' | 'error'; key: string }> = {
	degraded: { severity: 'error', key: 'dashboard.issue.poolDegraded' },
	error: { severity: 'error', key: 'dashboard.issue.poolError' },
	unmounted: { severity: 'warning', key: 'dashboard.issue.poolUnmounted' },
	unknown: { severity: 'warning', key: 'dashboard.issue.poolUnknown' }
};

// The socket's problems that say what the dashboard's own issue of a pool or a disk would say
// (supervisor/src/health/router.py).
const POOL_HEALTH_KINDS = new Set(['pool_degraded', 'pool_unmounted', 'pool_read_only']);
const DISK_FAILURE_KINDS = new Set(['disk_failed', 'disk_test_failed']);

/**
 * The dashboard's own issues: pools that could not be read, each pool that is not healthy and
 * whose health no problem of the socket reports, a healthy pool mounted read-only, the disks to replace
 * soon, and a failed check of them.
 */
export function dashboardIssues(
	t: Translate,
	pools: Pool[],
	/** Why the pools could not be read; '' when they were. */
	poolsError: string,
	problems: Problem[],
	toReplace: DiskPrediction[],
	predictionsError: string
): ExtraIssue[] {
	// A pool or a disk that the socket already reports in the same sense is listed once, in the
	// server's words; a problem of another kind (a pool low on space) hides nothing.
	const named = new Set(problems.filter((p) => POOL_HEALTH_KINDS.has(p.kind)).map((p) => p.subject));
	const failing = new Set(problems.filter((p) => DISK_FAILURE_KINDS.has(p.kind)).map((p) => p.subject));
	const issues: ExtraIssue[] = [];
	if (poolsError) {
		issues.push({
			key: 'pools',
			severity: 'warning',
			text: t('dashboard.issue.poolsFailed'),
			detail: poolsError,
			href: '/storage'
		});
	}
	for (const pool of pools) {
		if (named.has(pool.name)) continue;
		const health = POOL_HEALTH[pool.health];
		if (health) {
			issues.push({
				key: `pool:${pool.name}`,
				severity: health.severity,
				text: t(health.key, { name: pool.name }),
				href: '/storage'
			});
		} else if (pool.read_only) {
			issues.push({
				key: `pool:${pool.name}`,
				severity: 'warning',
				text: t('dashboard.issue.poolReadOnly', { name: pool.name }),
				href: '/storage'
			});
		}
	}
	for (const prediction of toReplace) {
		if (failing.has(prediction.device)) continue;
		issues.push({
			key: `replace:${prediction.serial}`,
			severity: 'error',
			text: t('dashboard.issue.replaceSoon', { device: prediction.device, serial: prediction.serial }),
			detail: prediction.risk_factors.join('; '),
			href: '/system#health'
		});
	}
	if (predictionsError) {
		issues.push({
			key: 'predictions',
			severity: 'warning',
			text: t('dashboard.issue.predictionsFailed'),
			detail: predictionsError,
			href: '/system#health'
		});
	}
	return issues;
}
