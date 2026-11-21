<script lang="ts">
	import { untrack } from 'svelte';
	import { Edit, Plus, Trash2, UsersRound } from 'lucide-svelte';
	import type { User } from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import { deleteGroup, type Group } from '$lib/api/groups';
	import { Alert, Button, Card, CardAction, ConfirmDialog, EmptyState, HelpText, LoadState, Skeleton } from '$lib/components/ui';
	import { SmbStateBadge } from '$lib/components/shares';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';
	import GroupDialog from './GroupDialog.svelte';
	import type { GroupDirectory } from './groups.svelte';
	import OutcomeAlerts from './OutcomeAlerts.svelte';

	type Props = {
		/**
		 * Groups of users (ADR-0385): a group gets access on the Shares page, and each member whose
		 * SMB is on gets it. The page's users: when it loads them again (a user deleted, renamed or
		 * made an administrator leaves or changes their groups), the card loads its groups and users
		 * again. The card lists the users it loaded with the groups, so that a dialog never sends a
		 * member list older than the group.
		 */
		users?: User[];
		/** The groups, which the page loads with its users when it opens (ADR-0430). */
		directory: GroupDirectory;
	};

	let { users = [], directory }: Props = $props();

	// The dialog of a new group or of a group's name and members.
	let groupDialog: ReturnType<typeof GroupDialog> | undefined;

	let retrying = $state(false);

	async function retry() {
		retrying = true;
		try {
			await directory.load();
		} finally {
			retrying = false;
		}
	}

	// Again with each new list of users (no polling: only after a load of the page's own). The
	// page loaded the groups with the first list.
	let usersLoaded: User[] | null = null;
	$effect(() => {
		const current = users;
		if (usersLoaded === null || current === usersLoaded) {
			usersLoaded = current;
			return;
		}
		usersLoaded = current;
		untrack(() => void directory.load());
	});

	// The markup that calls it reads $t through it: the text follows a change of the language.
	function levelText(level: string): string {
		return level === 'write' ? $t('groups.level.write') : $t('groups.level.read');
	}

	// ---- Delete ----

	let toDelete = $state.raw<Group | null>(null);
	let deleting = $state(false);
	let deleteError = $state('');

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
			directory.outcome.show($t('groups.deleted', { name: target.name }), result.warnings);
			await directory.load();
		} catch (e) {
			if (e instanceof ApiError && (e.status === 404 || e.status === 409)) {
				deleting = false;
				toDelete = null;
				directory.outcome.fail(messageOf(e));
				await directory.load();
				return;
			}
			deleteError = messageOf(e);
		} finally {
			deleting = false;
		}
	}
</script>

<!-- id: the Shares page's access table links here to create a group. -->
<div id="groups" class="scroll-mt-4">
<Card>
	<div class="card-header flex-wrap gap-3">
		<h2 class="section-title">
			<UsersRound class="w-5 h-5 text-content-secondary" aria-hidden="true" />
			{$t('groups.title', { count: directory.groups.length })}
		</h2>
		<CardAction icon={Plus} onclick={() => groupDialog?.openCreate()}>{$t('groups.add')}</CardAction>
	</div>
	<HelpText text={$t('groups.about')} class="mb-4" />

	<OutcomeAlerts outcome={directory.outcome} failureTitle={$t('groups.actionFailed')} />

	<LoadState
		loading={directory.loading}
		error={directory.loadError}
		errorTitle={$t('groups.loadFailed')}
		keepOnError={directory.groups.length > 0}
		empty={directory.groups.length === 0}
		onretry={retry}
		{retrying}
	>
		{#snippet skeleton()}<Skeleton count={2} />{/snippet}
		{#snippet emptyState()}
			<EmptyState compact icon={UsersRound} title={$t('groups.empty')} />
		{/snippet}
		<ul class="space-y-3">
			{#each directory.groups as group (group.id)}
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
										<span class="inline-flex flex-wrap items-center gap-1 break-words"
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
							<Button variant="secondary" size="sm" icon={Edit} onclick={() => groupDialog?.openEdit(group)}>
								{$t('groups.edit')}<span class="sr-only">{` ${$t('groups.editOf', { name: group.name })}`}</span>
							</Button>
							<Button variant="ghost-danger" size="icon-sm" title={$t('groups.deleteShort')} onclick={() => openDelete(group)}>
								<Trash2 class="w-4 h-4" />
								<span class="sr-only">{$t('groups.deleteGroup', { name: group.name })}</span>
							</Button>
						</div>
					</div>
				</li>
			{/each}
		</ul>
	</LoadState>
</Card>
</div>

<GroupDialog bind:this={groupDialog} {directory} />

<ConfirmDialog
	open={toDelete !== null}
	title={$t('groups.delete.title')}
	busy={deleting}
	confirmLabel={$t('groups.delete.submit')}
	oncancel={closeDelete}
	onconfirm={confirmDelete}
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
</ConfirmDialog>
