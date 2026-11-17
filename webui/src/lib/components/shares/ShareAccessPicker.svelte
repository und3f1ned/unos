<script lang="ts">
	import type { User } from '$lib/api/auth';
	import type { Group } from '$lib/api/groups';
	import { Button, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import SmbStateBadge from './SmbStateBadge.svelte';
	import { accessKey, canBeGivenAccess, groupKey, pickableUsers, type AccessChoice } from './access';

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
	/** Every group (GET /groups), sorted by name. */
	export let groups: Group[] = [];
	/** The choice per group, by groupKey(). Missing: no access. */
	export let groupLevels: Record<string, AccessChoice> = {};
	/** The levels the share has saved for groups, by groupKey(). */
	export let savedGroupLevels: Record<string, AccessChoice> = {};

	type Option = { value: AccessChoice; label: string; disabled?: boolean };

	// Reactive: the labels follow the language of the page.
	let options: Option[];
	$: options = [
		{ value: 'none', label: $t('shares.level.none') },
		{ value: 'read', label: $t('shares.level.read') },
		{ value: 'write', label: $t('shares.level.write') }
	];

	function reducedTo(all: Option[], saved: AccessChoice): Option[] {
		return all.map((option) => ({
			...option,
			disabled: option.value !== 'none' && option.value !== saved
		}));
	}

	// While access can only be removed: "No access" and the saved level, by saved level.
	let reducedOptions: Record<AccessChoice, Option[]>;
	$: reducedOptions = {
		none: reducedTo(options, 'none'),
		read: reducedTo(options, 'read'),
		write: reducedTo(options, 'write')
	};

	$: rows = pickableUsers(users);
	$: selectable = rows.filter(canBeGivenAccess);
	$: someAccess =
		selectable.some((user) => (levels[accessKey(user.id, user.username)] ?? 'none') !== 'none') ||
		groups.some((group) => (groupLevels[groupKey(group.id, group.name)] ?? 'none') !== 'none');

	function choice(value: unknown): AccessChoice {
		return value === 'read' || value === 'write' ? value : 'none';
	}

	function setLevel(user: User, value: unknown) {
		levels = { ...levels, [accessKey(user.id, user.username)]: choice(value) };
	}

	function setGroupLevel(group: Group, value: unknown) {
		groupLevels = { ...groupLevels, [groupKey(group.id, group.name)]: choice(value) };
	}

	// Bulk changes only fill the picker; nothing is sent before the form is saved. "Everyone" sets
	// the level of every user (ADR-0076); Clear also takes away the access of every group.
	function setAll(level: AccessChoice) {
		const next = { ...levels };
		for (const user of selectable) next[accessKey(user.id, user.username)] = level;
		levels = next;
		if (level === 'none') groupLevels = {};
	}

	function membersText(group: Group): string {
		if (group.members.length === 0) return $t('shares.access.groupEmpty');
		return $t('shares.access.groupMembers', {
			names: group.members.map((member) => member.username).join(', ')
		});
	}

	function badgeHint(user: User): string {
		switch (user.smb?.state) {
			case 'password_needed':
				return $t('shares.access.passwordNeeded');
			case 'off':
				return $t('shares.access.off');
			default:
				// 'unavailable': the reason is shown as text under the user's level.
				return '';
		}
	}
</script>

<fieldset class="space-y-3 min-w-0">
	<legend class="form-label">{$t('shares.access.title')}</legend>

	{#if reduceOnly && reduceOnlyHint}
		<p class="form-hint mt-0">{reduceOnlyHint}</p>
	{/if}

	{#if rows.length === 0}
		<p class="text-sm text-muted">{$t('shares.access.noUsers')}</p>
	{:else}
		<div class="flex flex-wrap gap-2">
			<Button
				variant="secondary"
				size="sm"
				disabled={reduceOnly || selectable.length === 0}
				on:click={() => setAll('read')}
			>
				{$t('shares.access.everyoneRead')}
			</Button>
			<Button
				variant="secondary"
				size="sm"
				disabled={reduceOnly || selectable.length === 0}
				on:click={() => setAll('write')}
			>
				{$t('shares.access.everyoneWrite')}
			</Button>
			<Button
				variant="ghost"
				size="sm"
				disabled={(selectable.length === 0 && groups.length === 0) || (reduceOnly && !someAccess)}
				on:click={() => setAll('none')}
			>
				{$t('shares.access.clear')}
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

	<p class="form-hint">{$t('shares.access.adminsNotListed')}</p>

	<div class="space-y-3">
		<p class="form-label">{$t('shares.access.groups')}</p>
		{#if groups.length === 0}
			<p class="text-sm text-muted">{$t('shares.access.noGroups')}</p>
		{:else}
			<ul class="space-y-3">
				{#each groups as group, i (group.id)}
					{@const key = groupKey(group.id, group.name)}
					{@const level = groupLevels[key] ?? 'none'}
					{@const saved = choice(savedGroupLevels[key])}
					<li class="flex items-start gap-3">
						<Select
							id="share-group-access-{i}"
							class="flex-1 min-w-0"
							label={$t('shares.access.groupLabel', { name: group.name })}
							options={reduceOnly ? reducedOptions[saved] : options}
							value={level}
							disabled={reduceOnly && level === 'none' && saved === 'none'}
							hint={membersText(group)}
							on:change={(event) => setGroupLevel(group, event.detail?.value)}
						/>
					</li>
				{/each}
			</ul>
			<p class="form-hint">{$t('shares.access.groupsHint')}</p>
		{/if}
	</div>
</fieldset>
