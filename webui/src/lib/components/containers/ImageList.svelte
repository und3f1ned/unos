<script lang="ts">
	import { onMount } from 'svelte';
	import { Download, Layers, Trash2 } from 'lucide-svelte';
	import { Alert, Button, Card, EmptyState, Input, LoadState, Skeleton, confirm, toast } from '$lib/components/ui';
	import { getImages, pullImage, removeImage, type Image } from '$lib/api/containers';
	import { formatSize } from '$lib/components/apps/size';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { NO_SECONDS } from './labels';

	// The Images tab of the Containers page: the images Docker has, a pull by
	// name and the removal of an image that no container uses, with all its
	// tags (ADR-0255).

	let images = $state<Image[]>([]);
	let loading = $state(true);
	// The first load has answered: until then the list is its skeleton (ADR-0430).
	let loaded = $state(false);
	let loadError = $state('');

	let imageName = $state('');
	let pulling = $state('');
	let pullError = $state('');

	// The image whose removal runs: its button waits.
	let removing = $state('');
	// Why the last removal was refused, such as the containers that use the image: on the page,
	// above the list, until the next removal, to act on.
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
			loaded = true;
		}
	}

	async function handlePull() {
		const name = imageName.trim();
		if (!name || pulling) return;
		pulling = name;
		pullError = '';
		try {
			await pullImage(name);
			toast.success($t('containers.images.pulled', { name }));
			imageName = '';
			await refresh();
		} catch (e) {
			pullError = messageOf(e);
		} finally {
			pulling = '';
		}
	}

	/**
	 * Remove an image once confirmed, with all its tags. Never forced: the server refuses an image
	 * that a container uses and names the containers; Docker's own refusals come with Docker's
	 * reason, which stays above the list.
	 */
	async function askRemove(image: Image) {
		if (removing) return;
		const name = nameOf(image);
		const others = image.tags.slice(1);
		const yes = await confirm({
			title: $t('containers.images.removeTitleOf', { name }),
			message: $t('containers.images.removeNote'),
			consequences: others.length > 0 ? [$t('containers.images.otherTags', { tags: others.join(', ') })] : [],
			confirmLabel: $t('containers.images.remove')
		});
		if (!yes) return;
		removing = image.id;
		removeError = '';
		try {
			await removeImage(image.id);
			toast.success($t('containers.images.removed', { name }));
			await refresh();
		} catch (e) {
			removeError = messageOf(e);
		} finally {
			removing = '';
		}
	}

	function nameOf(image: Image): string {
		return image.tags[0] ?? image.id;
	}

	// It reads $intlLocale, so that the list follows a change of the language.
	function formatDate(value: string): string {
		return formatDateTime(value, $intlLocale, NO_SECONDS);
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
			<Button type="submit" icon={Download} loading={pulling !== ''} disabled={!imageName.trim()} class="sm:mb-6">
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

	{#if removeError}
		<div role="alert">
			<Alert
				variant="error"
				title={$t('containers.images.notRemoved')}
				dismissible
				ondismiss={() => (removeError = '')}
			>
				<p class="break-words">{removeError}</p>
			</Alert>
		</div>
	{/if}

	<LoadState
		loading={!loaded}
		error={loadError}
		errorTitle={$t('containers.images.loadError')}
		empty={images.length === 0}
		onretry={refresh}
		retrying={loading}
	>
		{#snippet skeleton()}
			<!-- The first load (ADR-0430): the table's rows; a refresh keeps the list. -->
			<div class="card"><Skeleton shape="row" columns={5} count={4} /></div>
		{/snippet}
		{#snippet emptyState()}
			<EmptyState icon={Layers} title={$t('containers.images.empty')} description={$t('containers.images.emptyHint')} />
		{/snippet}
		<Card flush>
			<div class="table-frame">
				<table class="table" aria-label={$t('containers.images.list')}>
					<thead class="table-header">
						<tr>
							<th>{$t('containers.images.column.image')}</th>
							<th>{$t('containers.images.column.id')}</th>
							<th class="text-right">{$t('containers.images.column.size')}</th>
							<th>{$t('containers.images.column.created')}</th>
							<th class="w-px"><span class="sr-only">{$t('containers.images.column.actions')}</span></th>
						</tr>
					</thead>
					<tbody>
						{#each images as image (image.id)}
							<tr class="table-row align-top">
								<!-- A tag breaks anywhere; at its least 12rem wide, so that a phone's frame scrolls
								     instead of breaking it after every few letters. -->
								<td class="min-w-[12rem]">
									{#if image.tags.length > 0}
										{#each image.tags as tag}
											<p class="mono text-sm break-all">{tag}</p>
										{/each}
									{:else}
										<p class="text-content-muted">{$t('containers.images.noTag')}</p>
									{/if}
								</td>
								<td class="mono-xs text-content-secondary">{image.id}</td>
								<td class="num">{formatSize(image.size, $intlLocale, $t, 2)}</td>
								<td class="whitespace-nowrap">{formatDate(image.created)}</td>
								<td>
									<div class="flex justify-end">
										<Button
											variant="ghost-danger"
											size="icon-sm"
											loading={removing === image.id}
											disabled={removing !== ''}
											onclick={() => askRemove(image)}
											title={$t('containers.images.removeOne', { name: nameOf(image) })}
										>
											<Trash2 class="w-4 h-4" aria-hidden="true" />
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
	</LoadState>
</div>
