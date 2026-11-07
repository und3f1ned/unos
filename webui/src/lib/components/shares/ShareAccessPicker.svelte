<script lang="ts">
	import type { User } from '$lib/api/auth';
	import { Button, Select } from '$lib/components/ui';
	import SmbStateBadge from './SmbStateBadge.svelte';
	import { accessKey, canBeGivenAccess, pickableUsers, type AccessChoice } from './access';
	import { ADMINS_NOT_LISTED } from './texts';

	/** Every UNOS user (GET /auth/users); administrators are not listed. */
	export let users: User[] = [];
	/** The choice per user, by accessKey(). Missing: no access. */
	export let levels: Record<string, AccessChoice> = {};
	/**
	 * Access can only be removed, never given (a share that is not served):
	 * "No access", "Clear" and each user's saved level work, so that a removal
	 * can be undone; the other levels and the bulk grants do not.
	 */
	export let reduceOnly = false;
	/** Shown while access can only be removed. */
	export let reduceOnlyHint = '';
	/** The levels the share has saved, by accessKey(). Missing: no access. */
	export let savedLevels: Record<string, AccessChoice> = {};

	type Option = { value: AccessChoice; label: string; disabled?: boolean };

	const options: Option[] = [
		{ value: 'none', label: 'No access' },
		{ value: 'read', label: 'Read only' },
		{ value: 'write', label: 'Read & write' }
	];

	function reducedTo(saved: AccessChoice): Option[] {
		return options.map((option) => ({
			...option,
			disabled: option.value !== 'none' && option.value !== saved
		}));
	}

	// While access can only be removed: "No access" and the saved level, by saved level.
	const reducedOptions: Record<AccessChoice, Option[]> = {
		none: reducedTo('none'),
		read: reducedTo('read'),
		write: reducedTo('write')
	};

	$: rows = pickableUsers(users);
	$: selectable = rows.filter(canBeGivenAccess);
	$: someAccess = selectable.some(
		(user) => (levels[accessKey(user.id, user.username)] ?? 'none') !== 'none'
	);

	function choice(value: unknown): AccessChoice {
		return value === 'read' || value === 'write' ? value : 'none';
	}

	function setLevel(user: User, value: unknown) {
		levels = { ...levels, [accessKey(user.id, user.username)]: choice(value) };
	}

	// Bulk changes only fill the picker; nothing is sent before the form is saved.
	function setAll(level: AccessChoice) {
		const next = { ...levels };
		for (const user of selectable) next[accessKey(user.id, user.username)] = level;
		levels = next;
	}

	function badgeHint(user: User): string {
		switch (user.smb?.state) {
			case 'password_needed':
				return 'SMB is allowed, but the user has not turned it on yet: the access counts once SMB is on';
			case 'off':
				return 'SMB is off for this user: the access counts once SMB is allowed on the Users page and turned on';
			default:
				// 'unavailable': the reason is shown as text under the user's level.
				return '';
		}
	}
</script>

<fieldset class="space-y-3 min-w-0">
	<legend class="form-label">Access</legend>

	{#if reduceOnly && reduceOnlyHint}
		<p class="form-hint mt-0">{reduceOnlyHint}</p>
	{/if}

	{#if rows.length === 0}
		<p class="text-sm text-muted">No users can be given access yet: add users on the Users page.</p>
	{:else}
		<div class="flex flex-wrap gap-2">
			<Button
				variant="secondary"
				size="sm"
				disabled={reduceOnly || selectable.length === 0}
				on:click={() => setAll('read')}
			>
				Everyone: Read only
			</Button>
			<Button
				variant="secondary"
				size="sm"
				disabled={reduceOnly || selectable.length === 0}
				on:click={() => setAll('write')}
			>
				Everyone: Read &amp; write
			</Button>
			<Button
				variant="ghost"
				size="sm"
				disabled={selectable.length === 0 || (reduceOnly && !someAccess)}
				on:click={() => setAll('none')}
			>
				Clear
			</Button>
		</div>

		<ul class="space-y-3">
			{#each rows as user (user.id)}
				{@const key = accessKey(user.id, user.username)}
				{@const unavailable = !canBeGivenAccess(user)}
				{@const level = unavailable ? 'none' : levels[key] ?? 'none'}
				{@const saved = choice(savedLevels[key])}
				<li class="flex items-start gap-3">
					<!-- While access can only be removed, a user with no access, now or saved, has nothing to change. -->
					<Select
						id="share-access-{user.id}"
						class="flex-1 min-w-0"
						label={user.username}
						options={reduceOnly ? reducedOptions[saved] : options}
						value={level}
						disabled={unavailable || (reduceOnly && level === 'none' && saved === 'none')}
						hint={unavailable ? user.smb?.reason ?? '' : ''}
						on:change={(event) => setLevel(user, event.detail?.value)}
					/>
					{#if user.smb && user.smb.state !== 'active'}
						<div class="pt-0.5 flex-shrink-0">
							<SmbStateBadge state={user.smb.state} hint={badgeHint(user)} />
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	<p class="form-hint">{ADMINS_NOT_LISTED}</p>
</fieldset>
