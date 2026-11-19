<script lang="ts">
	import type { SharePath } from '$lib/api/shares';
	import { Alert, Input, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// Where a share or an NFS export goes: a pool or a volume of the offered ones, and a folder in
	// it. The words that differ between SMB and NFS come from the form.
	type Props = {
		/** The ids of the two fields. */
		locationId: string;
		folderId: string;
		/** Pools and volumes (GET /shares/paths). */
		paths: SharePath[];
		/** The ones offered: those of pools that UNOS manages. */
		locations: SharePath[];
		/** The path of the chosen pool or volume. */
		base: string;
		/** The folder typed below it. */
		folder: string;
		folderError: string;
		folderPlaceholder: string;
		folderHint: string;
		/** Why some pools are not offered. */
		unmanagedHint: string;
		/** The folder that the form will share, when it is to be shown, and the words before it. */
		finalPath: string;
		finalPathLabel: string;
	};

	let {
		locationId,
		folderId,
		paths,
		locations,
		base = $bindable(),
		folder = $bindable(),
		folderError,
		folderPlaceholder,
		folderHint,
		unmanagedHint,
		finalPath,
		finalPathLabel
	}: Props = $props();

	// A pool's or a volume's name, and its kind in the page's language.
	const locationOptions = $derived(
		locations.map((p) => ({
			value: p.path,
			label: $t(p.type === 'volume' ? 'shares.location.volume' : 'shares.location.pool', { name: p.name })
		}))
	);
</script>

{#if paths.length === 0}
	<Alert variant="warning">{$t('shares.location.noneMounted')}</Alert>
{:else if locations.length === 0}
	<Alert variant="warning">
		<p>{$t('shares.location.noneManaged')}</p>
		<p class="mt-1">{unmanagedHint}</p>
	</Alert>
{:else}
	<Select
		id={locationId}
		label={$t('shares.location.label')}
		bind:value={base}
		options={locationOptions}
		placeholder={$t('shares.location.choose')}
		hint={locations.length < paths.length ? unmanagedHint : ''}
	/>
{/if}
<Input
	id={folderId}
	label={$t('shares.location.folder')}
	bind:value={folder}
	placeholder={folderPlaceholder}
	autocomplete="off"
	maxlength={4096}
	error={folderError}
	hint={folderHint}
/>
{#if finalPath}
	<p class="text-sm text-secondary">
		{finalPathLabel} <span class="mono break-all">{finalPath}</span>
	</p>
{/if}
