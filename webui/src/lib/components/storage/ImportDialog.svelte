<script lang="ts">
	// The dialog that imports a btrfs filesystem found on the disks as a pool (ADR-0283), under a
	// name that no pool and no other filesystem's label has.
	import { HardDriveDownload } from 'lucide-svelte';
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import {
		importFilesystem,
		POOL_NAME_PATTERN,
		suggestedPoolName,
		type ImportableFilesystem,
		type Pool
	} from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { messageOf, poolMountpoint } from './pools';

	type Props = {
		/** The filesystem to import; the dialog is open while it is set. */
		target: ImportableFilesystem | null;
		pools: Pool[];
		/** Every filesystem found on the disks. */
		importable: ImportableFilesystem[];
		onclose: () => void;
		/** The filesystem is a pool now. */
		onimported: () => void | Promise<void>;
	};

	let { target, pools, importable, onclose, onimported }: Props = $props();

	// The name: the one its label suggests when the dialog opens for a filesystem, then the user's.
	let importName = $derived(suggestedPoolName(target?.label ?? null));
	let importing = $state(false);
	let importError = $state('');

	let importNameValid = $derived(POOL_NAME_PATTERN.test(importName));
	let importNameTaken = $derived(pools.some((p) => p.name === importName));
	// The label of another filesystem found on the disks: the server refuses a name that another
	// filesystem has as its label (409), and of two with one label, neither is a pool.
	let importLabelTaken = $derived(
		importable.some((fs) => fs.uuid !== target?.uuid && fs.label === importName)
	);
	let importNameError = $derived(
		!importName
			? ''
			: !importNameValid
				? $t('storage.nameRule')
				: importNameTaken
					? $t('storage.import.nameTaken', { name: importName })
					: importLabelTaken
						? $t('storage.import.labelTaken', { name: importName })
						: ''
	);
	let canImport = $derived(
		target !== null && importNameValid && !importNameTaken && !importLabelTaken && !importing
	);

	function closeImport() {
		if (importing) return;
		importError = '';
		onclose();
	}

	async function handleImport() {
		if (!target || !canImport) return;
		importing = true;
		importError = '';
		try {
			await importFilesystem(target.uuid, importName);
			importing = false;
			importError = '';
			await onimported();
		} catch (e) {
			// 409, and nothing was changed: the filesystem is in use, misses devices or is on a
			// boot, virtual or system device, the name is taken or still used by shares or apps
			// of an earlier pool, or the filesystem mounts another subvolume than its top level
			importError = messageOf(e);
		} finally {
			importing = false;
		}
	}
</script>

<Modal
	open={target !== null}
	title={$t('storage.import.title')}
	size="md"
	closable={!importing}
	onclose={closeImport}
>
	{#if target}
		<form onsubmit={(e) => { e.preventDefault(); handleImport(); }} class="space-y-4">
			<p>
				{$t('storage.import.filesystem')}
				<span class="mono-xs break-all">{target.uuid}</span>
				({target.label === null
					? $t('storage.import.noLabel')
					: $t('storage.import.label', { label: target.label })})
				{$t('storage.import.on')}
				<span class="mono-xs">{target.devices.join(', ')}</span>
				{$t('storage.import.asPool')}
			</p>
			<Alert variant="info" title={$t('storage.import.keptTitle')}>
				{$t('storage.import.keptBefore')}
				<span class="mono-xs">{poolMountpoint(importName || $t('storage.import.nameSlot'))}</span>
				{$t('storage.import.keptAfter')}
			</Alert>
			<Input
				label={$t('storage.import.name')}
				bind:value={importName}
				placeholder={$t('storage.import.namePlaceholder')}
				autocomplete="off"
				error={importNameError}
			/>
			{#if importError}
				<Alert variant="error" title={$t('storage.import.failed')}>{importError}</Alert>
			{/if}
		</form>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={importing} onclick={closeImport}>
			{$t('common.cancel')}
		</Button>
		<Button
			class="flex-1"
			icon={HardDriveDownload}
			loading={importing}
			disabled={!canImport}
			onclick={handleImport}
		>
			{$t('storage.import.confirm')}
		</Button>
	{/snippet}
</Modal>
