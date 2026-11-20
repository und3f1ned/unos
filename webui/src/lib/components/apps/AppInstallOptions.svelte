<script lang="ts">
	// The install form's options above an app's tabs: the pool for its data (or why none can be
	// chosen), and the group unos-smb, the only way to give it a share's folder (ADR-0274).
	import { HardDrive } from 'lucide-svelte';
	import { Alert, Button, Select, Skeleton } from '$lib/components/ui';
	import { UNOS_SMB_IDS, type AppTemplate } from '$lib/api/apps';
	import { intlLocale, t } from '$lib/i18n';
	import type { PoolChoice } from './pools.svelte';
	import { formatSize } from './size';

	type Props = {
		app: AppTemplate;
		isInstalled: boolean;
		/** Some of the app's data goes on a pool. */
		needsPool: boolean;
		/** The pools that the dialog read, and the one chosen. */
		pools: PoolChoice;
		/** Run the app in the group unos-smb. */
		runInUnosSmb: boolean;
	};

	let { app, isInstalled, needsPool, pools, runInUnosSmb = $bindable() }: Props = $props();

	const poolOptions = $derived(
		pools.pools.map((pool) => ({
			value: pool.name,
			label: $t('apps.detail.poolOption', {
				name: pool.name,
				free: formatSize(pool.free_bytes, $intlLocale, $t, 2),
				total: formatSize(pool.total_bytes, $intlLocale, $t, 2)
			})
		}))
	);
</script>

<!-- Storage pool for the app's data -->
{#if needsPool && !isInstalled}
	<div class="mb-4">
		{#if pools.loading}
			<div aria-busy="true"><Skeleton shape="block" class="h-9 w-full" /></div>
		{:else if pools.error}
			<Alert variant="error" title={$t('apps.detail.poolsError')}>{pools.error}</Alert>
		{:else if pools.pools.length === 0}
			<Alert variant="warning" title={$t('apps.detail.noPoolTitle')}>
				<p>{$t('apps.detail.noPool', { name: app.name })}</p>
				<Button variant="secondary" size="sm" href="/storage" icon={HardDrive} class="mt-3">
					{$t('apps.detail.goToStorage')}
				</Button>
			</Alert>
		{:else}
			<Select
				label={$t('apps.detail.pool')}
				options={poolOptions}
				bind:value={pools.selectedName}
				hint={$t('apps.detail.poolHint')}
			/>
		{/if}
	</div>
{/if}
{#if app.can_run_in_unos_smb && !isInstalled}
	<div class="mb-4">
		<label class="flex items-center gap-2 cursor-pointer">
			<input
				type="checkbox"
				class="form-checkbox"
				bind:checked={runInUnosSmb}
				aria-describedby="app-unos-smb-hint"
			/>
			{$t('apps.detail.unosSmb')}
		</label>
		<p id="app-unos-smb-hint" class="form-hint">
			{$t('apps.detail.unosSmbHint', { puid: UNOS_SMB_IDS.PUID, pgid: UNOS_SMB_IDS.PGID })}
		</p>
		{#if runInUnosSmb}
			<Alert variant="warning" class="mt-2">
				{$t('apps.detail.unosSmbWarning', { name: app.name })}
			</Alert>
		{/if}
	</div>
{/if}
