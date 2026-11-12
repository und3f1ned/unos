import { writable } from 'svelte/store';
import type { RepairResult } from '$lib/api/shares';

/**
 * The repair of a share's permissions that runs, with its counts so far, or else the last one that
 * ended, as /ws/v1/events sends it to administrators (supervisor/src/websocket/events.py, ADR-0363)
 * and as a start answers it; null before any since the page loaded, and again after sign-out.
 */
export const repairJob = writable<RepairResult | null>(null);

/** Whether `job` is a repair of the share `name`: share names are compared without case, as Samba does. */
export function isRepairOf(job: RepairResult | null, name: string): job is RepairResult {
  return job !== null && job.share.toLowerCase() === name.toLowerCase();
}
