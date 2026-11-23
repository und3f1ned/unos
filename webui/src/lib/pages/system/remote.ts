// System > Remote Access's read (routes/system/remote/+page.ts, ADR-0420): the WireGuard tunnel and
// its peers. A new peer's configuration comes only in the answer of its creation, never here.
import { getRemoteAccess, type RemoteAccessAnswer } from '$lib/api/remoteAccess';
import type { PageReader } from '$lib/pages/load';
import { settle, type Settled } from '$lib/pages/read';

export type RemoteSnapshot = { remote: Settled<RemoteAccessAnswer> };

export const remoteReader: PageReader<RemoteSnapshot> = {
	key: 'system:remote',
	// Whether the firewall lets the tunnel's port in is ufw's (/security/firewall).
	areas: ['network', 'security'],
	adminOnly: true,
	read: async () => ({ remote: await settle(getRemoteAccess()) })
};
