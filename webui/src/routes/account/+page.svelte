<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { FolderOpen, Palette, ShieldCheck } from 'lucide-svelte';
	import { getMySmb, type ApiToken, type MySmbStatus } from '$lib/api/auth';
	import { LanguageCard, SmbAccessCard, ThemeCard, TwoFactor } from '$lib/components/account';
	import PageTabs from '$lib/components/layout/PageTabs.svelte';
	import { tabFromHash } from '$lib/components/layout/tabs';
	import { isAdmin } from '$lib/stores/auth';
	import { t } from '$lib/i18n';
	import { accountReader, type AccountSnapshot } from '$lib/pages/account';
	import { changeMark, upToDate } from '$lib/pages/cache';
	import { followPage } from '$lib/pages/follow.svelte';
	import { settle, type Settled } from '$lib/pages/read';
	import SignInTab from './tabs/SignInTab.svelte';
	import type { PageProps } from './$types';

	// The Account page (ADR-0431): every signed-in account's own settings, in three tabs with a hash
	// of their own, the page's look of System. File sharing is only for an account that can have
	// SMB: an administrator never does (ADR-0117). The command palette lists the tabs in this order
	// (palette/choices.ts).
	let { data }: PageProps = $props();

	let tabs = $derived([
		{ id: 'security', icon: ShieldCheck, label: $t('account.tabs.security') },
		...($isAdmin ? [] : [{ id: 'sharing', icon: FolderOpen, label: $t('account.tabs.sharing') }]),
		{ id: 'appearance', icon: Palette, label: $t('account.tabs.appearance') }
	]);

	let activeTab = $state('security');

	// What the tabs show, which the page's load reads for all of them ($lib/pages/account) and the
	// cards' own reads and changes give back: a tab drawn again shows it at once.
	const twoFactor = new TwoFactor();
	let tokens = $state.raw<Settled<ApiToken[]> | null>(null);
	let smb = $state.raw<Settled<MySmbStatus> | null>(null);
	// changeMark() when the SMB access shown was read: File sharing, drawn again after a change of
	// the account since (two-factor authentication, the password), reads it again.
	let smbMark = 0;
	let smbReading = false;

	followPage(
		() => data,
		(snapshot: AccountSnapshot) => {
			twoFactor.apply(snapshot.totp, snapshot.recoveryCodes, snapshot.securityKeys);
			tokens = snapshot.tokens;
			// A snapshot is shown only while no change of its areas came since its read.
			takeSmb(snapshot.smb, changeMark());
		}
	);

	function takeSmb(read: Settled<MySmbStatus> | null, mark: number) {
		smb = read;
		smbMark = mark;
	}

	/** File sharing is drawn: its access is read again when a change of the account made it old. */
	async function sharingShown() {
		if (smb === null || smbReading || upToDate(accountReader.areas, smbMark)) return;
		smbReading = true;
		const mark = changeMark();
		try {
			const answer = await settle(getMySmb());
			// What is shown stays when it cannot be read (the card's next action reads it again), or
			// when the card changed it meanwhile.
			if (answer.ok && upToDate(accountReader.areas, mark)) takeSmb(answer, mark);
		} finally {
			smbReading = false;
		}
	}

	// Follow the URL's hash (links, reloads, bookmarks, Back and Forward), as System does: before the
	// page is drawn, and depending on the URL and the tabs only, so that a click on a tab is not
	// undone by the hash it replaces.
	$effect.pre(() => {
		const hash = page.url.hash;
		const available = tabs;
		untrack(() => {
			activeTab = tabFromHash(hash, available, activeTab);
			if (activeTab === 'sharing') void sharingShown();
		});
	});

	function setTab(id: string) {
		activeTab = id;
		if (id === 'sharing') void sharingShown();
		goto(`#${id}`, { replace: true, reset: false });
	}
</script>

<div class="page-container">
	<div class="page-header">
		<h1 id="account-title" class="page-title">{$t('layout.nav.account')}</h1>
	</div>

	<div class="grid gap-6 items-start xl:grid-cols-[220px_minmax(0,1fr)]">
		<PageTabs
			labelledby="account-title"
			idPrefix="account"
			groups={[{ id: 'account', label: $t('layout.nav.account'), tabs }]}
			active={activeTab}
			onselect={setTab}
		/>

		<div class="min-w-0 content-narrow">
			{#if activeTab === 'security'}
				<SignInTab {twoFactor} {tokens} ontokens={(read) => (tokens = read)} />
			{:else if activeTab === 'sharing'}
				<div class="space-y-4">
					<h2 class="text-xl font-semibold">{$t('account.tabs.sharing')}</h2>
					<SmbAccessCard read={smb} onchange={(read) => takeSmb(read, changeMark())} />
				</div>
			{:else if activeTab === 'appearance'}
				<div class="space-y-4">
					<h2 class="text-xl font-semibold">{$t('account.tabs.appearance')}</h2>
					<ThemeCard />
					<LanguageCard />
				</div>
			{/if}
		</div>
	</div>
</div>
