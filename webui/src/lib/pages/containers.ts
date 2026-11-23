// The Containers page's reads (routes/containers/+page.ts): every container, from Docker. The
// Images tab reads its list when it is first shown, as before.
import { ApiError } from '$lib/api/client';
import { getContainers, type Container } from '$lib/api/containers';
import { messageOf } from '$lib/errors';
import type { PageReader } from './load';

/** A read of Docker's: its value, or why it failed and whether Docker did not answer (503, ADR-0323). */
export type DockerRead<T> = { ok: true; value: T } | { ok: false; error: string; dockerDown: boolean };

/** The answer of `request`, or why it failed: it never throws. */
export async function settleDocker<T>(request: Promise<T>): Promise<DockerRead<T>> {
	try {
		return { ok: true, value: await request };
	} catch (e) {
		return { ok: false, error: messageOf(e), dockerDown: e instanceof ApiError && e.status === 503 };
	}
}

export type ContainersSnapshot = { containers: DockerRead<Container[]> };

export const containersReader: PageReader<ContainersSnapshot> = {
	key: 'containers',
	// The apps are containers: an install, an uninstall, a start or a stop of one changes the list.
	areas: ['containers', 'apps'],
	adminOnly: true,
	read: async () => ({ containers: await settleDocker(getContainers()) })
};
