<script lang="ts">
	import { untrack } from 'svelte';
	import { createUser, USERNAME_PATTERN } from '$lib/api/auth';
	import { paletteRequest, type PaletteRequest } from '$lib/stores/palette';
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { UserDirectory } from './directory.svelte';
	import { messageOf } from './errors';
	import { passwordComplete } from './password';
	import PasswordFields from './PasswordFields.svelte';

	type Props = {
		// The page's users: the new one joins them, and the outcome is shown above them.
		directory: UserDirectory;
	};

	let { directory }: Props = $props();

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
		USERNAME_PATTERN.test(newUsername) && passwordComplete(newPassword, newConfirm) && !creating
	);

	/** Opens the dialog with an empty form. */
	export function open() {
		newUsername = '';
		newPassword = '';
		newConfirm = '';
		newSmb = false;
		createError = '';
		createOpen = true;
	}

	// The command palette's Add a user opens this dialog, which adds the user only once it is
	// submitted (ADR-0393). The effect depends on the request alone; before the first render too,
	// so that a request made on another page opens the dialog at once.
	$effect.pre(() => {
		const request = $paletteRequest;
		untrack(() => openRequested(request));
	});

	function openRequested(request: PaletteRequest | null) {
		if (request?.dialog !== 'add-user') return;
		paletteRequest.set(null);
		if (!createOpen) open();
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
			directory.outcome.show($t('users.new.done', { name: username }), created.warnings);
			await directory.load(false);
		} catch (e) {
			createError = messageOf(e);
		} finally {
			creating = false;
		}
	}
</script>

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
		<PasswordFields
			id="new-password"
			confirmId="new-password-confirm"
			label={$t('common.password')}
			confirmLabel={$t('users.new.confirm')}
			bind:password={newPassword}
			bind:confirmation={newConfirm}
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
