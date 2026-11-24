// The critical apps of the SD standby that a pool holds and that still run from it (ADR-0498): what a
// refused unmount or removal of the pool offers to move to the card first. None when the standby cannot
// be read or is not set up.
import { getStandby } from '$lib/api/standby';

export async function criticalAppsOf(pool: string): Promise<string[]> {
	try {
		const status = await getStandby();
		if (!status.armed || !status.card_present) return [];
		return status.apps.filter((app) => app.pool === pool && !app.moved).map((app) => app.app);
	} catch {
		return [];
	}
}
