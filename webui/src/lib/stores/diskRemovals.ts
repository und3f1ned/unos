import { writable } from 'svelte/store';
import type { DiskRemovalStatus } from '$lib/api/storage';

/**
 * A pool's disk removal as /ws/v1/events sends administrators (supervisor/src/storage/devices.py,
 * ADR-0411): its status as GET /storage/pools/{name}/removal answers it, or why it could not be read.
 */
export type DiskRemovalUpdate = DiskRemovalStatus | { pool: string; error: string };

/**
 * The removals of the server's latest disk_removals message: each pool whose removal was seen
 * running, with its last status. The Storage page follows a running removal with them. Regular
 * users never get one; empty again after sign-out.
 */
export const diskRemovals = writable<DiskRemovalUpdate[]>([]);
