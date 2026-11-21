<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import {
		createGroup,
		GROUP_NAME_PATTERN,
		updateGroup,
		type Group,
		type GroupMemberEntry,
		type GroupUpdate
	} from '$lib/api/groups';
	import { Alert, Button, Checkbox, HelpText, Input, Sheet } from '$lib/components/ui';
	import { SmbStateBadge } from '$lib/components/shares';
	import { accessKey, canBeGivenAccess } from '$lib/components/shares/access';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';
	import type { GroupDirectory } from './groups.svelte';

	type Props = {
		// The card's groups and users: a change reloads them, and its outcome is shown above them.
		directory: GroupDirectory;
	};

	let { directory }: Props = $props();

	// The dialog: a new group (editing null), or a group's name and members.
	let editing = $state.raw<Group | null>(null);
	let dialogOpen = $state(false);
	let name = $state('');
	let chosen = $state.raw<Record<string, boolean>>({});
	let saving = $state(false);
	let attempted = $state(false);
	let formError = $state('');
	let reloadedNotice = $state('');

	let nameError = $derived(
		name !== '' && !GROUP_NAME_PATTERN.test(name)
			? $t('groups.form.nameInvalid')
			: attempted && name === ''
				? $t('groups.form.nameRequired')
				: ''
	);

	function chosenFrom(group: Group | null): Record<string, boolean> {
		const result: Record<string, boolean> = {};
		for (const member of group?.members ?? []) result[accessKey(member.user_id, member.username)] = true;
		return result;
	}

	/** Opens the dialog for a new group. */
	export function openCreate() {
		editing = null;
		name = '';
		chosen = {};
		attempted = false;
		formError = '';
		reloadedNotice = '';
		dialogOpen = true;
	}

	/** Opens the dialog with the name and members of `group`, as the list shows it now. */
	export function openEdit(group: Group) {
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
		return directory.listedUsers
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
				directory.outcome.show($t('groups.created', { name: created.name }), created.warnings);
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
					directory.outcome.show($t('groups.saved', { name: updated.name }), updated.warnings);
				}
				dialogOpen = false;
			}
			await directory.load();
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
		await directory.load();
		if (!editing) return;
		const fresh = directory.groups.find((group) => group.id === editing?.id);
		if (!fresh) {
			dialogOpen = false;
			directory.outcome.fail(formError);
			return;
		}
		if (fresh.version !== editing.version || fresh.name !== editing.name) {
			editing = fresh;
			name = fresh.name;
			chosen = chosenFrom(fresh);
			reloadedNotice = $t('groups.form.reloaded');
		}
	}
</script>

<!-- A list of members to choose: a Sheet (ADR-0432). -->
<Sheet
	open={dialogOpen}
	title={editing ? $t('groups.form.editTitle', { name: editing.name }) : $t('groups.form.addTitle')}
	size="lg"
	closable={!saving}
	onclose={closeDialog}
>
	<form
		onsubmit={(e) => {
			e.preventDefault();
			save();
		}}
		class="space-y-4"
	>
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
			{#if directory.listedUsers.length === 0}
				<p class="text-sm text-muted">{$t('groups.form.noUsers')}</p>
			{:else}
				<ul class="space-y-2">
					{#each directory.listedUsers as user (user.id)}
						{@const usable = canBeGivenAccess(user)}
						<li>
							<Checkbox
								checked={usable && chosen[accessKey(user.id, user.username)] === true}
								disabled={!usable || saving}
								onchange={(on) => (chosen = { ...chosen, [accessKey(user.id, user.username)]: on })}
							>
								<span class="flex flex-wrap items-center gap-2">
									<span class="break-all">{user.username}</span>
									{#if user.smb && user.smb.state !== 'active'}
										<SmbStateBadge state={user.smb.state} hint={usable ? '' : (user.smb.reason ?? '')} />
									{/if}
								</span>
							</Checkbox>
						</li>
					{/each}
				</ul>
			{/if}
			<HelpText text={$t('groups.form.adminsNotListed')} />
		</fieldset>
		<HelpText text={$t('groups.form.accessNote')} />
	</form>

	{#if formError}
		<Alert variant="error" title={editing ? $t('groups.form.notSaved') : $t('groups.form.notCreated')}>
			<p>{formError}</p>
			{#if reloadedNotice}
				<p class="mt-2">{reloadedNotice}</p>
			{/if}
		</Alert>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={saving} onclick={closeDialog}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={saving} onclick={save}>
			{editing ? $t('groups.form.save') : $t('groups.form.create')}
		</Button>
	{/snippet}
</Sheet>
