// The Shares page's reads (routes/shares/+page.ts): the SMB shares and then the SMB service's status
// (the list imports the old smb.conf on first use, and the status carries its report), beside the
// pools and volumes that can hold a share, the NFS exports and the NFS server's status, and the
// interfaces SMB listens on.
import {
	getNfsExports,
	getNfsStatus,
	getSharePaths,
	getSmbInterfaces,
	getSmbShares,
	getSmbStatus,
	type NfsExport,
	type NfsServiceStatus,
	type SharePath,
	type SmbInterfaces,
	type SmbServiceStatus,
	type SmbShare
} from '$lib/api/shares';
import type { PageReader } from './load';
import { settle, type Settled } from './read';

export type SharesSnapshot = {
	shares: Settled<SmbShare[]>;
	smbStatus: Settled<SmbServiceStatus>;
	paths: Settled<SharePath[]>;
	nfsExports: Settled<NfsExport[]>;
	nfsStatus: Settled<NfsServiceStatus>;
	interfaces: Settled<SmbInterfaces>;
};

async function readShares(): Promise<SharesSnapshot> {
	const smb = settle(getSmbShares()).then(async (shares) => ({ shares, smbStatus: await settle(getSmbStatus()) }));
	const [{ shares, smbStatus }, paths, nfsExports, nfsStatus, interfaces] = await Promise.all([
		smb,
		settle(getSharePaths()),
		settle(getNfsExports()),
		settle(getNfsStatus()),
		settle(getSmbInterfaces())
	]);
	return { shares, smbStatus, paths, nfsExports, nfsStatus, interfaces };
}

export const sharesReader: PageReader<SharesSnapshot> = {
	key: 'shares',
	// The paths are the pools' and their volumes'; the access lists name users and groups; a share's
	// problems name its pool's snapshots; the interfaces are the host's network's.
	areas: ['shares', 'storage', 'snapshots', 'auth', 'groups', 'network'],
	adminOnly: true,
	read: readShares
};
