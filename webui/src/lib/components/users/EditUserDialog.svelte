<script lang="ts">
	import {
		disableUserSmb,
		enableUserSmb,
		updateUser,
		USERNAME_PATTERN,
		type User,
		type UserUpdate
	} from '$lib/api/auth';
	import { getGroups } from '$lib/api/groups';
	import { getSmbShares } from '$lib/api/shares';
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import { SmbStateBadge } from '$lib/components/shares';
	import { t } from '$lib/i18n';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import type { UserDirectory } from './directory.svelte';
	import { messageOf } from './errors';
	import { passwordComplete } from './password';
	import PasswordFields from './PasswordFields.svelte';

	type Props = {
		// The page's users: a change reloads them, and its outcome is shown above them.
		directory: UserDirectory;
		// The signed-in user, who is told that renaming themselves ends their session.
		currentUserId: number | undefined;
	};

	let { directory, currentUserId }: Props = $props();

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
		editPassword === '' || passwordComplete(editPassword, editConfirm)
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

	/** Opens the dialog for `user`, as the list shows them now. */
	export function open(user: User) {
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
		const now = await directory.currentAfterRefusal(e, target);
		if (now === null) {
			// The id names someone else now: close the dialog, so that none of
			// its controls can act on them; the reloaded list shows who is who.
			closeEdit(true);
			directory.outcome.fail(message, $t('users.edit.failed'));
			return;
		}
		// The same user: show their SMB state as stored now (e.g. SMB was not
		// turned on). The rest of the dialog keeps what it was opened with.
		if (editing?.id === now.id) editing = { ...editing, smb: now.smb };
		editError = message;
	}

	// The text follows a change of the language: the markup reads $t through it.
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
			directory.outcome.show(
				newName !== null
					? $t('users.edit.renamed', { old: target.username, name: newName })
					: $t('users.edit.done', { name: target.username }),
				[...editWarnings, ...(result.warnings ?? [])]
			);
			// After renaming yourself, your session has ended: this load's 401 signs you out.
			await directory.load(false);
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
			void directory.load(false);
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
			void directory.load(false);
		} catch (e) {
			await handleEditError(e, target);
		} finally {
			smbBusy = false;
			confirmSmbOff = false;
		}
	}
</script>

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
				{#if renaming && currentUserId === editing.id}
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

			<!-- The own password has one place, the Account page, which asks for the current one
			     (ADR-0430). -->
			{#if currentUserId === editing.id}
			<section class="space-y-2">
				<h3 class="card-title">{$t('common.password')}</h3>
				<p class="form-hint">
					{$t('users.edit.ownPassword')}
					<a href="/account#security" class="link">{$t('account.tabs.security')}</a>
				</p>
			</section>
			{:else}
			<section class="space-y-2">
				<h3 class="card-title">{$t('common.password')}</h3>
				<PasswordFields
					id="edit-password"
					confirmId="edit-password-confirm"
					label={$t('users.edit.password')}
					confirmLabel={$t('users.edit.confirm')}
					hint={$t('users.edit.passwordHint')}
					disabled={saving}
					bind:password={editPassword}
					bind:confirmation={editConfirm}
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
			{/if}

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
<ConfirmDialog
	open={confirmSmbOff}
	title={$t('users.smbOff.title')}
	busy={smbBusy}
	before={$t('users.smbOff.before')}
	name={editing?.username ?? null}
	after={$t('users.smbOff.after')}
	submit={$t('users.smbOff.submit')}
	onclose={cancelSmbOff}
	onconfirm={confirmTurnOffSmb}
/>
