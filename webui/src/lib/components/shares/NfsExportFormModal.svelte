<script lang="ts">
	import { untrack } from 'svelte';
	import {
		createNfsExport,
		type NfsExport,
		type SharePath,
		type ShareAccessLevel
	} from '$lib/api/shares';
	import { Alert, Button, HelpText, Input, Modal, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';
	import LocationFields from './LocationFields.svelte';
	import { folderParts, folderProblem, joinSharePath, managedLocations } from './paths';

	type Props = {
		/** Pools and volumes (GET /shares/paths); only those of pools that UNOS manages are offered. */
		paths?: SharePath[];
		// The user closed the dialog (not while saving): the page unmounts it.
		onclose?: () => void;
		// The server created the export.
		onsaved?: (saved: { exp: NfsExport }) => void;
	};

	let { paths = [], onclose, onsaved }: Props = $props();

	// Reactive: the labels follow the language of the page.
	const accessOptions: { value: ShareAccessLevel; label: string }[] = $derived([
		{ value: 'read', label: $t('shares.level.read') },
		{ value: 'write', label: $t('shares.level.write') }
	]);
	// The folders that are never exported, as the server words them (supervisor/src/shares/nfs.py).
	const nfsMessages = $derived({
		poolRoot: $t('shares.nfs.form.poolRoot'),
		apps: (pool: string) => $t('shares.nfs.form.appsFolder', { pool })
	});

	// The server refuses a folder of a pool that UNOS does not manage (ADR-0293). Taken from the
	// paths the dialog opened with.
	const locations = untrack(() => managedLocations(paths));
	let base = $state(locations[0]?.path ?? '');
	let folder = $state('');
	let client = $state('');
	let access: ShareAccessLevel = $state('read');

	let saving = $state(false);
	let attempted = $state(false);
	let error = $state('');

	const location = $derived(locations.find((p) => p.path === base));
	const parts = $derived(folderParts(folder));
	const finalPath = $derived(base ? joinSharePath(base, parts) : '');
	const problem = $derived(!base ? $t('shares.location.choose') : folderProblem(location, parts, nfsMessages));
	// A missing folder is shown once the user tried to save; "apps" at once.
	const folderError = $derived(problem && (attempted || parts.length > 0) ? problem : '');
	const clientError = $derived(attempted && !client.trim() ? $t('shares.nfs.form.clientRequired') : '');

	async function submit() {
		if (saving) return;
		attempted = true;
		error = '';
		// The conditions of the errors shown under the fields, now that `attempted` is set.
		if (problem || !client.trim()) return;

		saving = true;
		try {
			const created = await createNfsExport({ path: finalPath, client: client.trim(), access });
			onsaved?.({ exp: created });
		} catch (e) {
			error = messageOf(e);
		} finally {
			saving = false;
		}
	}

	function close() {
		if (!saving) onclose?.();
	}
</script>

<Modal open title={$t('shares.nfs.form.title')} size="md" closable={!saving} onclose={close}>
	<form
		onsubmit={(event) => {
			event.preventDefault();
			submit();
		}}
		class="space-y-4"
	>
		<div class="space-y-3">
			<LocationFields
				locationId="nfs-location"
				folderId="nfs-folder"
				{paths}
				{locations}
				bind:base
				bind:folder
				{folderError}
				folderPlaceholder={$t('shares.nfs.form.folderPlaceholder')}
				folderHint={location?.type === 'volume' ? $t('shares.nfs.form.volumeHint') : $t('shares.nfs.form.poolHint')}
				unmanagedHint={$t('shares.nfs.form.unmanagedPools')}
				finalPath={finalPath && !problem ? finalPath : ''}
				finalPathLabel={$t('shares.nfs.form.exportedFolder')}
			/>
		</div>

		<Input
			id="nfs-client"
			label={$t('shares.nfs.form.client')}
			bind:value={client}
			placeholder="192.168.1.0/24"
			autocomplete="off"
			spellcheck={false}
			maxlength={64}
			error={clientError}
			hint={$t('shares.nfs.form.clientHint')}
		/>

		<Select
			id="nfs-access"
			label={$t('shares.access.title')}
			bind:value={access}
			options={accessOptions}
		/>

		<HelpText text={$t('shares.nfs.note')} />
	</form>

	{#if error}
		<Alert variant="error" title={$t('shares.nfs.form.notCreated')}>{error}</Alert>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={saving} onclick={close}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={saving} onclick={submit}>{$t('shares.nfs.form.create')}</Button>
	{/snippet}
</Modal>
