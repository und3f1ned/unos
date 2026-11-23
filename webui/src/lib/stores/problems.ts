import { writable } from 'svelte/store';

/**
 * A problem of the NAS that is still there, as /ws/v1/events sends administrators the list
 * (supervisor/src/health/problems.py, ADR-0332). The web UI says it through its kind and subject,
 * in the language of the page; title and detail are the server's English alert.
 */
export interface Problem {
	/** "<kind>:<subject or task id>": one problem of a kind per pool, disk or task. */
	key: string;
	severity: 'warning' | 'error';
	/**
	 * pool_unmounted, pool_degraded, pool_read_only, pool_low_space, disk_failed, disk_test_failed,
	 * task_failed, task_missed, volume_quota_80, volume_quota_90, volume_quota_95, app_memory, app_cpu,
	 * app_oom, shutdown_scheduled, shutdown_postponed, shutdown_idle, emmc_worn, system_disk_errors,
	 * system_disk_failed
	 */
	kind: string;
	/** The pool's name, the disk's path, the task's name or <pool>/<volume>. */
	subject: string;
	title: string;
	detail: string;
	/** When it was found, ISO 8601 with the UTC offset. */
	since: string;
}

/**
 * The problems of the server's latest problems message, which the health banner of the layout
 * shows. Regular users never get one; empty again after sign-out.
 */
export const problems = writable<Problem[]>([]);

/**
 * Whether the current socket has sent its problems message: until it has, an empty list says
 * nothing, and the dashboard's "No problems" waits (ADR-0428). False again when the socket closes.
 */
export const problemsKnown = writable(false);
