import { writable } from 'svelte/store';
import type { BalanceStatus } from '$lib/api/storage';

/**
 * A pool's balance as /ws/v1/events sends administrators (supervisor/src/storage/balances.py,
 * ADR-0377): its status as GET /storage/pools/{name}/balance answers it, or why it could not be read.
 */
export type BalanceUpdate = BalanceStatus | { pool: string; error: string };

/**
 * The balances of the server's latest balances message: each pool whose balance was seen running,
 * with its last status. The Storage page follows a running balance with them. Regular users never
 * get one; empty again after sign-out.
 */
export const balances = writable<BalanceUpdate[]>([]);
