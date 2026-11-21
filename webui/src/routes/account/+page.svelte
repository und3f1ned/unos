<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { FolderOpen, Palette, ShieldCheck } from 'lucide-svelte';
	import { LanguageCard, SmbAccessCard, ThemeCard } from '$lib/components/account';
	import PageTabs from '$lib/components/layout/PageTabs.svelte';
	import { tabFromHash } from '$lib/components/layout/tabs';
	import { isAdmin } from '$lib/stores/auth';
	import { t } from '$lib/i18n';
	import SignInTab from './tabs/SignInTab.svelte';

	// The Account page (ADR-0431): every signed-in account's own settings, in three tabs with a hash
	// of their own, the page's look of System. File sharing is only for an account that can have
	// SMB: an administrator never does (ADR-0117). The command palette lists the tabs in this order
	// (palette/choices.ts).
	let tabs = $derived([
		{ id: 'security', icon: ShieldCheck, label: $t('account.tabs.security') },
		...($isAdmin ? [] : [{ id: 'sharing', icon: FolderOpen, label: $t('account.tabs.sharing') }]),
		{ id: 'appearance', icon: Palette, label: $t('account.tabs.appearance') }
	]);

	let activeTab = $state('security');

	// Follow the URL's hash (links, reloads, bookmarks, Back and Forward), as System does: before the
	// page is drawn, and depending on the URL and the tabs only, so that a click on a tab is not
	// undone by the hash it replaces.
	$effect.pre(() => {
		const hash = page.url.hash;
		const available = tabs;
		untrack(() => (activeTab = tabFromHash(hash, available, activeTab)));
	});

	function setTab(id: string) {
		activeTab = id;
		goto(`#${id}`, { replaceState: true, noScroll: true, keepFocus: true });
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
				<SignInTab />
			{:else if activeTab === 'sharing'}
				<div class="space-y-4">
					<h2 class="text-xl font-semibold">{$t('account.tabs.sharing')}</h2>
					<SmbAccessCard />
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
