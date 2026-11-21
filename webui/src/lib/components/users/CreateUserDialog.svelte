<script lang="ts">
	import { untrack } from 'svelte';
	import { createUser, USERNAME_PATTERN } from '$lib/api/auth';
	import { paletteRequest, type PaletteRequest } from '$lib/stores/palette';
	import { Alert, Button, Checkbox, HelpText, Input, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { UserDirectory } from './directory.svelte';
	import { messageOf } from './errors';
	import { MIN_PASSWORD_LENGTH, passwordComplete } from './password';
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
	// Create was pressed: the fields left empty say so. The button is never disabled
	// for what the form lacks: it names what is missing instead.
	let attempted = $state(false);

	let newUsernameError = $derived(
		newUsername
			? USERNAME_PATTERN.test(newUsername)
				? ''
				: $t('users.username.invalid')
			: attempted
				? $t('users.username.required')
				: ''
	);
	let canCreate = $derived(USERNAME_PATTERN.test(newUsername) && passwordComplete(newPassword, newConfirm));

	/** Opens the dialog with an empty form. */
	export function open() {
		newUsername = '';
		newPassword = '';
		newConfirm = '';
		newSmb = false;
		createError = '';
		attempted = false;
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
		if (creating) return;
		attempted = true;
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
<Modal open={createOpen} title={$t('users.new.title')} size="md" closable={!creating} onclose={closeCreate}>
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
			hint={$t('users.password.rule', { n: MIN_PASSWORD_LENGTH })}
			{attempted}
			bind:password={newPassword}
			bind:confirmation={newConfirm}
		/>
		<div>
			<Checkbox bind:checked={newSmb} label={$t('users.new.smb')} describedby="new-smb-hint" />
			<div id="new-smb-hint" class="ml-7">
				<HelpText text={$t('users.new.smbHint')} />
			</div>
		</div>
		<HelpText text={$t('users.new.roleHint')} />
		{#if createError}
			<Alert variant="error" title={$t('users.new.failed')}>{createError}</Alert>
		{/if}
	</form>

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={creating} onclick={closeCreate}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={creating} onclick={handleCreate}>
			{$t('users.new.submit')}
		</Button>
	{/snippet}
</Modal>
