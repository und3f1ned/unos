<script lang="ts">
	import { createEventDispatcher, onMount } from 'svelte';
	import { getUsers, type User } from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import {
		createSmbShare,
		getSmbShare,
		SHARE_OPTIONS,
		updateSmbShare,
		type ShareOption,
		type SharePath,
		type SmbShare,
		type SmbShareUpdate
	} from '$lib/api/shares';
	import { Alert, Button, Input, Modal, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import ShareAccessPicker from './ShareAccessPicker.svelte';
	import {
		accessEntries,
		levelsFromAccess,
		pickableUsers,
		sameAccess,
		type AccessChoice
	} from './access';
	import { folderParts, folderProblem, joinSharePath, splitSharePath } from './paths';

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
	// The protocol options (ADR-0377), each off by default.
	let options: Record<ShareOption, boolean> = noOptions();

	function noOptions(): Record<ShareOption, boolean> {
		return { encryption_required: false, time_machine: false, previous_versions: false };
	}

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
			options = {
				encryption_required: from.encryption_required,
				time_machine: from.time_machine,
				previous_versions: from.previous_versions
			};
		} else {
			name = '';
			base = offered[0]?.path ?? '';
			folder = '';
			comment = '';
			browseable = true;
			guestOk = false;
			levels = {};
			options = noOptions();
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
	// A pool's or a volume's name, and its kind in the page's language.
	$: locationOptions = locations.map((p) => ({
		value: p.path,
		label: $t(p.type === 'volume' ? 'shares.location.volume' : 'shares.location.pool', { name: p.name })
	}));
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
	// The folders that are never shared, as the server words them (supervisor/src/shares/service.py).
	$: folderMessages = {
		poolRoot: $t('shares.form.poolRoot'),
		apps: (pool: string) => $t('shares.form.appsFolder', { pool })
	};
	$: problem = !base ? $t('shares.location.choose') : folderProblem(location, parts, folderMessages);
	// A missing folder is shown once the user tried to save; "apps" at once.
	$: folderError =
		locationEditable && checkPath && problem && (attempted || parts.length > 0) ? problem : '';
	$: nameError = !editing && attempted && !name.trim() ? $t('shares.form.nameRequired') : '';
	// A share that is not served cannot be given access (the server answers 400)
	// until its folder is changed to one that can be served. Access it still has
	// can only be removed: the server saves such a share once it grants nothing.
	$: accessLocked = editing && loaded?.served === false && (!pathChanged || problem !== '');
	$: loadedGrants = (loaded?.access.length ?? 0) > 0 || loaded?.guest_ok === true;
	// While access is locked, guest access can be turned off, and on again
	// only when the loaded share has it. Guests' connections are never encrypted:
	// a share that requires encryption has no guests (the server refuses both).
	$: guestLocked =
		(accessLocked && !guestOk && loaded?.guest_ok !== true) || (options.encryption_required && !guestOk);
	$: encryptionLocked = guestOk && !options.encryption_required;
	// The options in the form's order, with the catalog keys of their names and of their hints.
	const OPTION_KEYS: Record<ShareOption, string> = {
		encryption_required: 'shares.form.option.encryption',
		time_machine: 'shares.form.option.timeMachine',
		previous_versions: 'shares.form.option.previousVersions'
	};
	const OPTION_HINT_KEYS: Record<ShareOption, string> = {
		encryption_required: 'shares.form.option.encryptionHint',
		time_machine: 'shares.form.option.timeMachineHint',
		previous_versions: 'shares.form.option.previousVersionsHint'
	};
	$: optionChoices = SHARE_OPTIONS.map((name) => ({
		name,
		label: $t(OPTION_KEYS[name]),
		hint: $t(OPTION_HINT_KEYS[name]),
		locked: name === 'encryption_required' && encryptionLocked
	}));

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
		for (const name of SHARE_OPTIONS) {
			if (options[name] !== from[name]) patch[name] = options[name];
		}
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
					access: currentAccess,
					...options
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
				reloadedNotice = $t('shares.form.reloaded');
			}
		} catch (e) {
			reloadedNotice = $t('shares.form.reloadFailed', { message: messageOf(e) });
		}
	}

	function close() {
		if (!saving) dispatch('close', { stale });
	}
</script>

<Modal
	open
	title={editing ? $t('shares.form.editTitle', { name: share?.name ?? '' }) : $t('shares.form.addTitle')}
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
			label={$t('shares.form.name')}
			bind:value={name}
			placeholder={$t('shares.form.namePlaceholder')}
			autocomplete="off"
			maxlength={32}
			disabled={editing}
			required={!editing}
			error={nameError}
		/>

		<div class="space-y-3">
			{#if loaded && !loaded.served}
				<Alert variant="error" title={$t('shares.form.notServed')}>
					<p>{loaded.path_problem ?? $t('shares.form.cannotBeShared')}</p>
					<!-- In parts around the folder's path: ADR-0296. -->
					<p class="mt-1">
						{$t('shares.form.currentFolderBefore')}
						<span class="mono-xs break-all">{loaded.path}</span>{$t('shares.form.currentFolderAfter')}
					</p>
					{#if loadedGrants}
						<p class="mt-1">{$t('shares.form.notServedGrants')}</p>
					{/if}
				</Alert>
			{/if}

			{#if locationEditable}
				{#if paths.length === 0}
					<Alert variant="warning">{$t('shares.location.noneMounted')}</Alert>
				{:else if locations.length === 0}
					<Alert variant="warning">
						<p>{$t('shares.location.noneManaged')}</p>
						<p class="mt-1">{$t('shares.form.unmanagedPools')}</p>
					</Alert>
				{:else}
					<Select
						id="share-location"
						label={$t('shares.location.label')}
						bind:value={base}
						options={locationOptions}
						placeholder={$t('shares.location.choose')}
						hint={locations.length < paths.length ? $t('shares.form.unmanagedPools') : ''}
					/>
				{/if}
				<Input
					id="share-folder"
					label={$t('shares.location.folder')}
					bind:value={folder}
					placeholder={$t('shares.form.folderPlaceholder')}
					autocomplete="off"
					maxlength={4096}
					error={folderError}
					hint={location?.type === 'volume' ? $t('shares.form.volumeHint') : $t('shares.form.poolHint')}
				/>
				{#if finalPath && !problem && (!editing || pathChanged)}
					<p class="text-sm text-secondary">
						{$t('shares.form.sharedFolder')} <span class="mono break-all">{finalPath}</span>
					</p>
				{/if}
			{:else if loaded}
				<div>
					<p class="form-label">{$t('shares.location.folder')}</p>
					<p class="mono break-all">{loaded.path}</p>
				</div>
			{/if}
			<p class="form-hint">{$t('shares.form.appsNote')}</p>
		</div>

		<Input
			id="share-comment"
			label={$t('shares.form.comment')}
			bind:value={comment}
			autocomplete="off"
			maxlength={1024}
		/>

		<div class="space-y-2">
			<label class="flex items-center gap-2 cursor-pointer">
				<input type="checkbox" class="form-checkbox" bind:checked={browseable} />
				{$t('shares.form.browseable')}
			</label>
			<label class="flex items-center gap-2 {guestLocked ? 'opacity-60' : 'cursor-pointer'}">
				<input
					type="checkbox"
					class="form-checkbox"
					bind:checked={guestOk}
					disabled={guestLocked}
				/>
				{$t('shares.form.guest')}
			</label>
			{#if guestOk}
				<Alert variant="warning">{$t('shares.form.guestWarning')}</Alert>
			{/if}
		</div>

		<fieldset class="space-y-2">
			<legend class="form-label">{$t('shares.form.options')}</legend>
			{#each optionChoices as choice (choice.name)}
				<div>
					<label class="flex items-center gap-2 {choice.locked ? 'opacity-60' : 'cursor-pointer'}">
						<input
							type="checkbox"
							class="form-checkbox"
							bind:checked={options[choice.name]}
							disabled={choice.locked}
							aria-describedby="share-option-{choice.name}"
						/>
						{choice.label}
					</label>
					<p id="share-option-{choice.name}" class="form-hint ml-6">{choice.hint}</p>
				</div>
			{/each}
		</fieldset>

		{#if usersLoading}
			<p class="text-sm text-muted">{$t('shares.form.loadingUsers')}</p>
		{:else if usersError}
			<Alert variant="error" title={$t('shares.form.usersError')}>{usersError}</Alert>
		{:else}
			<ShareAccessPicker
				{users}
				bind:levels
				reduceOnly={accessLocked}
				reduceOnlyHint={$t('shares.form.notServedHint')}
				savedLevels={loadedLevels}
			/>
		{/if}

		<p class="form-hint">{$t('shares.form.accessChangeNote')}</p>
	</form>

	<!-- Below the scrolling form, so that it is seen after a save. -->
	{#if error}
		<Alert variant="error" title={editing ? $t('shares.form.notSaved') : $t('shares.form.notCreated')}>
			<p>{error}</p>
			{#if reloadedNotice}
				<p class="mt-2">{reloadedNotice}</p>
			{/if}
		</Alert>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={saving} on:click={close}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={saving} on:click={submit}>
			{editing ? $t('shares.form.save') : $t('shares.form.create')}
		</Button>
	</svelte:fragment>
</Modal>
