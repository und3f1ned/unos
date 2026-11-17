import { writable } from 'svelte/store';
import type { AppInstallJob } from '$lib/api/apps';

/**
 * The install of an app that runs, with its progress, and the last install of each app since the
 * supervisor started, as /ws/v1/events sends them to administrators once there is one and on each
 * change (supervisor/src/websocket/events.py, ADR-0383). auth_ok empties it: a supervisor that
 * restarted forgot its installs, and a new socket sends those it has; sign-out empties it too.
 */
export const appInstalls = writable<AppInstallJob[]>([]);
