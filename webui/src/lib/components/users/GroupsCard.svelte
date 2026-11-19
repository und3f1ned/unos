<script lang="ts">
	import { untrack } from 'svelte';
	import { Edit, Plus, Trash2, UsersRound } from 'lucide-svelte';
	import type { User } from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import { deleteGroup, type Group } from '$lib/api/groups';
	import { Alert, Button, Card } from '$lib/components/ui';
	import { SmbStateBadge } from '$lib/components/shares';
	import { t } from '$lib/i18n';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import { messageOf } from './errors';
	import GroupDialog from './GroupDialog.svelte';
	import { GroupDirectory } from './groups.svelte';
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
	};

	let { users = [] }: Props = $props();

	const directory = new GroupDirectory();

	// The dialog of a new group or of a group's name and members.
	let groupDialog: ReturnType<typeof GroupDialog> | undefined;

	// Once mounted, and again with each new list of users (no polling: only after a load of the
	// page's own).
	$effect(() => {
		void users;
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

<Card>
	<div class="flex flex-wrap items-center justify-between gap-3 mb-4">
		<h2 class="section-title">
			<UsersRound class="w-5 h-5" />
			{$t('groups.title', { count: directory.groups.length })}
		</h2>
		<Button icon={Plus} variant="secondary" onclick={() => groupDialog?.openCreate()}>{$t('groups.add')}</Button>
	</div>
	<p class="text-sm text-secondary mb-4">{$t('groups.about')}</p>

	{#if directory.loadError}
		<Alert variant="error" title={$t('groups.loadFailed')}>{directory.loadError}</Alert>
	{/if}
	<OutcomeAlerts outcome={directory.outcome} failureTitle={$t('groups.actionFailed')} />

	{#if directory.loading}
		<p class="loading-text">{$t('common.loading')}</p>
	{:else if directory.groups.length === 0 && !directory.loadError}
		<p class="text-muted">{$t('groups.empty')}</p>
	{:else}
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
							<Button variant="secondary" size="sm" icon={Edit} onclick={() => groupDialog?.openEdit(group)}>
								{$t('groups.edit')}<span class="sr-only">{` ${$t('groups.editOf', { name: group.name })}`}</span>
							</Button>
							<Button variant="ghost" size="icon-sm" onclick={() => openDelete(group)}>
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

<GroupDialog bind:this={groupDialog} {directory} />

<ConfirmDialog
	open={toDelete !== null}
	title={$t('groups.delete.title')}
	danger
	busy={deleting}
	before={$t('groups.delete.before')}
	name={toDelete?.name ?? null}
	after={$t('groups.delete.after')}
	submit={$t('groups.delete.submit')}
	error={deleteError}
	errorTitle={$t('groups.delete.failed')}
	onclose={closeDelete}
	onconfirm={confirmDelete}
/>
