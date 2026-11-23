<script lang="ts">
	import { onDestroy } from 'svelte';
	import type { ApiToken } from '$lib/api/auth';
	import {
		ApiTokensCard,
		LoginHistoryCard,
		PasswordCard,
		TwoFactorCard,
		TwoFactorDialogs
	} from '$lib/components/account';
	import type { TwoFactor } from '$lib/components/account/twoFactor.svelte';
	import { t } from '$lib/i18n';
	import type { Settled } from '$lib/pages/read';

	// The Account page's Sign-in and security (ADR-0431): the account's own password, two-factor
	// authentication with its recovery codes and security keys, API tokens and sign-in history.
	// The page holds what the tab shows (its load reads it), so that the tab is drawn again at once.

	type Props = {
		/** The two-factor status and its setup, shared by the card and its dialogs. */
		twoFactor: TwoFactor;
		/** The API tokens as last read; null until the page has read them. */
		tokens: Settled<ApiToken[]> | null;
		ontokens: (read: Settled<ApiToken[]>) => void;
	};

	let { twoFactor, tokens, ontokens }: Props = $props();

	// The two-factor setup's secret goes with the tab, as it did when the tab held its state.
	onDestroy(() => twoFactor.closeDialogs());
</script>

<div class="space-y-4">
	<h2 class="text-xl font-semibold">{$t('account.tabs.security')}</h2>

	<PasswordCard />

	<TwoFactorCard {twoFactor} />

	<ApiTokensCard read={tokens} onchange={ontokens} />

	<!-- The account's own attempts, also an administrator's: every account's are in the Audit Log. -->
	<LoginHistoryCard />
</div>

<!-- The dialogs take Modal's overlay (ADR-0302): one taller than the window scrolls with it, so
     that its title and buttons stay reachable; my-auto centers it while it fits. -->
<TwoFactorDialogs {twoFactor} />
