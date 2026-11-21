<script lang="ts">
	// The Volumes dialog (ADR-0243): the volumes (btrfs subvolumes) of a mounted pool, the notes on
	// their quotas, and the forms that delete one and create one. Its state is the page's
	// PoolVolumes, which opens it for a pool.
	import { Layers, Plus } from 'lucide-svelte';
	import { Alert, Button, ConfirmDialog, EmptyState, ErrorState, Input, Sheet, Skeleton } from '$lib/components/ui';
	import { MAX_QUOTA_GIB } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { poolMountpoint } from './pools';
	import type { PoolVolumes } from './volumes.svelte';
	import VolumeItem from './VolumeItem.svelte';

	type Props = {
		volumes: PoolVolumes;
		formatBytes: (bytes: number) => string;
		percentFormat: Intl.NumberFormat;
	};

	let { volumes, formatBytes, percentFormat }: Props = $props();

	let newNameError = $derived(volumes.newName && !volumes.newNameValid ? $t('storage.nameRule') : '');
	let newQuotaError = $derived(
		Number.isNaN(volumes.newQuotaBytes) ? $t('storage.volumes.quotaError', { max: MAX_QUOTA_GIB }) : ''
	);

	// The commands that the notes of the dialog name, run on the host: the same in every language.
	let volumesMountpoint = $derived(
		volumes.pool ? volumes.pool.mountpoint || poolMountpoint(volumes.pool.name) : ''
	);
	// i18n-ignore: a command
	let quotaEnableCommand = $derived('btrfs quota enable ' + volumesMountpoint);
	// i18n-ignore: a command
	let quotaRescanCommand = $derived('btrfs quota rescan ' + volumesMountpoint);
	let quotasOff = $derived(volumes.list.some((volume) => volume.usage === 'quotas_off'));
	let needsRescan = $derived(volumes.list.some((volume) => volume.usage === 'inconsistent'));
</script>

<!-- A list of volumes and the form of a new one: a Sheet (ADR-0432). -->
<Sheet
	open={volumes.pool !== null}
	title={volumes.pool
		? $t('storage.volumes.title', { pool: volumes.pool.name })
		: $t('storage.volumes.titleNone')}
	size="lg"
	closable={!volumes.busy}
	onclose={() => volumes.close()}
>
	{#if volumes.pool}
		<div class="space-y-4">
			<!-- btrfs snapshots are not recursive, and scheduled snapshot tasks only take
			     pools: say so before files move into a volume (ADR-0243). -->
			<p class="text-sm text-secondary">
				{$t('storage.volumes.introBefore')}
				<span class="mono-xs">{volumes.pool.mountpoint || poolMountpoint(volumes.pool.name)}</span>
				{$t('storage.volumes.introAfter')}
			</p>

			{#if volumes.readOnly}
				<Alert variant="warning" title={$t('storage.pool.readOnlyTitle')}>
					{$t('storage.volumes.readOnly')}
					{#if volumes.pool.forced_read_only}
						{$t('storage.volumes.forcedReadOnly')}
					{/if}
				</Alert>
			{/if}

			{#if volumes.loading && !volumes.loaded}
				<div class="space-y-3" aria-busy="true">
					<Skeleton shape="block" class="h-16 w-full rounded-lg" />
					<Skeleton shape="block" class="h-16 w-full rounded-lg" />
				</div>
			{:else if volumes.error}
				<ErrorState title={$t('storage.volumes.loadError')} message={volumes.error} onretry={() => volumes.load()} />
			{:else if volumes.list.length === 0}
				<EmptyState compact icon={Layers} title={$t('storage.volumes.none')} />
			{:else}
				<!-- The use of each volume's quota, as the kernel counts it. -->
				{#if quotasOff}
					<Alert variant="info" title={$t('storage.volumes.quotasOffTitle')}>
						{$t('storage.volumes.quotasOff')}
						<span class="mono-xs break-all">{quotaEnableCommand}</span>
					</Alert>
				{/if}
				{#if needsRescan}
					<Alert variant="warning" title={$t('storage.volumes.rescanTitle')}>
						{$t('storage.volumes.rescan')}
						<span class="mono-xs break-all">{quotaRescanCommand}</span>
					</Alert>
				{/if}
				<ul class="space-y-2">
					{#each volumes.list as volume (volume.name)}
						<VolumeItem {volume} {volumes} {formatBytes} {percentFormat} />
					{/each}
				</ul>
			{/if}

			<form onsubmit={(e) => { e.preventDefault(); volumes.create(); }} class="space-y-2">
				<Input
					label={$t('storage.volumes.name')}
					bind:value={volumes.newName}
					placeholder={$t('storage.volumes.namePlaceholder')}
					autocomplete="off"
					disabled={volumes.readOnly}
					error={newNameError}
				/>
				<Input
					label={$t('storage.volumes.quota')}
					bind:value={volumes.newQuota}
					placeholder={$t('storage.volumes.quotaPlaceholder')}
					autocomplete="off"
					disabled={volumes.readOnly}
					error={newQuotaError}
					hint={$t('storage.volumes.quotaHint')}
				/>
				{#if volumes.createError}
					<Alert variant="error" title={$t('storage.volumes.createFailed')}>{volumes.createError}</Alert>
				{/if}
				<Button
					type="submit"
					icon={Plus}
					loading={volumes.creating}
					disabled={!volumes.newNameValid ||
						!!newQuotaError ||
						volumes.busy ||
						volumes.readOnly}
				>
					{$t('storage.volumes.create')}
				</Button>
			</form>
		</div>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={volumes.busy} onclick={() => volumes.close()}>
			{$t('storage.volumes.close')}
		</Button>
	{/snippet}
</Sheet>

<!-- Deleting a volume asks over the dialog, which stays open under it, with the volume's name
     typed exactly. -->
<ConfirmDialog
	open={volumes.toDelete !== null}
	title={$t('storage.volumes.deleteTitle', { name: volumes.toDelete?.name ?? '' })}
	confirmLabel={$t('storage.volumes.delete')}
	typeToConfirm={volumes.toDelete?.name ?? ''}
	typeLabel={$t('storage.volumes.confirm', { name: volumes.toDelete?.name ?? '' })}
	typeExact
	busy={volumes.deleting}
	oncancel={() => volumes.cancelDelete()}
	onconfirm={() => volumes.delete()}
>
	{#if volumes.toDelete}
		<p>
			{$t('storage.volumes.deleteBefore')}
			<span class="mono-xs break-all">{volumes.toDelete.path}</span>
			{$t('storage.volumes.deleteAfter')}
		</p>
		{#if volumes.deleteError}
			<Alert variant="error" title={$t('storage.volumes.deleteFailed')}>{volumes.deleteError}</Alert>
		{/if}
	{/if}
</ConfirmDialog>
