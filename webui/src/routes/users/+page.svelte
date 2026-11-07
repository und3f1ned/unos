<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import {
		createUser,
		deleteUser,
		disableUserSmb,
		enableUserSmb,
		getUsers,
		updateUser,
		USERNAME_PATTERN,
		type User,
		type UserUpdate
	} from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import { getSmbShares } from '$lib/api/shares';
	import { user as currentUser } from '$lib/stores/auth';
	import { Edit, Shield, ShieldOff, Trash2, UserPlus, Users } from 'lucide-svelte';
	import { Alert, Badge, Button, Card, Input, Modal } from '$lib/components/ui';
	import { SmbStateBadge } from '$lib/components/shares';

	const USERNAME_HINT = '1–32 lowercase letters, digits, _ or -, starting with a letter';
	const MIN_PASSWORD_LENGTH = 8;
	const PASSWORD_NEEDED_HINT =
		'The user turns SMB on in their Account page with their password, or set a new password here';

	let users: User[] = [];
	let loading = true;
	let error = '';
	// Outcome of the last change: shown until dismissed.
	let notice = '';
	let warnings: string[] = [];
	// A change refused after its dialog closed (the user's id names someone else now).
	let actionError: { title: string; message: string } | null = null;

	onMount(async () => {
		// Redirect non-admin users
		if (!$currentUser?.is_admin) {
			goto('/');
			return;
		}
		await loadUsers();
	});

	// Also watch for user changes (in case they navigate here before user loads)
	$: if ($currentUser !== null && !$currentUser.is_admin) {
		goto('/');
	}

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function loadUsers(showSpinner = true) {
		if (showSpinner) loading = true;
		try {
			users = await getUsers();
			error = '';
		} catch (e) {
			error = messageOf(e) || 'Failed to load users';
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

	function formatDate(date: string | null): string {
		if (!date) return 'Unknown';
		return new Date(date).toLocaleString();
	}

	function smbHint(user: User): string {
		if (user.smb?.state === 'password_needed') return PASSWORD_NEEDED_HINT;
		if (user.smb?.state === 'unavailable') return user.smb.reason ?? '';
		return '';
	}

	function passwordError(password: string): string {
		return password && password.length < MIN_PASSWORD_LENGTH
			? `Use at least ${MIN_PASSWORD_LENGTH} characters`
			: '';
	}

	function confirmError(password: string, confirmation: string): string {
		return confirmation && confirmation !== password ? 'Passwords do not match' : '';
	}

	// ---- Create ----

	let createOpen = false;
	let newUsername = '';
	let newPassword = '';
	let newConfirm = '';
	let newSmb = false;
	let creating = false;
	let createError = '';

	$: newUsernameError =
		newUsername && !USERNAME_PATTERN.test(newUsername) ? `Use ${USERNAME_HINT}` : '';
	$: canCreate =
		USERNAME_PATTERN.test(newUsername) &&
		newPassword.length >= MIN_PASSWORD_LENGTH &&
		newConfirm === newPassword &&
		!creating;

	function openCreate() {
		newUsername = '';
		newPassword = '';
		newConfirm = '';
		newSmb = false;
		createError = '';
		createOpen = true;
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
			showOutcome(`User "${username}" created`, created.warnings);
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
	let editing: User | null = null;
	let editIsAdmin = false;
	let editPassword = '';
	let editConfirm = '';
	let alsoTurnOnSmb = true;
	let saving = false;
	let smbBusy = false;
	let editError = '';
	let editWarnings: string[] = [];
	let confirmSmbOff = false;
	// Whether the user has access to a share; null while unknown.
	let editHasShareAccess: boolean | null = null;
	let shareAccessRequest = 0;

	$: editState = editing?.smb?.state;
	$: promoting = editing !== null && !editing.is_admin && editIsAdmin;
	$: adminWarning =
		promoting &&
		(editState === 'active' || editState === 'password_needed' || editHasShareAccess !== false);
	$: turnOnWithPassword =
		editState === 'password_needed' && !editIsAdmin && alsoTurnOnSmb && editPassword !== '';
	$: editPasswordReady =
		editPassword === '' ||
		(editPassword.length >= MIN_PASSWORD_LENGTH && editConfirm === editPassword);
	$: editChanged = editing !== null && (editIsAdmin !== editing.is_admin || editPassword !== '');
	$: canSave = editChanged && editPasswordReady && !saving && !smbBusy;
	$: smbToggleDisabled = saving || smbBusy || promoting;

	function openEdit(user: User) {
		editing = user;
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

	async function loadShareAccess(user: User) {
		const request = ++shareAccessRequest;
		try {
			const shares = await getSmbShares();
			const hasAccess = shares.some((share) => share.access.some((a) => a.user_id === user.id));
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
			actionError = { title: 'The change was not made', message };
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
				return `SMB is on: ${user.username} signs in as ${user.smb.linux_name ?? user.username} with their UNOS password.`;
			case 'password_needed':
				return PASSWORD_NEEDED_HINT;
			case 'off':
				return `SMB is off for ${user.username}.`;
			default:
				return '';
		}
	}

	async function saveEdit() {
		if (!editing || !canSave) return;
		const target = editing;
		const password = editPassword;
		saving = true;
		editError = '';
		try {
			let result: User;
			if (turnOnWithPassword) {
				// Sets the user's UNOS and SMB password and turns SMB on.
				result = await enableUserSmb(target.id, target.username, password);
			} else {
				const data: UserUpdate = { username: target.username };
				if (editIsAdmin !== target.is_admin) data.is_admin = editIsAdmin;
				if (password) data.password = password;
				result = await updateUser(target.id, data);
			}
			saving = false;
			closeEdit(true);
			showOutcome(`User "${target.username}" updated`, [
				...editWarnings,
				...(result.warnings ?? [])
			]);
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

	// ---- Delete ----

	let userToDelete: User | null = null;
	let deleting = false;
	let deleteError = '';

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
			showOutcome(`User "${target.username}" deleted`, result?.warnings);
			await loadUsers(false);
		} catch (e) {
			const message = messageOf(e);
			if ((await currentAfterRefusal(e, target)) === null) {
				// From a list loaded before the id named someone else (or nobody).
				deleting = false;
				userToDelete = null;
				actionError = { title: 'User was not deleted', message };
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
			<h1 class="page-title">Users</h1>
			<Button icon={UserPlus} on:click={openCreate}>Add user</Button>
		</div>

		{#if error}
			<Alert variant="error">{error}</Alert>
		{/if}
		{#if notice}
			<Alert variant="success" dismissible on:dismiss={() => (notice = '')}>{notice}</Alert>
		{/if}
		{#if warnings.length > 0}
			<Alert variant="warning" dismissible on:dismiss={() => (warnings = [])}>
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
				on:dismiss={() => (actionError = null)}>{actionError.message}</Alert
			>
		{/if}

		{#if loading}
			<p class="loading-text">Loading...</p>
		{:else}
			<Card>
				<h2 class="section-title mb-4">
					<Users class="w-5 h-5" />
					Users ({users.length})
				</h2>

				{#if users.length === 0}
					<p class="text-muted">No users found</p>
				{:else}
					<div class="overflow-x-auto">
						<table class="table">
							<thead class="table-header">
								<tr>
									<th>Username</th>
									<th>Role</th>
									<th>2FA</th>
									<th>SMB</th>
									<th>Created</th>
									<th class="text-right">Actions</th>
								</tr>
							</thead>
							<tbody>
								{#each users as user (user.id)}
									<tr class="table-row">
										<td>
											<div class="flex items-center gap-2">
												<span class="font-medium">{user.username}</span>
												{#if $currentUser?.id === user.id}
													<Badge variant="info">You</Badge>
												{/if}
											</div>
										</td>
										<td>
											{#if user.is_admin}
												<span class="flex items-center gap-1 text-warning">
													<Shield class="w-4 h-4" />
													Admin
												</span>
											{:else}
												<span class="flex items-center gap-1 text-secondary">
													<ShieldOff class="w-4 h-4" />
													User
												</span>
											{/if}
										</td>
										<td>
											{#if user.totp_enabled}
												<span class="text-success">Enabled</span>
											{:else}
												<span class="text-muted">Disabled</span>
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
												<Button variant="secondary" size="icon-sm" on:click={() => openEdit(user)}>
													<Edit class="w-4 h-4" />
													<span class="sr-only">Edit user {user.username}</span>
												</Button>
												{#if $currentUser?.id !== user.id}
													<Button variant="ghost" size="icon-sm" on:click={() => openDelete(user)}>
														<Trash2 class="w-4 h-4 text-error" />
														<span class="sr-only">Delete user {user.username}</span>
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

			<Card>
				<h2 class="section-title mb-4">About user management</h2>
				<ul class="space-y-2 text-sm text-secondary list-disc ml-5">
					<li>Admin users have full access to all UNOS features including user management.</li>
					<li>Regular users can access basic features but cannot manage other users.</li>
					<li>You cannot delete your own account.</li>
					<li>Passwords must be at least {MIN_PASSWORD_LENGTH} characters long.</li>
					<li>
						SMB (network file) access is off by default. Allow it per user; administrators cannot
						use SMB.
					</li>
					<li>2FA protects only the web interface.</li>
				</ul>
			</Card>
		{/if}
	</div>

	<!-- Create user -->
	<Modal open={createOpen} title="Add user" size="md" closable={!creating} on:close={closeCreate}>
		<form on:submit|preventDefault={handleCreate} class="space-y-4">
			<Input
				id="new-username"
				label="Username"
				bind:value={newUsername}
				autocomplete="off"
				maxlength={32}
				hint={USERNAME_HINT}
				error={newUsernameError}
			/>
			<Input
				id="new-password"
				type="password"
				label="Password"
				bind:value={newPassword}
				autocomplete="new-password"
				error={passwordError(newPassword)}
			/>
			<Input
				id="new-password-confirm"
				type="password"
				label="Confirm password"
				bind:value={newConfirm}
				autocomplete="new-password"
				error={confirmError(newPassword, newConfirm)}
			/>
			<div>
				<label class="flex items-center gap-2 cursor-pointer">
					<input type="checkbox" class="form-checkbox" bind:checked={newSmb} />
					Allow SMB (network file) access
				</label>
				<p class="form-hint">Uses the same password. SMB has no two-factor authentication.</p>
			</div>
			{#if createError}
				<Alert variant="error" title="User was not created">{createError}</Alert>
			{/if}
		</form>

		<svelte:fragment slot="footer">
			<Button variant="secondary" class="flex-1" disabled={creating} on:click={closeCreate}>
				Cancel
			</Button>
			<Button class="flex-1" loading={creating} disabled={!canCreate} on:click={handleCreate}>
				Create user
			</Button>
		</svelte:fragment>
	</Modal>

	<!-- Turn off SMB, asked from the edit dialog below. Declared before it on purpose: its Escape
	     shows the edit dialog again before that dialog's key handler runs, and the edit dialog
	     must stay open (Modal; users.spec 'Escape in the Turn off SMB confirmation ...'). -->
	<Modal open={confirmSmbOff} title="Turn off SMB" size="md" closable={!smbBusy} on:close={cancelSmbOff}>
		{#if editing}
			<p>
				Turn off SMB for <strong class="text-content">{editing.username}</strong>? Their open SMB
				connections are ended.
			</p>
		{/if}

		<svelte:fragment slot="footer">
			<Button variant="secondary" class="flex-1" disabled={smbBusy} on:click={cancelSmbOff}>
				Cancel
			</Button>
			<Button variant="danger" class="flex-1" loading={smbBusy} on:click={confirmTurnOffSmb}>
				Turn off SMB
			</Button>
		</svelte:fragment>
	</Modal>

	<!-- Edit user; hidden while its SMB confirmation is shown (one dialog at a time). -->
	<Modal
		open={editing !== null && !confirmSmbOff}
		title={editing ? `Edit user ${editing.username}` : 'Edit user'}
		size="lg"
		closable={!saving && !smbBusy}
		on:close={() => closeEdit()}
	>
		{#if editing}
			<form
				on:submit|preventDefault={saveEdit}
				class="space-y-5 max-h-[60vh] overflow-y-auto scrollbar-thin pr-1"
			>
				<section class="space-y-2">
					<h3 class="card-title">Role</h3>
					<label class="flex items-center gap-2 cursor-pointer">
						<input
							type="checkbox"
							class="form-checkbox"
							bind:checked={editIsAdmin}
							disabled={saving || smbBusy}
						/>
						Administrator
					</label>
					{#if adminWarning}
						<Alert variant="warning">
							Administrators cannot use SMB: making {editing.username} an administrator removes SMB
							access and their share permissions, and ends their open SMB connections
						</Alert>
					{/if}
				</section>

				<section class="space-y-2">
					<h3 class="card-title">Password</h3>
					<Input
						id="edit-password"
						type="password"
						label="New password"
						bind:value={editPassword}
						autocomplete="new-password"
						hint="Leave empty to keep the current password"
						error={passwordError(editPassword)}
						disabled={saving}
					/>
					<Input
						id="edit-password-confirm"
						type="password"
						label="Confirm new password"
						bind:value={editConfirm}
						autocomplete="new-password"
						error={confirmError(editPassword, editConfirm)}
						disabled={saving}
					/>
					{#if editState === 'active'}
						<p class="form-hint">
							Also changes the SMB password and ends the user's open SMB connections
						</p>
					{:else if editState === 'password_needed' && !editIsAdmin}
						<label class="flex items-center gap-2 cursor-pointer">
							<input
								type="checkbox"
								class="form-checkbox"
								bind:checked={alsoTurnOnSmb}
								disabled={saving}
							/>
							Also turn SMB on with this password
						</label>
					{/if}
				</section>

				<section class="space-y-2">
					<h3 class="card-title">SMB access</h3>
					{#if !editing.smb}
						<p class="text-sm text-muted">The SMB state of this user is not known.</p>
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
								on:change={onSmbToggle}
							/>
							Allow SMB access
						</label>
						{#if smbBusy}
							<p class="text-sm text-muted">Saving...</p>
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
				<Alert variant="error" title="The change was not made">{editError}</Alert>
			{/if}
		{/if}

		<svelte:fragment slot="footer">
			<Button
				variant="secondary"
				class="flex-1"
				disabled={saving || smbBusy}
				on:click={() => closeEdit()}
			>
				Close
			</Button>
			<Button class="flex-1" loading={saving} disabled={!canSave} on:click={saveEdit}>Save</Button>
		</svelte:fragment>
	</Modal>

	<Modal
		open={userToDelete !== null}
		title="Delete user"
		size="md"
		danger
		closable={!deleting}
		on:close={closeDelete}
	>
		{#if userToDelete}
			<p>
				Are you sure you want to delete user <strong class="text-content"
					>{userToDelete.username}</strong
				>? This action cannot be undone.
			</p>
			{#if deleteError}
				<Alert variant="error" title="User was not deleted">{deleteError}</Alert>
			{/if}
		{/if}

		<svelte:fragment slot="footer">
			<Button variant="secondary" class="flex-1" disabled={deleting} on:click={closeDelete}>
				Cancel
			</Button>
			<Button variant="danger" class="flex-1" loading={deleting} on:click={confirmDelete}>
				Delete user
			</Button>
		</svelte:fragment>
	</Modal>
{/if}
