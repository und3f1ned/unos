<script lang="ts">
	import { onMount } from 'svelte';
	import { Download, Layers, Trash2 } from 'lucide-svelte';
	import { Alert, Button, Card, EmptyState, Input, Modal, Skeleton } from '$lib/components/ui';
	import { getImages, pullImage, removeImage, type Image } from '$lib/api/containers';
	import { formatBytes } from '$lib/stores/stats';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';

	// The Images tab of the Containers page: the images Docker has, a pull by
	// name and the removal of an image that no container uses, with all its
	// tags (ADR-0255).

	let images = $state<Image[]>([]);
	let loading = $state(true);
	let loadError = $state('');

	let imageName = $state('');
	let pulling = $state('');
	let pullError = $state('');
	let notice = $state('');

	let imageToRemove = $state<Image | null>(null);
	let removing = $state(false);
	let removeError = $state('');

	onMount(refresh);

	function messageOf(e: unknown): string {
		return e instanceof Error && e.message ? e.message : $t('containers.images.requestFailed');
	}

	/** Load the list again. */
	export async function refresh() {
		loading = true;
		try {
			images = await getImages();
			// The error stays until a load succeeds: a reload does not blank it (ADR-0430).
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	async function handlePull() {
		const name = imageName.trim();
		if (!name || pulling) return;
		pulling = name;
		pullError = '';
		notice = '';
		try {
			await pullImage(name);
			notice = $t('containers.images.pulled', { name });
			imageName = '';
			await refresh();
		} catch (e) {
			pullError = messageOf(e);
		} finally {
			pulling = '';
		}
	}

	function askRemove(image: Image) {
		imageToRemove = image;
		removeError = '';
	}

	function closeRemove() {
		if (removing) return;
		imageToRemove = null;
		removeError = '';
	}

	async function handleRemove() {
		if (!imageToRemove || removing) return;
		const image = imageToRemove;
		removing = true;
		removeError = '';
		try {
			// Never forced: the server refuses an image that a container uses and names the
			// containers; Docker's own refusals come with Docker's reason.
			await removeImage(image.id);
			notice = $t('containers.images.removed', { name: nameOf(image) });
			imageToRemove = null;
			await refresh();
		} catch (e) {
			removeError = messageOf(e);
		} finally {
			removing = false;
		}
	}

	function nameOf(image: Image): string {
		return image.tags[0] ?? image.id;
	}

	// It reads $intlLocale, so that the list follows a change of the language.
	function formatDate(value: string): string {
		return formatDateTime(value, $intlLocale);
	}
</script>

<div class="space-y-6">
	<Card>
		<form
			onsubmit={(e) => {
				e.preventDefault();
				void handlePull();
			}}
			class="flex flex-col sm:flex-row sm:items-end gap-3"
		>
			<Input
				label={$t('containers.images.image')}
				bind:value={imageName}
				placeholder={$t('containers.images.imagePlaceholder')}
				hint={$t('containers.images.imageHint')}
				autocomplete="off"
				spellcheck={false}
				disabled={pulling !== ''}
				class="flex-1"
			/>
			<Button
				type="submit"
				icon={Download}
				loading={pulling !== ''}
				disabled={!imageName.trim()}
				class="sm:mb-6"
			>
				{$t('containers.images.pull')}
			</Button>
		</form>
		{#if pulling}
			<p class="text-sm text-content-secondary mt-2" aria-live="polite">
				{$t('containers.images.pulling', { name: pulling })}
			</p>
		{/if}
		{#if pullError}
			<Alert variant="error" title={$t('containers.images.notPulled')} class="mt-4">{pullError}</Alert>
		{/if}
	</Card>

	{#if notice}
		<Alert variant="success" dismissible ondismiss={() => (notice = '')}>{notice}</Alert>
	{/if}

	{#if loading && images.length === 0 && !loadError}
		<!-- The first load (ADR-0430): the table's rows; a refresh keeps the list. -->
		<div class="card" aria-busy="true">
			<Skeleton shape="row" columns={5} count={4} />
		</div>
	{:else if loadError}
		<Alert variant="error" title={$t('containers.images.loadError')}>{loadError}</Alert>
	{:else if images.length === 0}
		<EmptyState
			icon={Layers}
			title={$t('containers.images.empty')}
			description={$t('containers.images.emptyHint')}
		/>
	{:else}
		<Card>
			<div class="table-frame">
				<table class="table" aria-label={$t('containers.images.list')}>
					<thead class="table-header">
						<tr>
							<th>{$t('containers.images.column.image')}</th>
							<th>{$t('containers.images.column.id')}</th>
							<th>{$t('containers.images.column.size')}</th>
							<th>{$t('containers.images.column.created')}</th>
							<th><span class="sr-only">{$t('containers.images.column.actions')}</span></th>
						</tr>
					</thead>
					<tbody>
						{#each images as image (image.id)}
							<tr class="table-row align-top">
								<td>
									{#if image.tags.length > 0}
										{#each image.tags as tag}
											<p class="mono text-sm break-all">{tag}</p>
										{/each}
									{:else}
										<p class="text-content-muted">{$t('containers.images.noTag')}</p>
									{/if}
								</td>
								<td class="mono-xs text-content-secondary">{image.id}</td>
								<td class="whitespace-nowrap">{formatBytes(image.size)}</td>
								<td class="whitespace-nowrap">{formatDate(image.created)}</td>
								<td>
									<div class="flex justify-end">
										<Button variant="ghost-danger" size="icon-sm" onclick={() => askRemove(image)}>
											<Trash2 class="w-4 h-4" />
											<span class="sr-only">
												{$t('containers.images.removeOne', { name: nameOf(image) })}
											</span>
										</Button>
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</Card>
	{/if}
</div>

<Modal
	open={imageToRemove !== null}
	title={$t('containers.images.removeTitle')}
	danger
	closable={!removing}
	onclose={closeRemove}
>
	{#if imageToRemove}
		<div class="space-y-4">
			<p class="text-sm">
				{$t('containers.images.removeQuestion')}
				<span class="font-semibold break-all">{nameOf(imageToRemove)}</span>
				(<span class="mono-xs break-all">{imageToRemove.id}</span>)?
				{#if imageToRemove.tags.length > 1}
					{$t('containers.images.otherTags')}
					<span class="mono-xs break-all">{imageToRemove.tags.slice(1).join(', ')}</span>.
				{/if}
				{$t('containers.images.removeNote')}
			</p>
			{#if removeError}
				<Alert variant="error" title={$t('containers.images.notRemoved')}>{removeError}</Alert>
			{/if}
		</div>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={removing} onclick={closeRemove}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" icon={Trash2} loading={removing} onclick={handleRemove}>
			{$t('containers.images.remove')}
		</Button>
	{/snippet}
</Modal>
