<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import {
		createNfsExport,
		type NfsExport,
		type SharePath,
		type ShareAccessLevel
	} from '$lib/api/shares';
	import { Alert, Button, Input, Modal, Select } from '$lib/components/ui';
	import { folderParts, folderProblem, joinSharePath } from './paths';
	import { NFS_NOTE, NFS_POOL_ROOT_MESSAGE, NFS_UNMANAGED_POOLS_NOTE, nfsAppsFolderMessage } from './texts';

	/** Pools and volumes (GET /shares/paths); only those of pools that UNOS manages are offered. */
	export let paths: SharePath[] = [];

	const dispatch = createEventDispatcher<{ close: void; saved: { exp: NfsExport } }>();

	const accessOptions: { value: ShareAccessLevel; label: string }[] = [
		{ value: 'read', label: 'Read only' },
		{ value: 'write', label: 'Read & write' }
	];
	const nfsMessages = { poolRoot: NFS_POOL_ROOT_MESSAGE, apps: nfsAppsFolderMessage };

	// The server refuses a folder of a pool that UNOS does not manage (ADR-0262).
	const locations = paths.filter((location) => location.managed);
	let base = locations[0]?.path ?? '';
	let folder = '';
	let client = '';
	let access: ShareAccessLevel = 'read';

	let saving = false;
	let attempted = false;
	let error = '';

	$: locationOptions = locations.map((p) => ({ value: p.path, label: p.label }));
	$: location = locations.find((p) => p.path === base);
	$: parts = folderParts(folder);
	$: finalPath = base ? joinSharePath(base, parts) : '';
	$: problem = !base ? 'Choose a pool or volume' : folderProblem(location, parts, nfsMessages);
	// A missing folder is shown once the user tried to save; "apps" at once.
	$: folderError = problem && (attempted || parts.length > 0) ? problem : '';
	$: clientError = attempted && !client.trim() ? 'Enter an address or network' : '';

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function submit() {
		if (saving) return;
		attempted = true;
		error = '';
		// Reactive values that depend on `attempted` are only updated after this function.
		if (problem || !client.trim()) return;

		saving = true;
		try {
			const created = await createNfsExport({ path: finalPath, client: client.trim(), access });
			dispatch('saved', { exp: created });
		} catch (e) {
			error = messageOf(e);
		} finally {
			saving = false;
		}
	}

	function close() {
		if (!saving) dispatch('close');
	}
</script>

<Modal open title="Add NFS export" size="md" closable={!saving} on:close={close}>
	<form on:submit|preventDefault={submit} class="space-y-4">
		<div class="space-y-3">
			{#if paths.length === 0}
				<Alert variant="warning">No pool or volume is mounted. Mount a pool on the Storage page first.</Alert>
			{:else if locations.length === 0}
				<Alert variant="warning">
					<p>No pool that UNOS manages is mounted.</p>
					<p class="mt-1">{NFS_UNMANAGED_POOLS_NOTE}</p>
				</Alert>
			{:else}
				<Select
					id="nfs-location"
					label="Location"
					bind:value={base}
					options={locationOptions}
					placeholder="Choose a pool or volume"
					hint={locations.length < paths.length ? NFS_UNMANAGED_POOLS_NOTE : ''}
				/>
			{/if}
			<Input
				id="nfs-folder"
				label="Folder"
				bind:value={folder}
				placeholder="media"
				autocomplete="off"
				maxlength={4096}
				error={folderError}
				hint={location?.type === 'volume'
					? 'An existing folder inside the volume. Leave empty to export the whole volume'
					: 'An existing folder inside the pool, for example one made in Files'}
			/>
			{#if finalPath && !problem}
				<p class="text-sm text-secondary">
					Exported folder: <span class="mono break-all">{finalPath}</span>
				</p>
			{/if}
		</div>

		<Input
			id="nfs-client"
			label="Client network"
			bind:value={client}
			placeholder="192.168.1.0/24"
			autocomplete="off"
			spellcheck={false}
			maxlength={64}
			error={clientError}
			hint="One address or network of your local network, such as 192.168.1.0/24, 192.168.1.20 or fd00::/64"
		/>

		<Select id="nfs-access" label="Access" bind:value={access} options={accessOptions} />

		<p class="form-hint">{NFS_NOTE}</p>
	</form>

	{#if error}
		<Alert variant="error" title="Export was not created">{error}</Alert>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={saving} on:click={close}>Cancel</Button>
		<Button class="flex-1" loading={saving} on:click={submit}>Create export</Button>
	</svelte:fragment>
</Modal>
