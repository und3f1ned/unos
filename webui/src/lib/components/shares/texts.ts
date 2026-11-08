// Fixed texts of the SMB pages, as the server and the user guide word them.

export const GUEST_WARNING =
	'Anyone on your network can read this share without a password, and its name is visible to anyone on your network. Guests can never write. While a guest share exists, connections to this server are not required to be signed. Windows 11 refuses guest access by default, and guest connections are neither signed nor encrypted.';

export const ACCESS_CHANGE_NOTE =
	'Removing or lowering access, or turning guest access off, disconnects open connections to that share (clients reconnect); adding access disconnects nobody.';

export const NETWORK_NOTE =
	'SMB answers only addresses of your local network (private IPv4, IPv6 ULA and link-local, VPN ranges such as 100.64.0.0/10); public addresses are refused.';

export const APPS_NOTE =
	'Folders that apps use (downloads, media) can be shared. SMB users with write access then change what the app sees.';

export const ADMINS_NOT_LISTED = 'Administrators are not listed: they cannot use SMB.';

export const NOT_SERVED_HINT =
	'Fix the folder first: a share that is not served cannot be given access';

/** A share that is not served is saved only once it grants nothing, or with a folder that can be shared. */
export const NOT_SERVED_GRANTS_NOTE =
	'Its access entries and guest access do not count while it is not served. To save other changes without a new folder, remove them: No access for every user, and guest access off.';

/** The server's answer for a pool's top level (it is never shared). */
export const POOL_ROOT_MESSAGE =
	'Share a folder inside the pool or a volume: the top level of a pool holds app data (apps/), volumes and snapshots';

/** The server's answer for <pool>/apps and everything below it. */
export function appsFolderMessage(pool: string): string {
	return `App data (${pool}/apps) cannot be shared: apps keep their configuration there`;
}

/** Shown when the location list leaves out pools that UNOS does not manage. */
export const UNMANAGED_POOLS_NOTE =
	'Pools marked "Not mounted at boot" on the Storage page are not listed: SMB shares need a pool that UNOS manages.';

/** The server's answer for a pool's top level (it is never exported). */
export const NFS_POOL_ROOT_MESSAGE =
	'Export a folder inside the pool or a volume: the top level of a pool holds app data (apps/), volumes and snapshots';

/** The server's answer for <pool>/apps and everything below it. */
export function nfsAppsFolderMessage(pool: string): string {
	return `App data (${pool}/apps) cannot be exported: apps keep their configuration there`;
}

/** Shown when the location list of an NFS export leaves out pools that UNOS does not manage. */
export const NFS_UNMANAGED_POOLS_NOTE =
	'Pools marked "Not mounted at boot" on the Storage page are not listed: NFS exports need a pool that UNOS manages.';

/** What an NFS client may do: NFS checks no password (supervisor/src/shares/nfs.py, ADR-0293). */
export const NFS_NOTE =
	'NFS asks for no password: every computer in the client network can mount the export, and its users get the access that the user and group ids of their own accounts have in the folder (root on a client counts as nobody). UNOS exports only to addresses of your local network.';

/**
 * The host part of a UNC path. Windows takes an IPv6 address there only in
 * the ipv6-literal.net form: "[fd00::103]" (as the browser gives it) becomes
 * "fd00--103.ipv6-literal.net" (':' becomes '-', a zone's '%' becomes 's').
 */
function uncHost(host: string): string {
	if (!(host.startsWith('[') && host.endsWith(']'))) return host;
	return `${host.slice(1, -1).replace(/:/g, '-').replace(/%/g, 's')}.ipv6-literal.net`;
}

/** Windows (UNC) address of a share. */
export function windowsAddress(host: string, share: string): string {
	return `\\\\${uncHost(host)}\\${share}`;
}

/** smb:// address of a share (macOS Finder, Linux file managers); an IPv6 host keeps its brackets. */
export function smbUrl(host: string, share: string): string {
	return `smb://${host}/${share}`;
}
