<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import {
		createUser,
		deleteUser,
		disableUserSmb,
		endSessions,
		enableUserSmb,
		getUsers,
		updateUser,
		USERNAME_PATTERN,
		type User,
		type UserUpdate
	} from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import { getGroups } from '$lib/api/groups';
	import { getSmbShares } from '$lib/api/shares';
	import { user as currentUser } from '$lib/stores/auth';
	import { paletteRequest, type PaletteRequest } from '$lib/stores/palette';
	import { Edit, LogOut, Shield, ShieldOff, Trash2, UserPlus, Users } from 'lucide-svelte';
	import { Alert, Badge, Button, Card, Input, Modal } from '$lib/components/ui';
	import { SmbStateBadge } from '$lib/components/shares';
	import { GroupsCard } from '$lib/components/users';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';

	const MIN_PASSWORD_LENGTH = 8;

	let users = $state.raw<User[]>([]);
	let loading = $state(true);
	let error = $state('');
	// Outcome of the last change: shown until dismissed.
	let notice = $state('');
	let warnings = $state.raw<string[]>([]);
	// A change refused after its dialog closed (the user's id names someone else now).
	let actionError = $state.raw<{ title: string; message: string } | null>(null);

	onMount(async () => {
		// Redirect non-admin users
		if (!$currentUser?.is_admin) {
			goto('/');
			return;
		}
		await loadUsers();
	});

	// Also watch for user changes (in case they navigate here before user loads). Before the
	// first render too, as before.
	$effect.pre(() => {
		const current = $currentUser;
		if (current !== null && !current.is_admin) untrack(() => goto('/'));
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function loadUsers(showSpinner = true) {
		if (showSpinner) loading = true;
		try {
			users = await getUsers();
			error = '';
		} catch (e) {
			error = messageOf(e) || $t('users.loadFailed');
		} finally {
			loading = false;
		}
	}

	function showOutcome(message: string, newWarnings: string[] | undefined) {
		notice = message;
		warnings = newWarnings ?? [];
		actionError = null;
	}

	/**
	 * After a refusal that can mean the list is out of date (409, e.g. "User N
	 * is now 'x'; reload the page", or 404), load the users again and return
	 * `target` as stored now. null: its id names someone else now, or nobody,
	 * so nothing a dialog prepared for `target` applies any more. Other
	 * errors: `target` itself.
	 */
	async function currentAfterRefusal(e: unknown, target: User): Promise<User | null> {
		if (!(e instanceof ApiError && (e.status === 409 || e.status === 404))) return target;
		await loadUsers(false);
		const now = users.find((u) => u.id === target.id);
		return now !== undefined && now.username === target.username ? now : null;
	}

	// The texts that the functions below make follow a change of the language: the markup that
	// calls them reads $t and $intlLocale through them. Dates are in the language of the page
	// (ADR-0287).
	function formatDate(date: string | null): string {
		return date ? formatDateTime(date, $intlLocale) : $t('common.unknown');
	}

	function smbHint(user: User): string {
		if (user.smb?.state === 'password_needed') return $t('users.smbPasswordNeeded');
		if (user.smb?.state === 'unavailable') return user.smb.reason ?? '';
		return '';
	}

	function passwordError(password: string): string {
		return password && password.length < MIN_PASSWORD_LENGTH
			? $t('users.password.short', { n: MIN_PASSWORD_LENGTH })
			: '';
	}

	function confirmError(password: string, confirmation: string): string {
		return confirmation && confirmation !== password ? $t('users.password.mismatch') : '';
	}

	// ---- Create ----

	let createOpen = $state(false);
	let newUsername = $state('');
	let newPassword = $state('');
	let newConfirm = $state('');
	let newSmb = $state(false);
	let creating = $state(false);
	let createError = $state('');

	let newUsernameError = $derived(
		newUsername && !USERNAME_PATTERN.test(newUsername) ? $t('users.username.invalid') : ''
	);
	let canCreate = $derived(
		USERNAME_PATTERN.test(newUsername) &&
			newPassword.length >= MIN_PASSWORD_LENGTH &&
			newConfirm === newPassword &&
			!creating
	);

	function openCreate() {
		newUsername = '';
		newPassword = '';
		newConfirm = '';
		newSmb = false;
		createError = '';
		createOpen = true;
	}

	// The command palette's Add a user opens this dialog, which adds the user only once it is
	// submitted (ADR-0393). The effect depends on the request alone; before the first render too,
	// as before, so that a request made on another page opens the dialog at once.
	$effect.pre(() => {
		const request = $paletteRequest;
		untrack(() => openRequested(request));
	});

	function openRequested(request: PaletteRequest | null) {
		if (request?.dialog !== 'add-user') return;
		paletteRequest.set(null);
		if (!createOpen) openCreate();
	}

	function closeCreate() {
		if (creating) return;
		createOpen = false;
		newPassword = '';
		newConfirm = '';
	}

	async function handleCreate() {
		if (!canCreate) return;
		const username = newUsername;
		creating = true;
		createError = '';
		try {
			const created = await createUser({ username, password: newPassword, smb_enabled: newSmb });
			creating = false;
			closeCreate();
			// e.g. "User created, but SMB access could not be turned on: ..."
			showOutcome($t('users.new.done', { name: username }), created.warnings);
			await loadUsers(false);
		} catch (e) {
			createError = messageOf(e);
		} finally {
			creating = false;
		}
	}

	// ---- Edit ----

	// The user as the dialog was opened: every request carries this username,
	// so a change never reaches another user who got the same id meanwhile.
	let editing = $state.raw<User | null>(null);
	let editUsername = $state('');
	let editIsAdmin = $state(false);
	let editPassword = $state('');
	let editConfirm = $state('');
	let alsoTurnOnSmb = $state(true);
	let saving = $state(false);
	let smbBusy = $state(false);
	let editError = $state('');
	let editWarnings = $state.raw<string[]>([]);
	let confirmSmbOff = $state(false);
	// Whether the user has access to a share; null while unknown.
	let editHasShareAccess: boolean | null = $state(null);
	let shareAccessRequest = 0;

	let editState = $derived(editing?.smb?.state);
	// A new name (ADR-0368): the role changes in another save, and a user with SMB on needs a new
	// password, which Samba's account of the new name gets.
	let renaming = $derived(editing !== null && editUsername !== editing.username);
	let editUsernameError = $derived(
		renaming && !USERNAME_PATTERN.test(editUsername) ? $t('users.username.invalid') : ''
	);
	let renameWithRole = $derived(renaming && editing !== null && editIsAdmin !== editing.is_admin);
	let renameNeedsPassword = $derived(renaming && editState === 'active');
	let promoting = $derived(editing !== null && !editing.is_admin && editIsAdmin);
	let adminWarning = $derived(
		promoting &&
			(editState === 'active' || editState === 'password_needed' || editHasShareAccess !== false)
	);
	let turnOnWithPassword = $derived(
		editState === 'password_needed' &&
			!editIsAdmin &&
			alsoTurnOnSmb &&
			editPassword !== '' &&
			!renaming
	);
	let editPasswordReady = $derived(
		editPassword === '' ||
			(editPassword.length >= MIN_PASSWORD_LENGTH && editConfirm === editPassword)
	);
	let editChanged = $derived(
		editing !== null && (editIsAdmin !== editing.is_admin || editPassword !== '' || renaming)
	);
	let canSave = $derived(
		editChanged &&
			editPasswordReady &&
			!editUsernameError &&
			!renameWithRole &&
			!(renameNeedsPassword && editPassword === '') &&
			!saving &&
			!smbBusy
	);
	let smbToggleDisabled = $derived(saving || smbBusy || promoting);

	function openEdit(user: User) {
		editing = user;
		editUsername = user.username;
		editIsAdmin = user.is_admin;
		editPassword = '';
		editConfirm = '';
		alsoTurnOnSmb = true;
		editError = '';
		editWarnings = [];
		confirmSmbOff = false;
		editHasShareAccess = null;
		void loadShareAccess(user);
	}

	// Access of the user's own, or as a member of a group: a promotion removes both.
	async function loadShareAccess(user: User) {
		const request = ++shareAccessRequest;
		try {
			const [shares, groups] = await Promise.all([getSmbShares(), getGroups()]);
			const hasAccess =
				shares.some((share) => share.access.some((a) => a.user_id === user.id)) ||
				groups.some((group) => group.members.some((m) => m.user_id === user.id));
			if (request === shareAccessRequest) editHasShareAccess = hasAccess;
		} catch {
			// Unknown: the promotion warning is shown.
			if (request === shareAccessRequest) editHasShareAccess = null;
		}
	}

	function closeEdit(force = false) {
		if (!force && (saving || smbBusy)) return;
		editing = null;
		confirmSmbOff = false;
		editPassword = '';
		editConfirm = '';
	}

	async function handleEditError(e: unknown, target: User) {
		const message = messageOf(e);
		const now = await currentAfterRefusal(e, target);
		if (now === null) {
			// The id names someone else now: close the dialog, so that none of
			// its controls can act on them; the reloaded list shows who is who.
			closeEdit(true);
			actionError = { title: $t('users.edit.failed'), message };
			return;
		}
		// The same user: show their SMB state as stored now (e.g. SMB was not
		// turned on). The rest of the dialog keeps what it was opened with.
		if (editing?.id === now.id) editing = { ...editing, smb: now.smb };
		editError = message;
	}

	function smbStateText(user: User): string {
		switch (user.smb?.state) {
			case 'active':
				return $t('users.edit.smbOn', {
					name: user.username,
					login: user.smb.linux_name ?? user.username
				});
			case 'password_needed':
				return $t('users.smbPasswordNeeded');
			case 'off':
				return $t('users.edit.smbOff', { name: user.username });
			default:
				return '';
		}
	}

	async function saveEdit() {
		if (!editing || !canSave) return;
		const target = editing;
		const password = editPassword;
		const newName = renaming ? editUsername : null;
		saving = true;
		editError = '';
		try {
			let result: User;
			if (turnOnWithPassword) {
				// Sets the user's UNOS and SMB password and turns SMB on.
				result = await enableUserSmb(target.id, target.username, password);
			} else {
				const data: UserUpdate = { username: target.username };
				if (newName !== null) data.new_username = newName;
				if (editIsAdmin !== target.is_admin) data.is_admin = editIsAdmin;
				if (password) data.password = password;
				result = await updateUser(target.id, data);
			}
			saving = false;
			closeEdit(true);
			showOutcome(
				newName !== null
					? $t('users.edit.renamed', { old: target.username, name: newName })
					: $t('users.edit.done', { name: target.username }),
				[...editWarnings, ...(result.warnings ?? [])]
			);
			// After renaming yourself, your session has ended: this load's 401 signs you out.
			await loadUsers(false);
		} catch (e) {
			await handleEditError(e, target);
		} finally {
			saving = false;
		}
	}

	async function onSmbToggle(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		if (!editing) return;
		if (!input.checked) {
			// SMB stays on until the confirmation.
			input.checked = true;
			editError = '';
			confirmSmbOff = true;
			return;
		}
		const target = editing;
		smbBusy = true;
		editError = '';
		try {
			const updated = await enableUserSmb(target.id, target.username);
			editing = { ...target, smb: updated.smb ?? target.smb };
			editWarnings = updated.warnings ?? [];
			void loadUsers(false);
		} catch (e) {
			input.checked = false;
			await handleEditError(e, target);
		} finally {
			smbBusy = false;
		}
	}

	function cancelSmbOff() {
		if (!smbBusy) confirmSmbOff = false;
	}

	async function confirmTurnOffSmb() {
		if (!editing) return;
		const target = editing;
		smbBusy = true;
		editError = '';
		try {
			const updated = await disableUserSmb(target.id, target.username);
			editing = { ...target, smb: updated.smb ?? target.smb };
			editWarnings = updated.warnings ?? [];
			void loadUsers(false);
		} catch (e) {
			await handleEditError(e, target);
		} finally {
			smbBusy = false;
			confirmSmbOff = false;
		}
	}

	// ---- End sessions (ADR-0329) ----

	async function endUserSessions(target: User) {
		try {
			const result = await endSessions(target.id, target.username);
			showOutcome($t('users.endSessions.done', { name: target.username, n: result.ended }), []);
		} catch (e) {
			const message = messageOf(e);
			await currentAfterRefusal(e, target);
			actionError = { title: $t('users.endSessions.failed'), message };
		}
	}

	// ---- Delete ----

	let userToDelete = $state.raw<User | null>(null);
	let deleting = $state(false);
	let deleteError = $state('');

	function openDelete(user: User) {
		userToDelete = user;
		deleteError = '';
	}

	function closeDelete() {
		if (!deleting) userToDelete = null;
	}

	async function confirmDelete() {
		if (!userToDelete) return;
		const target = userToDelete;
		deleting = true;
		deleteError = '';
		try {
			const result = await deleteUser(target.id, target.username);
			deleting = false;
			userToDelete = null;
			showOutcome($t('users.delete.done', { name: target.username }), result?.warnings);
			await loadUsers(false);
		} catch (e) {
			const message = messageOf(e);
			if ((await currentAfterRefusal(e, target)) === null) {
				// From a list loaded before the id named someone else (or nobody).
				deleting = false;
				userToDelete = null;
				actionError = { title: $t('users.delete.failed'), message };
				return;
			}
			deleteError = message;
		} finally {
			deleting = false;
		}
	}
</script>

<!-- Only render content if user is admin -->
{#if $currentUser?.is_admin}
	<div class="page-container">
		<div class="page-header">
			<h1 class="page-title">{$t('users.title')}</h1>
			<Button icon={UserPlus} onclick={openCreate}>{$t('users.add')}</Button>
		</div>

		{#if error}
			<Alert variant="error">{error}</Alert>
		{/if}
		{#if notice}
			<Alert variant="success" dismissible ondismiss={() => (notice = '')}>{notice}</Alert>
		{/if}
		{#if warnings.length > 0}
			<Alert variant="warning" dismissible ondismiss={() => (warnings = [])}>
				{#each warnings as warning}
					<p>{warning}</p>
				{/each}
			</Alert>
		{/if}
		{#if actionError}
			<Alert
				variant="error"
				title={actionError.title}
				dismissible
				ondismiss={() => (actionError = null)}>{actionError.message}</Alert
			>
		{/if}

		{#if loading}
			<p class="loading-text">{$t('common.loading')}</p>
		{:else}
			<Card>
				<h2 class="section-title mb-4">
					<Users class="w-5 h-5" />
					{$t('users.count', { count: users.length })}
				</h2>

				{#if users.length === 0}
					<p class="text-muted">{$t('users.empty')}</p>
				{:else}
					<div class="table-frame">
						<table class="table">
							<thead class="table-header">
								<tr>
									<th>{$t('common.username')}</th>
									<th>{$t('users.role')}</th>
									<th>{$t('users.twoFactor')}</th>
									<th>{$t('users.smb')}</th>
									<th>{$t('users.created')}</th>
									<th class="text-right">{$t('users.actions')}</th>
								</tr>
							</thead>
							<tbody>
								{#each users as user (user.id)}
									<tr class="table-row">
										<td>
											<div class="flex items-center gap-2">
												<span class="font-medium">{user.username}</span>
												{#if $currentUser?.id === user.id}
													<Badge variant="info">{$t('users.you')}</Badge>
												{/if}
											</div>
										</td>
										<td>
											{#if user.is_admin}
												<span class="flex items-center gap-1 text-warning">
													<Shield class="w-4 h-4" />
													{$t('users.admin')}
												</span>
											{:else}
												<span class="flex items-center gap-1 text-secondary">
													<ShieldOff class="w-4 h-4" />
													{$t('users.user')}
												</span>
											{/if}
										</td>
										<td>
											{#if user.totp_enabled}
												<span class="text-success">{$t('users.twoFactorOn')}</span>
											{:else}
												<span class="text-muted">{$t('users.twoFactorOff')}</span>
											{/if}
										</td>
										<td>
											{#if user.smb}
												<SmbStateBadge state={user.smb.state} hint={smbHint(user)} />
											{:else}
												<span class="text-muted">-</span>
											{/if}
										</td>
										<td class="text-secondary">{formatDate(user.created_at)}</td>
										<td>
											<div class="flex items-center justify-end gap-2">
												<Button variant="secondary" size="icon-sm" onclick={() => openEdit(user)}>
													<Edit class="w-4 h-4" />
													<span class="sr-only">{$t('users.editUser', { name: user.username })}</span>
												</Button>
												{#if $currentUser?.id !== user.id}
													<Button variant="ghost" size="icon-sm" onclick={() => endUserSessions(user)}>
														<LogOut class="w-4 h-4" />
														<span class="sr-only">{$t('users.endSessionsOf', { name: user.username })}</span>
													</Button>
													<Button variant="ghost" size="icon-sm" onclick={() => openDelete(user)}>
														<Trash2 class="w-4 h-4 text-error" />
														<span class="sr-only">{$t('users.deleteUser', { name: user.username })}</span>
													</Button>
												{/if}
											</div>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</Card>

			<GroupsCard {users} />

			<Card>
				<h2 class="section-title mb-4">{$t('users.about.title')}</h2>
				<ul class="space-y-2 text-sm text-secondary list-disc ml-5">
					<li>{$t('users.about.admins')}</li>
					<li>{$t('users.about.users')}</li>
					<li>{$t('users.about.self')}</li>
					<li>{$t('users.about.passwords', { n: MIN_PASSWORD_LENGTH })}</li>
					<li>{$t('users.about.smb')}</li>
					<li>{$t('users.about.twoFactor')}</li>
				</ul>
			</Card>
		{/if}
	</div>

	<!-- Create user -->
	<Modal
		open={createOpen}
		title={$t('users.new.title')}
		size="md"
		closable={!creating}
		onclose={closeCreate}
	>
		<form
			onsubmit={(e) => {
				e.preventDefault();
				handleCreate();
			}}
			class="space-y-4"
		>
			<Input
				id="new-username"
				label={$t('common.username')}
				bind:value={newUsername}
				autocomplete="off"
				maxlength={32}
				hint={$t('users.username.hint')}
				error={newUsernameError}
			/>
			<Input
				id="new-password"
				type="password"
				label={$t('common.password')}
				bind:value={newPassword}
				autocomplete="new-password"
				error={passwordError(newPassword)}
			/>
			<Input
				id="new-password-confirm"
				type="password"
				label={$t('users.new.confirm')}
				bind:value={newConfirm}
				autocomplete="new-password"
				error={confirmError(newPassword, newConfirm)}
			/>
			<div>
				<label class="flex items-center gap-2 cursor-pointer">
					<input type="checkbox" class="form-checkbox" bind:checked={newSmb} />
					{$t('users.new.smb')}
				</label>
				<p class="form-hint">{$t('users.new.smbHint')}</p>
			</div>
			{#if createError}
				<Alert variant="error" title={$t('users.new.failed')}>{createError}</Alert>
			{/if}
		</form>

		{#snippet footer()}
			<Button variant="secondary" class="flex-1" disabled={creating} onclick={closeCreate}>
				{$t('common.cancel')}
			</Button>
			<Button class="flex-1" loading={creating} disabled={!canCreate} onclick={handleCreate}>
				{$t('users.new.submit')}
			</Button>
		{/snippet}
	</Modal>

	<!-- Edit user. -->
	<Modal
		open={editing !== null}
		title={editing
			? $t('users.edit.title', { name: editing.username })
			: $t('users.edit.titleWithoutName')}
		size="lg"
		closable={!saving && !smbBusy}
		onclose={() => closeEdit()}
	>
		{#if editing}
			<form
				onsubmit={(e) => {
					e.preventDefault();
					saveEdit();
				}}
				class="space-y-5 max-h-[60vh] overflow-y-auto scrollbar-thin pr-1"
			>
				<section class="space-y-2">
					<h3 class="card-title">{$t('users.edit.nameTitle')}</h3>
					<Input
						id="edit-username"
						label={$t('common.username')}
						bind:value={editUsername}
						autocomplete="off"
						maxlength={32}
						hint={renaming ? $t('users.edit.renameHint') : ''}
						error={editUsernameError}
						disabled={saving || smbBusy}
					/>
					{#if renaming && $currentUser?.id === editing.id}
						<Alert variant="info">{$t('users.edit.renameSelf')}</Alert>
					{/if}
				</section>

				<section class="space-y-2">
					<h3 class="card-title">{$t('users.role')}</h3>
					<label class="flex items-center gap-2 cursor-pointer">
						<input
							type="checkbox"
							class="form-checkbox"
							bind:checked={editIsAdmin}
							disabled={saving || smbBusy}
						/>
						{$t('users.edit.admin')}
					</label>
					{#if renameWithRole}
						<Alert variant="warning">{$t('users.edit.renameAndRole')}</Alert>
					{:else if adminWarning}
						<Alert variant="warning">
							{$t('users.edit.adminWarning', { name: editing.username })}
						</Alert>
					{/if}
				</section>

				<section class="space-y-2">
					<h3 class="card-title">{$t('common.password')}</h3>
					<Input
						id="edit-password"
						type="password"
						label={$t('users.edit.password')}
						bind:value={editPassword}
						autocomplete="new-password"
						hint={$t('users.edit.passwordHint')}
						error={passwordError(editPassword)}
						disabled={saving}
					/>
					<Input
						id="edit-password-confirm"
						type="password"
						label={$t('users.edit.confirm')}
						bind:value={editConfirm}
						autocomplete="new-password"
						error={confirmError(editPassword, editConfirm)}
						disabled={saving}
					/>
					{#if renameNeedsPassword}
						<p class="form-hint">{$t('users.edit.renameSmb', { name: editing.username })}</p>
					{:else if editState === 'active'}
						<p class="form-hint">{$t('users.edit.smbPassword')}</p>
					{:else if editState === 'password_needed' && !editIsAdmin && !renaming}
						<label class="flex items-center gap-2 cursor-pointer">
							<input
								type="checkbox"
								class="form-checkbox"
								bind:checked={alsoTurnOnSmb}
								disabled={saving}
							/>
							{$t('users.edit.turnOnSmb')}
						</label>
					{/if}
				</section>

				<section class="space-y-2">
					<h3 class="card-title">{$t('users.edit.smbTitle')}</h3>
					{#if !editing.smb}
						<p class="text-sm text-muted">{$t('users.edit.smbUnknown')}</p>
					{:else if editing.smb.state === 'unavailable'}
						<p class="text-sm text-secondary">{editing.smb.reason ?? ''}</p>
					{:else}
						<div class="flex flex-wrap items-center gap-2 text-sm">
							<SmbStateBadge state={editing.smb.state} />
							<span class="text-secondary">{smbStateText(editing)}</span>
						</div>
						<label
							class="flex items-center gap-2 {smbToggleDisabled ? 'opacity-60' : 'cursor-pointer'}"
						>
							<input
								type="checkbox"
								class="form-checkbox"
								checked={editing.smb.allowed}
								disabled={smbToggleDisabled}
								onchange={onSmbToggle}
							/>
							{$t('users.edit.allowSmb')}
						</label>
						{#if smbBusy}
							<p class="text-sm text-muted">{$t('users.edit.saving')}</p>
						{/if}
					{/if}
				</section>
			</form>

			<!-- Below the scrolling form, so that they are seen. -->
			{#if editWarnings.length > 0}
				<Alert variant="warning">
					{#each editWarnings as warning}
						<p>{warning}</p>
					{/each}
				</Alert>
			{/if}
			{#if editError}
				<Alert variant="error" title={$t('users.edit.failed')}>{editError}</Alert>
			{/if}
		{/if}

		{#snippet footer()}
			<Button
				variant="secondary"
				class="flex-1"
				disabled={saving || smbBusy}
				onclick={() => closeEdit()}
			>
				{$t('users.edit.close')}
			</Button>
			<Button class="flex-1" loading={saving} disabled={!canSave} onclick={saveEdit}>
				{$t('users.edit.save')}
			</Button>
		{/snippet}
	</Modal>

	<!-- Turn off SMB, asked from the edit dialog, which stays open and inert under it: Escape
	     closes only the top dialog (Modal, ADR-0361). -->
	<Modal
		open={confirmSmbOff}
		title={$t('users.smbOff.title')}
		size="md"
		closable={!smbBusy}
		onclose={cancelSmbOff}
	>
		{#if editing}
			<!-- In parts around the name: ADR-0296. -->
			<p>
				{$t('users.smbOff.before')}
				<strong class="text-content">{editing.username}</strong>{$t('users.smbOff.after')}
			</p>
		{/if}

		{#snippet footer()}
			<Button variant="secondary" class="flex-1" disabled={smbBusy} onclick={cancelSmbOff}>
				{$t('common.cancel')}
			</Button>
			<Button variant="danger" class="flex-1" loading={smbBusy} onclick={confirmTurnOffSmb}>
				{$t('users.smbOff.submit')}
			</Button>
		{/snippet}
	</Modal>

	<Modal
		open={userToDelete !== null}
		title={$t('users.delete.title')}
		size="md"
		danger
		closable={!deleting}
		onclose={closeDelete}
	>
		{#if userToDelete}
			<!-- In parts around the name: ADR-0296. -->
			<p>
				{$t('users.delete.before')}
				<strong class="text-content">{userToDelete.username}</strong>{$t('users.delete.after')}
			</p>
			{#if deleteError}
				<Alert variant="error" title={$t('users.delete.failed')}>{deleteError}</Alert>
			{/if}
		{/if}

		{#snippet footer()}
			<Button variant="secondary" class="flex-1" disabled={deleting} onclick={closeDelete}>
				{$t('common.cancel')}
			</Button>
			<Button variant="danger" class="flex-1" loading={deleting} onclick={confirmDelete}>
				{$t('users.delete.submit')}
			</Button>
		{/snippet}
	</Modal>
{/if}
