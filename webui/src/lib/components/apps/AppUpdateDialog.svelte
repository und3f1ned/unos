<script lang="ts">
	// The update of an installed app to the catalog's newer pinned images (ADR-0408): before it
	// runs, the dialog shows what the server read for it (each container's image before and
	// after, what the pulls write to the system disk, the copy of the database on the pool) or why
	// it cannot run; Update now starts its job, whose progress the App Store's job cards show.
	import { createEventDispatcher } from 'svelte';
	import { Alert, Badge, Button, Modal } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import {
		getUpdatePlan,
		updateApp,
		type AppInstallJob,
		type AppUpdatePlan,
		type ContainerUpdate,
		type InstalledApp
	} from '$lib/api/apps';

	export let open = false;
	/** The installed app to update, as the installed list gave it, and its name in the catalog. */
	export let app: InstalledApp | null = null;
	export let name = '';

	const dispatch = createEventDispatcher<{ close: void; started: AppInstallJob }>();

	let plan: AppUpdatePlan | null = null;
	let error = '';
	let loading = false;
	let starting = false;
	// The app whose plan was asked for, while the dialog is open.
	let readFor = '';

	$: if (open && app && app.container_id !== readFor) void read(app);
	$: if (!open) readFor = '';

	async function read(target: InstalledApp) {
		readFor = target.container_id;
		plan = null;
		error = '';
		loading = true;
		try {
			plan = await getUpdatePlan(target.container_id);
		} catch (e) {
			error = $t('appUpdates.loadFailed', { error: e instanceof Error ? e.message : String(e) });
		} finally {
			loading = false;
		}
	}

	async function start() {
		if (!app) return;
		starting = true;
		error = '';
		try {
			const job = await updateApp(app.container_id);
			dispatch('started', job);
		} catch (e) {
			error = $t('appUpdates.startFailed', { error: e instanceof Error ? e.message : String(e) });
		} finally {
			starting = false;
		}
	}

	// The units of a size, by powers of 1024, as the App Store writes them.
	const SIZE_UNITS = ['apps.size.bytes', 'apps.size.kb', 'apps.size.mb', 'apps.size.gb', 'apps.size.tb'];

	$: formatSize = (bytes: number): string => {
		const power =
			bytes > 0 ? Math.max(0, Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), SIZE_UNITS.length - 1)) : 0;
		const size = new Intl.NumberFormat($intlLocale, { maximumFractionDigits: 1, useGrouping: false }).format(
			bytes / 1024 ** power
		);
		return $t(SIZE_UNITS[power], { size });
	};

	/** The image as its tag names it; one created by its digest alone with the start of the digest. */
	function shortName(image: string | null): string {
		const [name, digest] = (image ?? '').split('@');
		const tagged = name.slice(name.lastIndexOf('/') + 1).includes(':');
		return tagged || !digest ? name : `${name}@${digest.slice(0, 19)}`;
	}

	function change(container: ContainerUpdate): { key: string; variant: 'info' | 'neutral' | 'warning' } {
		if (container.image_from === null) return { key: 'appUpdates.added', variant: 'info' };
		if (container.image_to === null) return { key: 'appUpdates.leftOut', variant: 'warning' };
		return container.changed
			? { key: 'appUpdates.newImage', variant: 'info' }
			: { key: 'appUpdates.sameImage', variant: 'neutral' };
	}
</script>

<Modal
	{open}
	size="lg"
	title={$t('appUpdates.title', { name: name || app?.name || '' })}
	closable={!starting}
	on:close={() => dispatch('close')}
>
	<div class="space-y-4">
		{#if loading}
			<p class="loading-text">{$t('appUpdates.loading')}</p>
		{/if}
		{#if error}
			<!-- Read out as it appears: the server's reason why the update cannot run. -->
			<div role="alert"><Alert variant="error"><p class="break-words">{error}</p></Alert></div>
		{/if}
		{#if plan}
			<p class="text-sm">
				{$t('appUpdates.versions', {
					from: plan.version_from ?? $t('appUpdates.versionUnknown'),
					to: plan.version_to
				})}
			</p>
			<div>
				<h3 class="text-sm font-medium mb-2">{$t('appUpdates.containers')}</h3>
				<ul class="space-y-2">
					{#each plan.containers as container (container.name)}
						{@const look = change(container)}
						<li class="rounded-md bg-surface-muted px-3 py-2">
							<div class="flex flex-wrap items-center gap-2">
								<span class="text-sm mono break-all">{container.name}</span>
								<Badge variant={look.variant}>{$t(look.key)}</Badge>
							</div>
							{#if container.changed && container.image_to}
								<p class="text-xs text-muted mono break-all mt-1">
									{#if container.image_from}{shortName(container.image_from)} → {/if}{shortName(container.image_to)}
								</p>
							{/if}
						</li>
					{/each}
				</ul>
			</div>
			<div>
				<h3 class="text-sm font-medium mb-1">{$t('appUpdates.systemDisk')}</h3>
				<p class="text-sm text-secondary">
					{plan.system_disk_free_bytes !== null
						? $t('appUpdates.writes', {
								download: formatSize(plan.download_bytes),
								writes: formatSize(plan.system_disk_bytes),
								free: formatSize(plan.system_disk_free_bytes)
							})
						: $t('appUpdates.writesNoFree', {
								download: formatSize(plan.download_bytes),
								writes: formatSize(plan.system_disk_bytes)
							})}
				</p>
			</div>
			{#if plan.copies.length > 0}
				<div>
					<h3 class="text-sm font-medium mb-1">{$t('appUpdates.copies')}</h3>
					{#each plan.copies as copy (copy.folder)}
						<p class="text-sm text-secondary break-words">
							{$t('appUpdates.copy', {
								folder: copy.folder,
								size: formatSize(copy.bytes),
								copy: copy.copy_folder,
								pool: copy.pool,
								free: formatSize(copy.pool_free_bytes)
							})}
						</p>
					{/each}
				</div>
			{/if}
			<Alert variant="info">
				<p>{$t('appUpdates.kept')}</p>
				<p class="mt-1">{$t('appUpdates.failureNote')}</p>
			</Alert>
		{/if}
	</div>
	<svelte:fragment slot="footer">
		<Button variant="secondary" disabled={starting} on:click={() => dispatch('close')}>
			{$t('common.cancel')}
		</Button>
		<Button variant="primary" loading={starting} disabled={!plan} on:click={start}>
			{$t('appUpdates.start')}
		</Button>
	</svelte:fragment>
</Modal>
