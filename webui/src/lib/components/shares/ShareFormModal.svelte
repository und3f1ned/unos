<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { getUsers, type User } from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import { getGroups, type Group } from '$lib/api/groups';
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
	import { Alert, Button, Input, Modal, Skeleton } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import LocationFields from './LocationFields.svelte';
	import ShareAccessTable from './ShareAccessTable.svelte';
	import ShareSettingsFields from './ShareSettingsFields.svelte';
	import {
		accessEntries,
		groupAccessEntries,
		groupLevelsFromAccess,
		levelsFromAccess,
		loweredAccess,
		pickableUsers,
		sameAccess,
		sameGroupAccess,
		type AccessChoice,
		type Lowering
	} from './access';
	import { messageOf } from './errors';
	import { folderParts, folderProblem, joinSharePath, managedLocations, splitSharePath } from './paths';

	type Props = {
		/** The share to edit; null to create one. The dialog is open while mounted. */
		share?: SmbShare | null;
		/** Pools and volumes (GET /shares/paths); only those of pools that UNOS manages are offered. */
		paths?: SharePath[];
		/**
		 * The user closed the dialog (not while saving), or a save changed nothing. `stale`: the
		 * server refused a change (409, 404), so the page's list may be out of date.
		 */
		onclose?: (closed: { stale: boolean }) => void;
		/** The server took the share: the one it created (`created`) or changed. */
		onsaved?: (saved: { share: SmbShare; created: boolean }) => void;
	};

	let { share = null, paths = [], onclose, onsaved }: Props = $props();

	// The share the dialog was opened with: the dialog is mounted for one share, or none.
	const opened = untrack(() => share);
	const editing = opened !== null;
	// The share as last loaded: an edit sends only what differs from it.
	let loaded: SmbShare | null = $state(opened);

	let name = $state('');
	let base = $state('');
	let folder = $state('');
	let comment = $state('');
	let browseable = $state(true);
	let guestOk = $state(false);
	let levels: Record<string, AccessChoice> = $state({});
	let groupLevels: Record<string, AccessChoice> = $state({});
	// The protocol options (ADR-0380), each off by default.
	let options: Record<ShareOption, boolean> = $state(noOptions());

	// The levels' names, by level.
	const LEVEL_KEYS: Record<AccessChoice, string> = {
		none: 'shares.access.none',
		read: 'shares.access.read',
		write: 'shares.access.write'
	};

	function noOptions(): Record<ShareOption, boolean> {
		return { encryption_required: false, time_machine: false, previous_versions: false };
	}

	let users: User[] = $state([]);
	let groups: Group[] = $state([]);
	let usersLoading = $state(true);
	let usersError = $state('');

	let saving = $state(false);
	let attempted = $state(false);
	let error = $state('');
	let reloadedNotice = $state('');
	// Only the handlers read it, when the dialog closes.
	let stale = false;
	// What a save of a served share takes away, asked about before it is sent: only
	// lowered access, guest access turned off, encryption required and Previous Versions turned
	// off close the share's open connections (ADR-0103). null: no question open.
	let confirming: { access: Lowering[]; settings: string[] } | null = $state(null);

	function fillForm(from: SmbShare | null) {
		const offered = managedLocations(paths);
		if (from) {
			const split = splitSharePath(from.path, offered);
			name = from.name;
			base = split.base;
			folder = split.folder;
			comment = from.comment;
			browseable = from.browseable;
			guestOk = from.guest_ok;
			levels = levelsFromAccess(from.access);
			groupLevels = groupLevelsFromAccess(from.group_access ?? []);
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
			groupLevels = {};
			options = noOptions();
		}
		attempted = false;
	}

	fillForm(opened);

	onMount(() => {
		void loadUsers();
	});

	/** The users and the groups that the access picker lists. */
	async function loadUsers() {
		usersLoading = true;
		usersError = '';
		try {
			[users, groups] = await Promise.all([getUsers(), getGroups()]);
		} catch (e) {
			users = [];
			groups = [];
			usersError = messageOf(e);
		} finally {
			usersLoading = false;
		}
	}

	const locations = $derived(managedLocations(paths));
	const location = $derived(locations.find((p) => p.path === base));
	const parts = $derived(folderParts(folder));
	const finalPath = $derived(base ? joinSharePath(base, parts) : '');
	// The folder of a served share is fixed; one that is not served can be moved.
	const locationEditable = $derived(!editing || loaded?.served === false);
	const pathChanged = $derived(editing && locationEditable && base !== '' && finalPath !== loaded?.path);
	// Checked when creating, when the folder of a share that is not served
	// changes, and when a folder is typed without a location for it. The folder
	// a share was loaded with is the server's to judge: its comment, for
	// example, can change without a new folder.
	const checkPath = $derived(!editing || pathChanged || (locationEditable && base === '' && parts.length > 0));
	// The folders that are never shared, as the server words them (supervisor/src/shares/service.py).
	const folderMessages = $derived({
		poolRoot: $t('shares.form.poolRoot'),
		apps: (pool: string) => $t('shares.form.appsFolder', { pool })
	});
	const problem = $derived(!base ? $t('shares.location.choose') : folderProblem(location, parts, folderMessages));
	// A missing folder is shown once the user tried to save; "apps" at once.
	const folderError = $derived(
		locationEditable && checkPath && problem && (attempted || parts.length > 0) ? problem : ''
	);
	const nameError = $derived(!editing && attempted && !name.trim() ? $t('shares.form.nameRequired') : '');
	// A share that is not served cannot be given access (the server answers 400)
	// until its folder is changed to one that can be served. Access it still has
	// can only be removed: the server saves such a share once it grants nothing.
	const accessLocked = $derived(editing && loaded?.served === false && (!pathChanged || problem !== ''));
	const loadedGrants = $derived(
		(loaded?.access.length ?? 0) > 0 ||
			(loaded?.group_access?.length ?? 0) > 0 ||
			loaded?.guest_ok === true
	);
	// While access is locked, guest access can be turned off, and on again
	// only when the loaded share has it. Guests' connections are never encrypted:
	// a share that requires encryption has no guests (the server refuses both).
	const guestLocked = $derived(
		(accessLocked && !guestOk && loaded?.guest_ok !== true) || (options.encryption_required && !guestOk)
	);
	const encryptionLocked = $derived(guestOk && !options.encryption_required);

	const listedUsers = $derived(pickableUsers(users));
	const loadedLevels = $derived(levelsFromAccess(loaded?.access ?? []));
	const currentAccess = $derived(accessEntries(listedUsers, levels));
	const loadedAccess = $derived(accessEntries(listedUsers, loadedLevels));
	const loadedGroupLevels = $derived(groupLevelsFromAccess(loaded?.group_access ?? []));
	const currentGroupAccess = $derived(groupAccessEntries(groups, groupLevels));
	const loadedGroupAccess = $derived(groupAccessEntries(groups, loadedGroupLevels));

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
		if (!sameGroupAccess(currentGroupAccess, loadedGroupAccess)) patch.group_access = currentGroupAccess;
		for (const name of SHARE_OPTIONS) {
			if (options[name] !== from[name]) patch[name] = options[name];
		}
		return patch;
	}

	/**
	 * What saving the edited share takes away from its open connections; null when nothing. As the
	 * server closes them (smbconf.shrunk_shares, ADR-0103) only when smb.conf shrinks, it counts
	 * only what smb.conf has now: a share that someone can connect to, the access of a user whose
	 * SMB is active or of a group with such a member, and a guest access or Previous Versions that
	 * smb.conf did not leave out.
	 */
	function disconnecting(from: SmbShare): { access: Lowering[]; settings: string[] } | null {
		if (!from.served || !from.available) return null;
		const inFile = new Set(from.access.filter((entry) => entry.effective).map((entry) => entry.user_id));
		const groupsInFile = new Set(
			from.group_access.filter((entry) => entry.members.some((m) => m.effective)).map((entry) => entry.group_id)
		);
		const access = loweredAccess(
			{
				users: loadedAccess.filter((entry) => inFile.has(entry.user_id)),
				groups: loadedGroupAccess.filter((entry) => groupsInFile.has(entry.group_id))
			},
			{ users: currentAccess, groups: currentGroupAccess }
		);
		const settings = [
			from.guest_ok && !from.not_rendered.guest_ok && !guestOk ? $t('shares.form.lower.guest') : '',
			!from.encryption_required && options.encryption_required ? $t('shares.form.lower.encryption') : '',
			from.previous_versions && !from.not_rendered.previous_versions && !options.previous_versions
				? $t('shares.form.lower.previousVersions')
				: ''
		].filter(Boolean);
		return access.length > 0 || settings.length > 0 ? { access, settings } : null;
	}

	function submit() {
		if (saving) return;
		attempted = true;
		error = '';
		reloadedNotice = '';
		// The conditions of the errors shown under the fields, now that `attempted` is set.
		if ((!editing && !name.trim()) || (locationEditable && checkPath && problem)) return;
		const question = loaded ? disconnecting(loaded) : null;
		if (question) {
			confirming = question;
			return;
		}
		void save();
	}

	function confirmSave() {
		confirming = null;
		void save();
	}

	async function save() {
		if (saving) return;
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
					group_access: currentGroupAccess,
					...options
				});
				onsaved?.({ share: created, created: true });
				return;
			}
			const patch = buildPatch(loaded);
			if (Object.keys(patch).length === 0) {
				onclose?.({ stale });
				return;
			}
			// The version makes the server refuse (409) a share changed since it was loaded.
			const updated = await updateSmbShare(loaded.name, {
				expected_version: loaded.version,
				...patch
			});
			onsaved?.({ share: updated, created: false });
		} catch (e) {
			error = messageOf(e);
			if (e instanceof ApiError && (e.status === 409 || e.status === 404)) stale = true;
			if (e instanceof ApiError && e.status === 409) {
				await reloadAfterConflict();
			} else if (e instanceof ApiError && e.status === 400) {
				// e.g. "Group 3 does not exist": a group deleted meanwhile leaves the picker.
				await loadUsers();
			}
		} finally {
			saving = false;
		}
	}

	/**
	 * After a 409, load what the server has now: the users (an id may name
	 * another user now) and the groups and, when editing, the share. A share changed meanwhile
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
		if (!saving) onclose?.({ stale });
	}
</script>

<Modal
	open
	title={editing ? $t('shares.form.editTitle', { name: share?.name ?? '' }) : $t('shares.form.addTitle')}
	size="xl"
	closable={!saving}
	onclose={close}
>
	<form
		onsubmit={(event) => {
			event.preventDefault();
			submit();
		}}
		class="space-y-4"
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
				<LocationFields
					locationId="share-location"
					folderId="share-folder"
					{paths}
					{locations}
					bind:base
					bind:folder
					{folderError}
					folderPlaceholder={$t('shares.form.folderPlaceholder')}
					folderHint={location?.type === 'volume' ? $t('shares.form.volumeHint') : $t('shares.form.poolHint')}
					unmanagedHint={$t('shares.form.unmanagedPools')}
					finalPath={finalPath && !problem && (!editing || pathChanged) ? finalPath : ''}
					finalPathLabel={$t('shares.form.sharedFolder')}
				/>
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

		<ShareSettingsFields bind:browseable bind:guestOk bind:options {guestLocked} {encryptionLocked} />

		{#if usersLoading}
			<!-- The access table's place (ADR-0430): its label and rows. -->
			<div aria-busy="true">
				<Skeleton size="sm" class="w-16 mb-1.5" />
				<Skeleton shape="row" columns={2} count={4} />
			</div>
		{:else if usersError}
			<Alert variant="error" title={$t('shares.form.usersError')}>{usersError}</Alert>
		{:else}
			<ShareAccessTable
				{users}
				bind:levels
				{groups}
				bind:groupLevels
				reduceOnly={accessLocked}
				reduceOnlyHint={$t('shares.form.notServedHint')}
				savedLevels={loadedLevels}
				savedGroupLevels={loadedGroupLevels}
			/>
		{/if}
	</form>

	<!-- Above the buttons, so that it is seen after a save. -->
	{#if error}
		<Alert variant="error" title={editing ? $t('shares.form.notSaved') : $t('shares.form.notCreated')}>
			<p>{error}</p>
			{#if reloadedNotice}
				<p class="mt-2">{reloadedNotice}</p>
			{/if}
		</Alert>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={saving} onclick={close}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={saving} onclick={submit}>
			{editing ? $t('shares.form.save') : $t('shares.form.create')}
		</Button>
	{/snippet}
</Modal>

<!-- The question before a save that closes open connections, over the form. -->
{#if confirming}
	<Modal open title={$t('shares.form.lower.title')} size="md" onclose={() => (confirming = null)}>
		<p>{$t('shares.form.lower.intro', { name: loaded?.name ?? '' })}</p>
		<ul class="list-disc space-y-1 pl-5 text-sm" aria-label={$t('shares.form.lower.list')}>
			{#each confirming.access as change (change.kind + change.name)}
				<li class="break-words">
					{$t(change.kind === 'user' ? 'shares.form.lower.user' : 'shares.form.lower.group', {
						name: change.name,
						from: $t(LEVEL_KEYS[change.from]),
						to: $t(LEVEL_KEYS[change.to])
					})}
				</li>
			{/each}
			{#each confirming.settings as setting (setting)}
				<li>{setting}</li>
			{/each}
		</ul>
		<p class="text-sm text-content-secondary">{$t('shares.form.lower.reconnect')}</p>
		{#snippet footer()}
			<Button variant="secondary" class="flex-1" onclick={() => (confirming = null)}>
				{$t('common.cancel')}
			</Button>
			<Button class="flex-1" onclick={confirmSave}>{$t('shares.form.lower.confirm')}</Button>
		{/snippet}
	</Modal>
{/if}
