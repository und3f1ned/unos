import { writable } from 'svelte/store';
import type { UpdateProgress } from '$lib/api/updates';

/**
 * The progress of the update or rollback of UNOS that runs or ran last, as /ws/v1/events sends it to
 * administrators once one ran since the supervisor started and at each of its steps
 * (supervisor/src/websocket/events.py, ADR-0439). auth_ok empties it: a supervisor that restarted,
 * as a switch to a release restarts it, sends only what it ran itself; sign-out empties it too.
 */
export const updateProgress = writable<UpdateProgress | null>(null);
