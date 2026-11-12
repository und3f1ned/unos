import { writable } from 'svelte/store';
import type { ScrubStatus } from '$lib/api/storage';

/**
 * A pool's scrub as /ws/v1/events sends administrators (supervisor/src/storage/scrubs.py,
 * ADR-0365): its status as GET /storage/pools/{name}/scrub answers it, or why it could not be read.
 */
export type ScrubUpdate = ScrubStatus | { pool: string; error: string };

/**
 * The scrubs of the server's latest scrubs message: each pool whose scrub was seen running, with its
 * last status. The Storage page follows a running scrub with them. Regular users never get one;
 * empty again after sign-out.
 */
export const scrubs = writable<ScrubUpdate[]>([]);
