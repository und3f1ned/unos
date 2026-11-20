<script lang="ts">
	import { messageOf } from '$lib/errors';
	// The update of an installed app to the catalog's newer pinned images (ADR-0408): before it
	// runs, the dialog shows what the server read for it (each container's image before and
	// after, what the pulls write to the system disk, the copy of the database on the pool) or why
	// it cannot run; Update now starts its job, whose progress the App Store's job cards show.
	import { untrack } from 'svelte';
	import { Alert, Badge, Button, Modal } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatSize as formatSizeIn } from './size';
	import {
		getUpdatePlan,
		updateApp,
		type AppInstallJob,
		type AppUpdatePlan,
		type ContainerUpdate,
		type InstalledApp
	} from '$lib/api/apps';

	type Props = {
		open?: boolean;
		/** The installed app to update, as the installed list gave it, and its name in the catalog. */
		app?: InstalledApp | null;
		name?: string;
		/** Cancel, Escape or the backdrop asked to close the dialog. */
		onclose?: () => void;
		/** Update now started the update's job. */
		onstarted?: (job: AppInstallJob) => void;
	};

	let { open = false, app = null, name = '', onclose, onstarted }: Props = $props();

	let plan = $state<AppUpdatePlan | null>(null);
	let error = $state('');
	let loading = $state(false);
	let starting = $state(false);
	// The app whose plan was asked for, while the dialog is open. Only the effect below reads it.
	let readFor = '';

	// The plan is asked for when the dialog opens or shows another app, before the dialog's markup
	// updates, as Svelte 4's statements did; only `open` and `app` make it run again.
	$effect.pre(() => {
		const isOpen = open;
		const target = app;
		const id = target?.container_id;
		untrack(() => {
			if (!isOpen) readFor = '';
			else if (target && id !== readFor) void read(target);
		});
	});

	async function read(target: InstalledApp) {
		readFor = target.container_id;
		plan = null;
		error = '';
		loading = true;
		try {
			plan = await getUpdatePlan(target.container_id);
		} catch (e) {
			error = $t('appUpdates.loadFailed', { error: messageOf(e) });
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
			onstarted?.(job);
		} catch (e) {
			error = $t('appUpdates.startFailed', { error: messageOf(e) });
		} finally {
			starting = false;
		}
	}

	// A size with one decimal in the language of the page (formatSize of ./size).
	function formatSize(bytes: number): string {
		return formatSizeIn(bytes, $intlLocale, $t, 1);
	}

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
	onclose={() => onclose?.()}
>
	<div class="space-y-4">
		{#if loading}
			<p class="text-center text-content-muted py-8">{$t('appUpdates.loading')}</p>
		{/if}
		{#if error}
			<!-- Read out as it appears: the server's reason why the update cannot run. -->
			<div role="alert"><Alert variant="error"><p class="break-words">{error}</p></Alert></div>
		{/if}
		{#if plan}
			{@const changed = plan.containers.filter((container) => container.changed).length}
			<!-- The summary first: what changes, the space it needs, the safety net; the details of
			     each container on demand (webui.ux-apps). -->
			<p class="text-base font-medium">
				{$t('appUpdates.versions', {
					from: plan.version_from ?? $t('appUpdates.versionUnknown'),
					to: plan.version_to
				})}
			</p>
			<div class="kv">
				<div>
					<span class="kv-label">{$t('appUpdates.containers')}</span>
					<span class="kv-value" data-field="containers">
						{$t('appUpdates.changedCount', { changed, n: plan.containers.length })}
					</span>
				</div>
				<div>
					<span class="kv-label">{$t('appUpdates.download')}</span>
					<span class="kv-value" data-field="download">{formatSize(plan.download_bytes)}</span>
				</div>
				<div>
					<span class="kv-label">{$t('appUpdates.systemDisk')}</span>
					<span class="kv-value" data-field="system-disk">
						{plan.system_disk_free_bytes !== null
							? $t('appUpdates.writes', {
									writes: formatSize(plan.system_disk_bytes),
									free: formatSize(plan.system_disk_free_bytes)
								})
							: $t('appUpdates.writesNoFree', { writes: formatSize(plan.system_disk_bytes) })}
					</span>
				</div>
			</div>
			{#if plan.copies.length > 0}
				<div>
					<h3 class="text-sm font-medium mb-1">{$t('appUpdates.copies')}</h3>
					{#each plan.copies as copy (copy.folder)}
						<p class="text-sm text-content-secondary break-words">
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
			<p class="text-sm">{$t('appUpdates.failureNote')}</p>
			<details class="rounded-lg border border-border">
				<summary class="cursor-pointer select-none px-4 py-2.5 text-sm font-medium text-content-secondary hover:text-content">
					{$t('appUpdates.details')}
				</summary>
				<div class="space-y-3 border-t border-border px-4 py-3">
					<p class="text-sm text-content-secondary">{$t('appUpdates.kept')}</p>
					<ul class="space-y-2">
						{#each plan.containers as container (container.name)}
							{@const look = change(container)}
							<li>
								<div class="flex flex-wrap items-center gap-2">
									<span class="text-sm mono break-all">{container.name}</span>
									<Badge variant={look.variant}>{$t(look.key)}</Badge>
								</div>
								{#if container.changed && container.image_to}
									<p class="text-xs text-content-muted mono break-all mt-1">
										{#if container.image_from}{shortName(container.image_from)} → {/if}{shortName(container.image_to)}
									</p>
								{/if}
							</li>
						{/each}
					</ul>
				</div>
			</details>
		{/if}
	</div>
	{#snippet footer()}
		<Button variant="secondary" disabled={starting} onclick={() => onclose?.()}>
			{$t('common.cancel')}
		</Button>
		<Button variant="primary" loading={starting} disabled={!plan} onclick={start}>
			{$t('appUpdates.start')}
		</Button>
	{/snippet}
</Modal>
