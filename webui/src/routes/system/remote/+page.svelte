<script lang="ts">
	import RemoteAccessCard from '$lib/components/remote/RemoteAccessCard.svelte';
	import { Skeleton } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { followPage } from '$lib/pages/follow.svelte';
	import type { RemoteSnapshot } from '$lib/pages/system/remote';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// System > Remote access (ADR-0420): the WireGuard tunnel. The apps' local names, which reach the
	// LAN only, are in System > Network (ADR-0431). The tab's load reads it (ADR-0469).
	let remote = $state.raw<RemoteSnapshot['remote'] | null>(null);

	followPage(
		() => data,
		(snapshot) => (remote = snapshot.remote)
	);
</script>

<div class="space-y-6">
	<h2 class="text-xl font-semibold">{$t('remote.tab')}</h2>
	{#if remote}
		<RemoteAccessCard read={remote} />
	{:else}
		<!-- The tab's first page while its data take longer than a moment (ADR-0469). -->
		<div aria-busy="true"><Skeleton shape="card" count={4} /></div>
	{/if}
</div>
