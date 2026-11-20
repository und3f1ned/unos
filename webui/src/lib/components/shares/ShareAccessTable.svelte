<script lang="ts">
	import type { Snippet } from 'svelte';
	import { User as UserIcon, Users as UsersIcon } from 'lucide-svelte';
	import type { User } from '$lib/api/auth';
	import type { Group } from '$lib/api/groups';
	import type { IconComponent } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import AccessLevelControl from './AccessLevelControl.svelte';
	import { accessKey, canBeGivenAccess, commonLevel, groupKey, pickableUsers, type AccessChoice } from './access';

	// Who may use a share and how: one row per user and per group, the name with its kind
	// and SMB state on the left and the level as a segmented control on the right; on a phone the
	// control goes under the name. An All users row sets every user at once. Nothing is sent before
	// the form is saved.

	type Props = {
		/** Every UNOS user (GET /auth/users); administrators are not listed. */
		users?: User[];
		/** The choice per user, by accessKey(). Missing: no access. */
		levels?: Record<string, AccessChoice>;
		/**
		 * Access can only be removed, never given (a share that is not served): None and each row's
		 * saved level can be chosen, so that a removal can be undone; the other levels cannot.
		 */
		reduceOnly?: boolean;
		/** Shown while access can only be removed. */
		reduceOnlyHint?: string;
		/** The levels the share has saved, by accessKey(). Missing: no access. */
		savedLevels?: Record<string, AccessChoice>;
		/** Every group (GET /groups), sorted by name. */
		groups?: Group[];
		/** The choice per group, by groupKey(). Missing: no access. */
		groupLevels?: Record<string, AccessChoice>;
		/** The levels the share has saved for groups, by groupKey(). */
		savedGroupLevels?: Record<string, AccessChoice>;
	};

	let {
		users = [],
		levels = $bindable({}),
		reduceOnly = false,
		reduceOnlyHint = '',
		savedLevels = {},
		groups = [],
		groupLevels = $bindable({}),
		savedGroupLevels = {}
	}: Props = $props();

	const rows = $derived(pickableUsers(users));
	const selectable = $derived(rows.filter(canBeGivenAccess));
	const allLevel = $derived(commonLevel(selectable.map((user) => levels[accessKey(user.id, user.username)] ?? 'none')));

	function choice(value: unknown): AccessChoice {
		return value === 'read' || value === 'write' ? value : 'none';
	}

	/** The levels that cannot be chosen while access can only be removed: all but None and the saved one. */
	function blockedFor(saved: AccessChoice): AccessChoice[] {
		if (!reduceOnly) return [];
		return (['read', 'write'] as AccessChoice[]).filter((level) => level !== saved);
	}

	function setLevel(user: User, level: AccessChoice) {
		levels = { ...levels, [accessKey(user.id, user.username)]: level };
	}

	function setGroupLevel(group: Group, level: AccessChoice) {
		groupLevels = { ...groupLevels, [groupKey(group.id, group.name)]: level };
	}

	// The All users row sets the level of every user who can be given access (ADR-0076); while
	// access can only be removed, it can only take it away.
	function setAll(level: AccessChoice) {
		const next = { ...levels };
		for (const user of selectable) next[accessKey(user.id, user.username)] = level;
		levels = next;
	}

	function groupHint(group: Group): string {
		if (group.members.length === 0) return $t('shares.access.groupNoMembers');
		return $t('shares.access.groupMembers', {
			n: group.members.length,
			names: group.members.map((member) => member.username).join(', ')
		});
	}
</script>

<!-- One row: who on the left, the level on the right (below it on a phone). -->
{#snippet row(id: string, icon: IconComponent, muted: boolean, who: Snippet, control: Snippet)}
	{@const Icon = icon}
	<li class="access-row" class:access-row-muted={muted} data-access-row={id}>
		<div class="flex min-w-0 items-start gap-2.5">
			<span class="access-icon" aria-hidden="true"><Icon class="h-4 w-4" /></span>
			<div class="min-w-0 flex-1">{@render who()}</div>
		</div>
		<div class="access-control">{@render control()}</div>
	</li>
{/snippet}

<fieldset class="min-w-0 space-y-2">
	<legend class="form-label">{$t('shares.access.title')}</legend>

	{#if reduceOnly && reduceOnlyHint}
		<p class="form-hint mt-0">{reduceOnlyHint}</p>
	{/if}

	<div class="access-table">
		<!-- The columns' heads, for the eye: each row's control is named by its row. -->
		<div class="access-head" aria-hidden="true">
			<span>{$t('shares.access.who')}</span>
			<span class="hidden sm:block">{$t('shares.access.level')}</span>
		</div>

		<p class="access-section eyebrow" id="share-access-users">{$t('shares.access.users')}</p>
		{#if rows.length === 0}
			<p class="access-empty">
				{$t('shares.access.noUsers')}
				<a class="link" href="/users" target="_blank" rel="noopener">{$t('shares.access.addUser')}<span class="sr-only">{` ${$t('shares.access.newTab')}`}</span></a>
			</p>
		{:else}
			<ul class="access-rows" aria-labelledby="share-access-users">
				{#if selectable.length > 1}
					{#snippet allWho()}
						<p class="access-name" id="share-access-all-name">{$t('shares.access.allUsers')}</p>
						<p class="access-hint">{$t('shares.access.allUsersHint')}</p>
					{/snippet}
					{#snippet allControl()}
						<AccessLevelControl
							name="share-access-all"
							value={allLevel}
							labelledby="share-access-all-name"
							blocked={reduceOnly ? ['read', 'write'] : []}
							onchange={setAll}
						/>
					{/snippet}
					{@render row('all', UsersIcon, false, allWho, allControl)}
				{/if}
				{#each rows as user (user.id)}
					{@const key = accessKey(user.id, user.username)}
					{@const usable = canBeGivenAccess(user)}
					{@const level = usable ? (levels[key] ?? 'none') : 'none'}
					{@const saved = choice(savedLevels[key])}
					{@const state = user.smb?.state}
					{#snippet who()}
						<p class="access-name" id="share-access-name-{user.id}">{user.username}</p>
						<p class="access-hint" id="share-access-hint-{user.id}">
							{#if state === 'off'}
								<span class="access-tag">{$t('shares.access.smbOff')}</span>
								<a class="link" href="/users" target="_blank" rel="noopener">{$t('shares.access.turnOnSmb')}<span class="sr-only">{` ${$t('shares.access.newTab')}`}</span></a>
							{:else if state === 'password_needed'}
								<span class="access-tag">{$t('shares.access.smbPending')}</span>
								{$t('shares.access.smbPendingHint')}
							{:else if state === 'unavailable'}
								<span class="access-tag">{$t('shares.access.smbUnavailable')}</span>
								<!-- The server's reason in two lines, whole in its hover text; the Users page shows it whole. -->
								<span class="line-clamp-2 w-full" title={user.smb?.reason ?? ''}>{user.smb?.reason ?? ''}</span>
							{:else}
								{$t('shares.access.kindUser')}
							{/if}
						</p>
					{/snippet}
					{#snippet control()}
						<!-- While access can only be removed, a user with no access, now or saved, has nothing to change. -->
						<AccessLevelControl
							name="share-access-{user.id}"
							value={level}
							labelledby="share-access-name-{user.id}"
							describedby="share-access-hint-{user.id}"
							disabled={!usable || (reduceOnly && level === 'none' && saved === 'none')}
							blocked={blockedFor(saved)}
							onchange={(next) => setLevel(user, next)}
						/>
					{/snippet}
					{@render row(String(user.id), UserIcon, state !== undefined && state !== 'active', who, control)}
				{/each}
			</ul>
		{/if}

		<p class="access-section eyebrow" id="share-access-groups">{$t('shares.access.groups')}</p>
		{#if groups.length === 0}
			<p class="access-empty">
				{$t('shares.access.noGroups')}
				<a class="link" href="/users#groups" target="_blank" rel="noopener">{$t('shares.access.createGroup')}<span class="sr-only">{` ${$t('shares.access.newTab')}`}</span></a>
			</p>
		{:else}
			<ul class="access-rows" aria-labelledby="share-access-groups">
				{#each groups as group, i (group.id)}
					{@const key = groupKey(group.id, group.name)}
					{@const level = groupLevels[key] ?? 'none'}
					{@const saved = choice(savedGroupLevels[key])}
					{#snippet who()}
						<p class="access-name">{group.name}</p>
						<p class="access-hint break-words" id="share-group-access-hint-{i}">{groupHint(group)}</p>
					{/snippet}
					{#snippet control()}
						<AccessLevelControl
							name="share-group-access-{i}"
							value={level}
							label={$t('shares.access.groupLabel', { name: group.name })}
							describedby="share-group-access-hint-{i}"
							disabled={reduceOnly && level === 'none' && saved === 'none'}
							blocked={blockedFor(saved)}
							onchange={(next) => setGroupLevel(group, next)}
						/>
					{/snippet}
					{@render row(`group-${group.id}`, UsersIcon, false, who, control)}
				{/each}
			</ul>
		{/if}
	</div>

	<p class="form-hint">
		{$t('shares.access.adminsNotListed')}
		{#if groups.length > 0}{' '}{$t('shares.access.groupsHint')}{/if}
	</p>
</fieldset>

<style>
	.access-table {
		@apply rounded-card border border-border;
	}

	.access-head {
		@apply flex items-center justify-between gap-4 px-3 py-2 text-xs font-medium text-content-secondary border-b border-border;
	}

	.access-section {
		@apply px-3 pt-3 pb-1;
	}

	.access-rows {
		@apply divide-y divide-border;
	}

	.access-row {
		@apply grid grid-cols-1 items-center gap-x-4 gap-y-2 px-3 py-2.5;
	}

	@media (min-width: 640px) {
		.access-row {
			grid-template-columns: minmax(0, 1fr) auto;
		}
	}

	.access-control {
		@apply min-w-0 sm:justify-self-end;
	}

	.access-icon {
		@apply mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-overlay text-content-secondary;
	}

	.access-name {
		@apply font-medium text-content break-all;
	}

	/* A user whose SMB is not on: the access counts once it is. */
	.access-row-muted .access-icon {
		@apply opacity-60;
	}

	.access-row-muted .access-name {
		@apply text-content-secondary;
	}

	.access-hint {
		@apply mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs leading-[18px] text-content-muted;
	}

	.access-tag {
		@apply inline-flex items-center rounded border border-border-strong px-1.5 text-2xs font-medium text-content-secondary;
	}

	.access-empty {
		@apply px-3 pb-3 text-sm text-content-secondary;
	}

	.access-section + .access-rows,
	.access-section + .access-empty {
		@apply border-b border-border;
	}

	.access-rows:last-child,
	.access-empty:last-child {
		@apply border-b-0;
	}
</style>
