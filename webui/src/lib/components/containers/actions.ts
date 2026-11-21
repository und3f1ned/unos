import { removeContainer, restartContainer, startContainer, stopContainer } from '$lib/api/containers';
import type { QuickAction } from './labels';

/** A container's own action, on its row or card and in its drawer: a removal is forced. */
export async function containerAction(id: string, action: QuickAction): Promise<void> {
	switch (action) {
		case 'start':
			await startContainer(id);
			break;
		case 'stop':
			await stopContainer(id);
			break;
		case 'restart':
			await restartContainer(id);
			break;
		case 'remove':
			await removeContainer(id, true);
			break;
	}
}
