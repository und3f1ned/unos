// The addresses of a share, as the connection hints of the Shares and Account pages write them.
// The texts of the share pages are in src/lib/i18n/catalog/shares.ts (ADR-0287).

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
