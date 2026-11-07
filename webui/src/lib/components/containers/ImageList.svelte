<script lang="ts">
	import { onMount } from 'svelte';
	import { Download, Layers, Trash2 } from 'lucide-svelte';
	import { Alert, Button, Card, EmptyState, Input, Modal } from '$lib/components/ui';
	import { getImages, pullImage, removeImage, type Image } from '$lib/api/containers';
	import { formatBytes } from '$lib/stores/stats';

	// The Images tab of the Containers page: the images Docker has, a pull by
	// name and the removal of an image that no container uses (ADR-0248).

	let images: Image[] = [];
	let loading = true;
	let loadError = '';

	let imageName = '';
	let pulling = '';
	let pullError = '';
	let notice = '';

	let imageToRemove: Image | null = null;
	let removing = false;
	let removeError = '';

	onMount(refresh);

	function messageOf(e: unknown): string {
		return e instanceof Error && e.message ? e.message : 'The request failed';
	}

	/** Load the list again. */
	export async function refresh() {
		loading = true;
		loadError = '';
		try {
			images = await getImages();
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
			notice = `Pulled ${name}.`;
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
			// Never forced: Docker refuses an image that a container uses, with its reason.
			await removeImage(image.id);
			notice = `Removed ${nameOf(image)}.`;
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

	function formatDate(value: string): string {
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
	}
</script>

<div class="space-y-6">
	<Card>
		<form on:submit|preventDefault={handlePull} class="flex flex-col sm:flex-row sm:items-end gap-3">
			<Input
				label="Image"
				bind:value={imageName}
				placeholder="nginx:latest"
				hint="Name and tag, as for docker pull. Without a tag, Docker pulls latest."
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
				Pull
			</Button>
		</form>
		{#if pulling}
			<p class="text-sm text-content-secondary mt-2" aria-live="polite">
				Pulling {pulling}. A large image can take several minutes.
			</p>
		{/if}
		{#if pullError}
			<Alert variant="error" title="The image was not pulled" class="mt-4">{pullError}</Alert>
		{/if}
	</Card>

	{#if notice}
		<Alert variant="success" dismissible on:dismiss={() => (notice = '')}>{notice}</Alert>
	{/if}

	{#if loading && images.length === 0}
		<p class="loading-text">Loading images...</p>
	{:else if loadError}
		<Alert variant="error" title="The images could not be read">{loadError}</Alert>
	{:else if images.length === 0}
		<EmptyState
			icon={Layers}
			title="No images"
			description="Pull an image to create containers from it."
		/>
	{:else}
		<Card>
			<div class="overflow-x-auto">
				<table class="table" aria-label="Images">
					<thead class="table-header">
						<tr>
							<th>Image</th>
							<th>ID</th>
							<th>Size</th>
							<th>Created</th>
							<th><span class="sr-only">Actions</span></th>
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
										<p class="text-content-muted">No tag</p>
									{/if}
								</td>
								<td class="mono-xs text-content-secondary">{image.id}</td>
								<td class="whitespace-nowrap">{formatBytes(image.size)}</td>
								<td class="whitespace-nowrap">{formatDate(image.created)}</td>
								<td>
									<div class="flex justify-end">
										<Button variant="ghost" size="icon-sm" on:click={() => askRemove(image)}>
											<Trash2 class="w-4 h-4 text-error" />
											<span class="sr-only">Remove image {nameOf(image)}</span>
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
	title="Remove Image"
	danger
	closable={!removing}
	on:close={closeRemove}
>
	{#if imageToRemove}
		<div class="space-y-4">
			<p class="text-sm">
				Remove the image <span class="font-semibold break-all">{nameOf(imageToRemove)}</span>
				(<span class="mono-xs break-all">{imageToRemove.id}</span>)? Docker removes it only if no
				container uses it. Pull it again to use it later.
			</p>
			{#if removeError}
				<Alert variant="error" title="The image was not removed">{removeError}</Alert>
			{/if}
		</div>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={removing} on:click={closeRemove}>
			Cancel
		</Button>
		<Button variant="danger" class="flex-1" icon={Trash2} loading={removing} on:click={handleRemove}>
			Remove Image
		</Button>
	</svelte:fragment>
</Modal>
