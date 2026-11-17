<script lang="ts">
	import { onMount } from 'svelte';
	import { Edit, Plus, Trash2, UsersRound } from 'lucide-svelte';
	import { getUsers, type User } from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import {
		createGroup,
		deleteGroup,
		getGroups,
		GROUP_NAME_PATTERN,
		updateGroup,
		type Group,
		type GroupMemberEntry,
		type GroupUpdate
	} from '$lib/api/groups';
	import { Alert, Button, Card, Input, Modal } from '$lib/components/ui';
	import { SmbStateBadge } from '$lib/components/shares';
	import { accessKey, canBeGivenAccess, pickableUsers } from '$lib/components/shares/access';
	import { t } from '$lib/i18n';

	/**
	 * Groups of users (ADR-0385): a group gets access on the Shares page, and each member whose SMB
	 * is on gets it. The page's users: when it loads them again (a user deleted, renamed or made an
	 * administrator leaves or changes their groups), the card loads its groups and users again. The
	 * card lists the users it loaded with the groups, so that a dialog never sends a member list
	 * older than the group.
	 */
	export let users: User[] = [];

	let groups: Group[] = [];
	let known: User[] = [];
	let loading = true;
	let loadError = '';
	let notice = '';
	let warnings: string[] = [];
	let actionError = '';
	let mounted = false;
	let request = 0;

	onMount(() => {
		mounted = true;
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function loadGroups() {
		const mine = ++request;
		try {
			const [loaded, loadedUsers] = await Promise.all([getGroups(), getUsers()]);
			if (mine !== request) return;
			groups = loaded;
			known = loadedUsers;
			loadError = '';
		} catch (e) {
			if (mine === request) loadError = messageOf(e);
		} finally {
			if (mine === request) loading = false;
		}
	}

	// Each new list of users loads the groups again (no polling: only after a load of the page's own).
	$: if (mounted) reloadFor(users);

	function reloadFor(_users: User[]) {
		void loadGroups();
	}

	function showOutcome(message: string, newWarnings: string[] | undefined) {
		notice = message;
		warnings = newWarnings ?? [];
		actionError = '';
	}

	$: levelText = (level: string): string =>
		level === 'write' ? $t('groups.level.write') : $t('groups.level.read');

	// ---- The dialog: a new group, or a group's name and members ----

	let editing: Group | null = null;
	let dialogOpen = false;
	let name = '';
	let chosen: Record<string, boolean> = {};
	let saving = false;
	let attempted = false;
	let formError = '';
	let reloadedNotice = '';

	$: listedUsers = pickableUsers(known);
	$: nameError =
		name !== '' && !GROUP_NAME_PATTERN.test(name)
			? $t('groups.form.nameInvalid')
			: attempted && name === ''
				? $t('groups.form.nameRequired')
				: '';

	function chosenFrom(group: Group | null): Record<string, boolean> {
		const result: Record<string, boolean> = {};
		for (const member of group?.members ?? []) result[accessKey(member.user_id, member.username)] = true;
		return result;
	}

	function openCreate() {
		editing = null;
		name = '';
		chosen = {};
		attempted = false;
		formError = '';
		reloadedNotice = '';
		dialogOpen = true;
	}

	function openEdit(group: Group) {
		editing = group;
		name = group.name;
		chosen = chosenFrom(group);
		attempted = false;
		formError = '';
		reloadedNotice = '';
		dialogOpen = true;
	}

	function closeDialog() {
		if (!saving) dialogOpen = false;
	}

	/** The members the dialog sends: the ticked users who can be given access, sorted by id. */
	function memberEntries(): GroupMemberEntry[] {
		return listedUsers
			.filter((user) => canBeGivenAccess(user) && chosen[accessKey(user.id, user.username)])
			.map((user) => ({ user_id: user.id, username: user.username }))
			.sort((a, b) => a.user_id - b.user_id);
	}

	function sameMembers(group: Group, entries: GroupMemberEntry[]): boolean {
		const before = group.members.map((m) => accessKey(m.user_id, m.username)).sort();
		const after = entries.map((m) => accessKey(m.user_id, m.username)).sort();
		return before.length === after.length && before.every((key, i) => key === after[i]);
	}

	async function save() {
		if (saving) return;
		attempted = true;
		formError = '';
		reloadedNotice = '';
		if (!GROUP_NAME_PATTERN.test(name)) return;
		const members = memberEntries();
		saving = true;
		try {
			if (!editing) {
				const created = await createGroup({ name, members });
				dialogOpen = false;
				showOutcome($t('groups.created', { name: created.name }), created.warnings);
			} else {
				const patch: Partial<GroupUpdate> = {};
				if (name !== editing.name) patch.new_name = name;
				if (!sameMembers(editing, members)) patch.members = members;
				if (Object.keys(patch).length > 0) {
					// The name and the version make the server refuse (409) a group renamed or changed
					// since it was loaded.
					const updated = await updateGroup(editing.id, {
						name: editing.name,
						expected_version: editing.version,
						...patch
					});
					showOutcome($t('groups.saved', { name: updated.name }), updated.warnings);
				}
				dialogOpen = false;
			}
			await loadGroups();
		} catch (e) {
			formError = messageOf(e);
			if (e instanceof ApiError && [400, 404, 409].includes(e.status)) await reloadAfterRefusal();
		} finally {
			saving = false;
		}
	}

	/**
	 * After a refusal that can mean the lists are out of date, load the groups and the users again. A
	 * group deleted meanwhile closes the dialog; one changed meanwhile resets the form, since sending
	 * this form's members again would undo the other change.
	 */
	async function reloadAfterRefusal() {
		await loadGroups();
		if (!editing) return;
		const fresh = groups.find((group) => group.id === editing?.id);
		if (!fresh) {
			dialogOpen = false;
			actionError = formError;
			return;
		}
		if (fresh.version !== editing.version || fresh.name !== editing.name) {
			editing = fresh;
			name = fresh.name;
			chosen = chosenFrom(fresh);
			reloadedNotice = $t('groups.form.reloaded');
		}
	}

	// ---- Delete ----

	let toDelete: Group | null = null;
	let deleting = false;
	let deleteError = '';

	function openDelete(group: Group) {
		toDelete = group;
		deleteError = '';
	}

	function closeDelete() {
		if (!deleting) toDelete = null;
	}

	async function confirmDelete() {
		if (!toDelete) return;
		const target = toDelete;
		deleting = true;
		deleteError = '';
		try {
			const result = await deleteGroup(target.id, target.name);
			deleting = false;
			toDelete = null;
			showOutcome($t('groups.deleted', { name: target.name }), result.warnings);
			await loadGroups();
		} catch (e) {
			if (e instanceof ApiError && (e.status === 404 || e.status === 409)) {
				deleting = false;
				toDelete = null;
				actionError = messageOf(e);
				await loadGroups();
				return;
			}
			deleteError = messageOf(e);
		} finally {
			deleting = false;
		}
	}
</script>

<Card>
	<div class="flex flex-wrap items-center justify-between gap-3 mb-4">
		<h2 class="section-title">
			<UsersRound class="w-5 h-5" />
			{$t('groups.title', { count: groups.length })}
		</h2>
		<Button icon={Plus} variant="secondary" on:click={openCreate}>{$t('groups.add')}</Button>
	</div>
	<p class="text-sm text-secondary mb-4">{$t('groups.about')}</p>

	{#if loadError}
		<Alert variant="error" title={$t('groups.loadFailed')}>{loadError}</Alert>
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
		<Alert variant="error" title={$t('groups.actionFailed')} dismissible on:dismiss={() => (actionError = '')}
			>{actionError}</Alert
		>
	{/if}

	{#if loading}
		<p class="loading-text">{$t('common.loading')}</p>
	{:else if groups.length === 0 && !loadError}
		<p class="text-muted">{$t('groups.empty')}</p>
	{:else}
		<ul class="space-y-3">
			{#each groups as group (group.id)}
				<li class="card-nested">
					<div class="flex flex-wrap items-start justify-between gap-3">
						<div class="min-w-0 space-y-1 text-sm">
							<h3 class="font-semibold text-base break-all">{group.name}</h3>
							{#if group.members.length === 0}
								<p class="text-muted">{$t('groups.noMembers')}</p>
							{:else}
								<p class="flex flex-wrap items-center gap-x-2 gap-y-1">
									<span class="text-secondary">{$t('groups.members')}</span>
									{#each group.members as member (member.user_id)}
										<span class="inline-flex items-center gap-1 break-all"
											>{member.username}{#if member.smb_state !== 'active'}<SmbStateBadge
													state={member.smb_state}
												/>{/if}</span
										>
									{/each}
								</p>
							{/if}
							{#if group.shares.length === 0}
								<p class="text-muted">{$t('groups.noShares')}</p>
							{:else}
								<p class="break-words">
									<span class="text-secondary">{$t('groups.shares')}</span>
									{group.shares
										.map((entry) => $t('groups.shareLevel', { share: entry.share, level: levelText(entry.level) }))
										.join(', ')}
								</p>
							{/if}
						</div>
						<div class="flex flex-wrap items-center gap-2">
							<Button variant="secondary" size="sm" icon={Edit} on:click={() => openEdit(group)}>
								{$t('groups.edit')}<span class="sr-only">{` ${$t('groups.editOf', { name: group.name })}`}</span>
							</Button>
							<Button variant="ghost" size="icon-sm" on:click={() => openDelete(group)}>
								<Trash2 class="w-4 h-4 text-error" />
								<span class="sr-only">{$t('groups.deleteGroup', { name: group.name })}</span>
							</Button>
						</div>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</Card>

<Modal
	open={dialogOpen}
	title={editing ? $t('groups.form.editTitle', { name: editing.name }) : $t('groups.form.addTitle')}
	size="lg"
	closable={!saving}
	on:close={closeDialog}
>
	<form on:submit|preventDefault={save} class="space-y-4">
		<Input
			id="group-name"
			label={$t('groups.form.name')}
			bind:value={name}
			autocomplete="off"
			maxlength={32}
			hint={$t('groups.form.nameHint')}
			error={nameError}
			disabled={saving}
		/>

		<fieldset class="space-y-2 min-w-0">
			<legend class="form-label">{$t('groups.form.members')}</legend>
			{#if listedUsers.length === 0}
				<p class="text-sm text-muted">{$t('groups.form.noUsers')}</p>
			{:else}
				<ul class="space-y-2">
					{#each listedUsers as user (user.id)}
						{@const usable = canBeGivenAccess(user)}
						<li>
							<label class="flex flex-wrap items-center gap-2 {usable ? 'cursor-pointer' : 'opacity-60'}">
								<input
									type="checkbox"
									class="form-checkbox"
									checked={usable && chosen[accessKey(user.id, user.username)] === true}
									disabled={!usable || saving}
									on:change={(event) =>
										(chosen = {
											...chosen,
											[accessKey(user.id, user.username)]: event.currentTarget.checked
										})}
								/>
								<span class="break-all">{user.username}</span>
								{#if user.smb && user.smb.state !== 'active'}
									<SmbStateBadge state={user.smb.state} hint={usable ? '' : user.smb.reason ?? ''} />
								{/if}
							</label>
						</li>
					{/each}
				</ul>
			{/if}
			<p class="form-hint">{$t('groups.form.adminsNotListed')}</p>
		</fieldset>
		<p class="form-hint">{$t('groups.form.accessNote')}</p>
	</form>

	{#if formError}
		<Alert variant="error" title={editing ? $t('groups.form.notSaved') : $t('groups.form.notCreated')}>
			<p>{formError}</p>
			{#if reloadedNotice}
				<p class="mt-2">{reloadedNotice}</p>
			{/if}
		</Alert>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={saving} on:click={closeDialog}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={saving} on:click={save}>
			{editing ? $t('groups.form.save') : $t('groups.form.create')}
		</Button>
	</svelte:fragment>
</Modal>

<Modal
	open={toDelete !== null}
	title={$t('groups.delete.title')}
	size="md"
	danger
	closable={!deleting}
	on:close={closeDelete}
>
	{#if toDelete}
		<!-- In parts around the name: ADR-0296. -->
		<p>
			{$t('groups.delete.before')}
			<strong class="text-content">{toDelete.name}</strong>{$t('groups.delete.after')}
		</p>
		{#if deleteError}
			<Alert variant="error" title={$t('groups.delete.failed')}>{deleteError}</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={deleting} on:click={closeDelete}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={deleting} on:click={confirmDelete}>
			{$t('groups.delete.submit')}
		</Button>
	</svelte:fragment>
</Modal>
