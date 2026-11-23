<script lang="ts">
	import { tick, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { deleteUser, endSessions, type User } from '$lib/api/auth';
	import { user as currentUser } from '$lib/stores/auth';
	import { UserPlus } from 'lucide-svelte';
	import { Alert, Button, ConfirmDialog, LoadState, Skeleton } from '$lib/components/ui';
	import { CreateUserDialog, EditUserDialog, GroupsCard, OutcomeAlerts, UsersCard } from '$lib/components/users';
	import { UserDirectory } from '$lib/components/users/directory.svelte';
	import { GroupDirectory } from '$lib/components/users/groups.svelte';
	import { messageOf } from '$lib/components/users/errors';
	import { t } from '$lib/i18n';
	import { followPage } from '$lib/pages/follow.svelte';
	import type { UsersSnapshot } from '$lib/pages/users';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const directory = new UserDirectory(() => $t('users.loadFailed'));
	// The groups (ADR-0385), read with the users, so that the page is drawn once (ADR-0430).
	const groups = new GroupDirectory();

	// The dialogs that the page's buttons open (raw state: they come and go with the admin's view).
	let createDialog = $state.raw<ReturnType<typeof CreateUserDialog>>();
	let editDialog = $state.raw<ReturnType<typeof EditUserDialog>>();

	// What the page's load read ($lib/pages/users): shown before the page is drawn, and replaced by
	// a fresh read when the load gave the last visit's. The groups card takes the users read with
	// its groups as its own, so that it does not read them again.
	followPage(
		() => data,
		(snapshot: UsersSnapshot) => {
			directory.apply(snapshot.users);
			groups.apply(snapshot.groups, snapshot.users);
		}
	);

	// A link to a card of the page (/users#groups, from a share's access table) finds it once the
	// page has drawn it with its data: at once after a navigation, later on a slow first load.
	let scrolled = false;
	$effect(() => {
		if (!directory.loaded || scrolled) return;
		scrolled = true;
		untrack(async () => {
			await tick();
			const target = location.hash ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
			if (target) requestAnimationFrame(() => target.scrollIntoView({ block: 'start' }));
		});
	});

	// A regular account leaves (the root layout leads it away too), before the first render.
	$effect.pre(() => {
		const current = $currentUser;
		if (current !== null && !current.is_admin) untrack(() => goto('/'));
	});

	let retrying = $state(false);

	async function retry() {
		retrying = true;
		await Promise.all([directory.load(false), groups.load()]);
		retrying = false;
	}

	// ---- End sessions (ADR-0329) ----

	async function endUserSessions(target: User) {
		try {
			const result = await endSessions(target.id, target.username);
			directory.outcome.show($t('users.endSessions.done', { name: target.username, n: result.ended }), []);
		} catch (e) {
			const message = messageOf(e);
			await directory.currentAfterRefusal(e, target);
			directory.outcome.fail(message, $t('users.endSessions.failed'));
		}
	}

	// ---- Delete ----

	let userToDelete = $state.raw<User | null>(null);
	let deleting = $state(false);
	let deleteError = $state('');

	/** What deleting `user` does, said before it is done; the server's delete_user does it. */
	function deleteConsequences(user: User): string[] {
		const smb = user.smb?.state === 'active' || user.smb?.state === 'password_needed';
		return [
			$t('users.delete.sessions'),
			$t('users.delete.access'),
			smb ? $t('users.delete.smb') : '',
			$t('users.delete.files')
		].filter(Boolean);
	}

	function openDelete(user: User) {
		userToDelete = user;
		deleteError = '';
	}

	function closeDelete() {
		if (!deleting) userToDelete = null;
	}

	async function confirmDelete() {
		if (!userToDelete) return;
		const target = userToDelete;
		deleting = true;
		deleteError = '';
		try {
			const result = await deleteUser(target.id, target.username);
			deleting = false;
			userToDelete = null;
			directory.outcome.show($t('users.delete.done', { name: target.username }), result?.warnings);
			await directory.load(false);
		} catch (e) {
			const message = messageOf(e);
			if ((await directory.currentAfterRefusal(e, target)) === null) {
				// From a list loaded before the id named someone else (or nobody).
				deleting = false;
				userToDelete = null;
				directory.outcome.fail(message, $t('users.delete.failed'));
				return;
			}
			deleteError = message;
		} finally {
			deleting = false;
		}
	}
</script>

<!-- Only render content if user is admin -->
{#if $currentUser?.is_admin}
	<div class="page-container">
		<div class="page-header">
			<h1 class="page-title">{$t('users.title')}</h1>
			<Button icon={UserPlus} onclick={() => createDialog?.open()}>{$t('users.add')}</Button>
		</div>

		<OutcomeAlerts outcome={directory.outcome} />

		<!-- A failed load says so, with Retry: never an empty list. Once loaded, the list stays under
		     it. -->
		<LoadState
			loading={!directory.loaded && !directory.error}
			error={directory.error}
			errorTitle={$t('users.loadFailed')}
			keepOnError={directory.loaded}
			onretry={retry}
			{retrying}
			class="space-y-6"
		>
			{#snippet skeleton()}
				<!-- The tab's first page, while its load takes a while (ADR-0469): the users' table, the
				     groups and the card under them. -->
				<div class="space-y-6">
					<Skeleton shape="table" columns={6} count={3} />
					<Skeleton shape="card" count={3} />
					<Skeleton shape="card" count={3} />
				</div>
			{/snippet}
			<UsersCard
				users={directory.users}
				currentUserId={$currentUser?.id}
				onedit={(user) => editDialog?.open(user)}
				onendsessions={endUserSessions}
				ondelete={openDelete}
			/>
			<GroupsCard users={directory.users} directory={groups} />
		</LoadState>
	</div>

	<CreateUserDialog bind:this={createDialog} {directory} />
	<EditUserDialog bind:this={editDialog} {directory} currentUserId={$currentUser?.id} />
	<ConfirmDialog
		open={userToDelete !== null}
		title={$t('users.delete.title')}
		busy={deleting}
		confirmLabel={$t('users.delete.submit')}
		oncancel={closeDelete}
		onconfirm={confirmDelete}
	>
		{#if userToDelete}
			<!-- In parts around the name: ADR-0296. -->
			<p>
				{$t('users.delete.before')}
				<strong class="text-content">{userToDelete.username}</strong>{$t('users.delete.after')}
			</p>
			<!-- What it does, after the question that names the user. -->
			<div>
				<p class="eyebrow mb-1.5">{$t('ui.confirm.consequences')}</p>
				<ul class="confirm-consequences">
					{#each deleteConsequences(userToDelete) as consequence (consequence)}
						<li>{consequence}</li>
					{/each}
				</ul>
			</div>
			{#if deleteError}
				<Alert variant="error" title={$t('users.delete.failed')}>{deleteError}</Alert>
			{/if}
		{/if}
	</ConfirmDialog>
{/if}
