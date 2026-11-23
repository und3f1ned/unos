// The Account page's reads (routes/account/+page.ts), for every tab at once, since a tab is the
// URL's hash, which runs no load: the two-factor status, and while it is on the recovery codes'
// count and the security keys (ADR-0238, ADR-0409); the API tokens (ADR-0239); and a regular
// account's SMB access (an administrator never has SMB, ADR-0117). The theme is the store's, which
// the root layout reads once per session (ADR-0405), and the sign-in history is read when it is
// shown. Every read is the signed-in account's own: the cache keeps it for that session and
// account only, and forgets it when another account or role signs in (ADR-0469).
import {
	getMySmb,
	getRecoveryCodesStatus,
	getSecurityKeys,
	getTOTPStatus,
	listApiTokens,
	type ApiToken,
	type MySmbStatus,
	type RecoveryCodesStatus,
	type SecurityKey,
	type TOTPStatus,
	type User
} from '$lib/api/auth';
import type { PageReader } from './load';
import { settle, type Settled } from './read';

export type AccountSnapshot = {
	totp: Settled<TOTPStatus>;
	/** Read while two-factor authentication is on; null while it is off or its status unknown. */
	recoveryCodes: Settled<RecoveryCodesStatus> | null;
	securityKeys: Settled<SecurityKey[]> | null;
	tokens: Settled<ApiToken[]>;
	/** A regular account's SMB access; null for an administrator. */
	smb: Settled<MySmbStatus> | null;
};

async function readAccount(account: User): Promise<AccountSnapshot> {
	const twoFactor = settle(getTOTPStatus()).then(async (totp) => {
		if (!totp.ok || !totp.value.enabled) return { totp, recoveryCodes: null, securityKeys: null };
		const [recoveryCodes, securityKeys] = await Promise.all([
			settle(getRecoveryCodesStatus()),
			settle(getSecurityKeys())
		]);
		return { totp, recoveryCodes, securityKeys };
	});
	const [parts, tokens, smb] = await Promise.all([
		twoFactor,
		settle(listApiTokens()),
		account.is_admin ? null : settle(getMySmb())
	]);
	return { ...parts, tokens, smb };
}

export const accountReader: PageReader<AccountSnapshot> = {
	key: 'account',
	// The SMB access lists the shares the account may use, also through its groups.
	areas: ['auth', 'shares', 'groups'],
	adminOnly: false,
	read: readAccount
};
