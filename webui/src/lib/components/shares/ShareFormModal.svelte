<script lang="ts">
	import { createEventDispatcher, onMount } from 'svelte';
	import { getUsers, type User } from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import {
		createSmbShare,
		getSmbShare,
		updateSmbShare,
		type SharePath,
		type SmbShare,
		type SmbShareUpdate
	} from '$lib/api/shares';
	import { Alert, Button, Input, Modal, Select } from '$lib/components/ui';
	import ShareAccessPicker from './ShareAccessPicker.svelte';
	import {
		accessEntries,
		levelsFromAccess,
		pickableUsers,
		sameAccess,
		type AccessChoice
	} from './access';
	import { folderParts, folderProblem, joinSharePath, splitSharePath } from './paths';
	import {
		ACCESS_CHANGE_NOTE,
		APPS_NOTE,
		GUEST_WARNING,
		NOT_SERVED_GRANTS_NOTE,
		NOT_SERVED_HINT,
		UNMANAGED_POOLS_NOTE
	} from './texts';

	/** The share to edit; null to create one. The dialog is open while mounted. */
	export let share: SmbShare | null = null;
	/** Pools and volumes (GET /shares/paths); only those of pools that UNOS manages are offered. */
	export let paths: SharePath[] = [];

	const dispatch = createEventDispatcher<{
		/** `stale`: the server refused a change (409, 404), so the page's list may be out of date. */
		close: { stale: boolean };
		saved: { share: SmbShare; created: boolean };
	}>();

	/** The locations an SMB share can use: the server refuses pools that UNOS does not manage. */
	function smbLocations(all: SharePath[]): SharePath[] {
		return all.filter((location) => location.managed);
	}

	const editing = share !== null;
	// The share as last loaded: an edit sends only what differs from it.
	let loaded: SmbShare | null = share;

	let name = '';
	let base = '';
	let folder = '';
	let comment = '';
	let browseable = true;
	let guestOk = false;
	let levels: Record<string, AccessChoice> = {};

	let users: User[] = [];
	let usersLoading = true;
	let usersError = '';

	let saving = false;
	let attempted = false;
	let error = '';
	let reloadedNotice = '';
	let stale = false;

	function fillForm(from: SmbShare | null) {
		const offered = smbLocations(paths);
		if (from) {
			const split = splitSharePath(from.path, offered);
			name = from.name;
			base = split.base;
			folder = split.folder;
			comment = from.comment;
			browseable = from.browseable;
			guestOk = from.guest_ok;
			levels = levelsFromAccess(from.access);
		} else {
			name = '';
			base = offered[0]?.path ?? '';
			folder = '';
			comment = '';
			browseable = true;
			guestOk = false;
			levels = {};
		}
		attempted = false;
	}

	fillForm(share);

	onMount(() => {
		void loadUsers();
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function loadUsers() {
		usersLoading = true;
		usersError = '';
		try {
			users = await getUsers();
		} catch (e) {
			users = [];
			usersError = messageOf(e);
		} finally {
			usersLoading = false;
		}
	}

	$: locations = smbLocations(paths);
	$: locationOptions = locations.map((p) => ({ value: p.path, label: p.label }));
	$: location = locations.find((p) => p.path === base);
	$: parts = folderParts(folder);
	$: finalPath = base ? joinSharePath(base, parts) : '';
	// The folder of a served share is fixed; one that is not served can be moved.
	$: locationEditable = !editing || loaded?.served === false;
	$: pathChanged = editing && locationEditable && base !== '' && finalPath !== loaded?.path;
	// Checked when creating, when the folder of a share that is not served
	// changes, and when a folder is typed without a location for it. The folder
	// a share was loaded with is the server's to judge: its comment, for
	// example, can change without a new folder.
	$: checkPath = !editing || pathChanged || (locationEditable && base === '' && parts.length > 0);
	$: problem = !base ? 'Choose a pool or volume' : folderProblem(location, parts);
	// A missing folder is shown once the user tried to save; "apps" at once.
	$: folderError =
		locationEditable && checkPath && problem && (attempted || parts.length > 0) ? problem : '';
	$: nameError = !editing && attempted && !name.trim() ? 'Enter a name for the share' : '';
	// A share that is not served cannot be given access (the server answers 400)
	// until its folder is changed to one that can be served. Access it still has
	// can only be removed: the server saves such a share once it grants nothing.
	$: accessLocked = editing && loaded?.served === false && (!pathChanged || problem !== '');
	$: loadedGrants = (loaded?.access.length ?? 0) > 0 || loaded?.guest_ok === true;
	// While access is locked, guest access can be turned off, and on again
	// only when the loaded share has it.
	$: guestLocked = accessLocked && !guestOk && loaded?.guest_ok !== true;

	$: listedUsers = pickableUsers(users);
	$: loadedLevels = levelsFromAccess(loaded?.access ?? []);
	$: currentAccess = accessEntries(listedUsers, levels);
	$: loadedAccess = accessEntries(listedUsers, loadedLevels);

	// A changed access list or guest setting is always sent: while access is
	// locked, the picker and the guest checkbox can only remove access, or give
	// back what the loaded share has (then nothing is sent for it).
	function buildPatch(from: SmbShare): SmbShareUpdate {
		const patch: SmbShareUpdate = {};
		if (pathChanged) patch.path = finalPath;
		if (comment !== from.comment) patch.comment = comment;
		if (browseable !== from.browseable) patch.browseable = browseable;
		if (guestOk !== from.guest_ok) patch.guest_ok = guestOk;
		if (!sameAccess(currentAccess, loadedAccess)) patch.access = currentAccess;
		return patch;
	}

	async function submit() {
		if (saving) return;
		attempted = true;
		error = '';
		reloadedNotice = '';
		// Reactive values that depend on `attempted` are only updated after this function.
		if ((!editing && !name.trim()) || (locationEditable && checkPath && problem)) return;

		saving = true;
		try {
			if (!loaded) {
				const created = await createSmbShare({
					name,
					path: finalPath,
					comment,
					browseable,
					guest_ok: guestOk,
					access: currentAccess
				});
				dispatch('saved', { share: created, created: true });
				return;
			}
			const patch = buildPatch(loaded);
			if (Object.keys(patch).length === 0) {
				dispatch('close', { stale });
				return;
			}
			// The version makes the server refuse (409) a share changed since it was loaded.
			const updated = await updateSmbShare(loaded.name, {
				expected_version: loaded.version,
				...patch
			});
			dispatch('saved', { share: updated, created: false });
		} catch (e) {
			error = messageOf(e);
			if (e instanceof ApiError && (e.status === 409 || e.status === 404)) stale = true;
			if (e instanceof ApiError && e.status === 409) {
				await reloadAfterConflict();
			}
		} finally {
			saving = false;
		}
	}

	/**
	 * After a 409, load what the server has now: the users (an id may name
	 * another user now) and, when editing, the share. A share changed meanwhile
	 * resets the form, since sending this form's access list again would undo
	 * the other change.
	 */
	async function reloadAfterConflict() {
		await loadUsers();
		if (!loaded) return;
		try {
			const fresh = await getSmbShare(loaded.name);
			if (fresh.version !== loaded.version) {
				loaded = fresh;
				fillForm(fresh);
				reloadedNotice =
					'The form now shows the current settings of the share: make your changes again.';
			}
		} catch (e) {
			reloadedNotice = `The share could not be loaded again: ${messageOf(e)}`;
		}
	}

	function close() {
		if (!saving) dispatch('close', { stale });
	}
</script>

<Modal
	open
	title={editing ? `Edit share ${share?.name ?? ''}` : 'Add SMB share'}
	size="xl"
	closable={!saving}
	on:close={close}
>
	<form
		on:submit|preventDefault={submit}
		class="space-y-4 max-h-[60vh] overflow-y-auto scrollbar-thin pr-1"
	>
		<Input
			id="share-name"
			label="Name"
			bind:value={name}
			placeholder="documents"
			autocomplete="off"
			maxlength={32}
			disabled={editing}
			required={!editing}
			error={nameError}
		/>

		<div class="space-y-3">
			{#if loaded && !loaded.served}
				<Alert variant="error" title="This share is not served">
					<p>{loaded.path_problem ?? 'Its folder cannot be shared.'}</p>
					<p class="mt-1">
						Folder now: <span class="mono-xs break-all">{loaded.path}</span>. Choose a folder that
						can be shared.
					</p>
					{#if loadedGrants}
						<p class="mt-1">{NOT_SERVED_GRANTS_NOTE}</p>
					{/if}
				</Alert>
			{/if}

			{#if locationEditable}
				{#if paths.length === 0}
					<Alert variant="warning">
						No pool or volume is mounted. Mount a pool on the Storage page first.
					</Alert>
				{:else if locations.length === 0}
					<Alert variant="warning">
						<p>No pool that UNOS manages is mounted.</p>
						<p class="mt-1">{UNMANAGED_POOLS_NOTE}</p>
					</Alert>
				{:else}
					<Select
						id="share-location"
						label="Location"
						bind:value={base}
						options={locationOptions}
						placeholder="Choose a pool or volume"
						hint={locations.length < paths.length ? UNMANAGED_POOLS_NOTE : ''}
					/>
				{/if}
				<Input
					id="share-folder"
					label="Folder"
					bind:value={folder}
					placeholder="documents"
					autocomplete="off"
					maxlength={4096}
					error={folderError}
					hint={location?.type === 'volume'
						? 'A folder inside the pool or volume; created if missing. Leave empty to share the whole volume'
						: 'A folder inside the pool or volume; created if missing.'}
				/>
				{#if finalPath && !problem && (!editing || pathChanged)}
					<p class="text-sm text-secondary">
						Shared folder: <span class="mono break-all">{finalPath}</span>
					</p>
				{/if}
			{:else if loaded}
				<div>
					<p class="form-label">Folder</p>
					<p class="mono break-all">{loaded.path}</p>
				</div>
			{/if}
			<p class="form-hint">{APPS_NOTE}</p>
		</div>

		<Input
			id="share-comment"
			label="Comment"
			bind:value={comment}
			autocomplete="off"
			maxlength={1024}
		/>

		<div class="space-y-2">
			<label class="flex items-center gap-2 cursor-pointer">
				<input type="checkbox" class="form-checkbox" bind:checked={browseable} />
				Show in network browsing
			</label>
			<label class="flex items-center gap-2 {guestLocked ? 'opacity-60' : 'cursor-pointer'}">
				<input
					type="checkbox"
					class="form-checkbox"
					bind:checked={guestOk}
					disabled={guestLocked}
				/>
				Guests can read this share without a password
			</label>
			{#if guestOk}
				<Alert variant="warning">{GUEST_WARNING}</Alert>
			{/if}
		</div>

		{#if usersLoading}
			<p class="text-sm text-muted">Loading users...</p>
		{:else if usersError}
			<Alert variant="error" title="The users could not be loaded">{usersError}</Alert>
		{:else}
			<ShareAccessPicker
				{users}
				bind:levels
				reduceOnly={accessLocked}
				reduceOnlyHint={NOT_SERVED_HINT}
				savedLevels={loadedLevels}
			/>
		{/if}

		<p class="form-hint">{ACCESS_CHANGE_NOTE}</p>
	</form>

	<!-- Below the scrolling form, so that it is seen after a save. -->
	{#if error}
		<Alert variant="error" title={editing ? 'Share was not saved' : 'Share was not created'}>
			<p>{error}</p>
			{#if reloadedNotice}
				<p class="mt-2">{reloadedNotice}</p>
			{/if}
		</Alert>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={saving} on:click={close}>Cancel</Button>
		<Button class="flex-1" loading={saving} on:click={submit}>
			{editing ? 'Save' : 'Create share'}
		</Button>
	</svelte:fragment>
</Modal>
