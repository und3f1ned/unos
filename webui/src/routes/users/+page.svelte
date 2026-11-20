<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { deleteUser, endSessions, type User } from '$lib/api/auth';
	import { user as currentUser } from '$lib/stores/auth';
	import { UserPlus } from 'lucide-svelte';
	import { Alert, Button, Skeleton } from '$lib/components/ui';
	import {
		ConfirmDialog,
		CreateUserDialog,
		EditUserDialog,
		GroupsCard,
		OutcomeAlerts,
		UsersAboutCard,
		UsersCard
	} from '$lib/components/users';
	import { UserDirectory } from '$lib/components/users/directory.svelte';
	import { GroupDirectory } from '$lib/components/users/groups.svelte';
	import { messageOf } from '$lib/components/users/errors';
	import { t } from '$lib/i18n';

	const directory = new UserDirectory(() => $t('users.loadFailed'));
	// The groups (ADR-0385), loaded with the users, so that the page is drawn once (ADR-0430).
	const groups = new GroupDirectory();

	// The dialogs that the page's buttons open (raw state: they come and go with the admin's view).
	let createDialog = $state.raw<ReturnType<typeof CreateUserDialog>>();
	let editDialog = $state.raw<ReturnType<typeof EditUserDialog>>();

	onMount(async () => {
		// Redirect non-admin users
		if (!$currentUser?.is_admin) {
			goto('/');
			return;
		}
		await Promise.all([directory.load(), groups.load()]);
	});

	// Also watch for user changes (in case they navigate here before user loads). Before the
	// first render too, as before.
	$effect.pre(() => {
		const current = $currentUser;
		if (current !== null && !current.is_admin) untrack(() => goto('/'));
	});

	// ---- End sessions (ADR-0329) ----

	async function endUserSessions(target: User) {
		try {
			const result = await endSessions(target.id, target.username);
			directory.outcome.show(
				$t('users.endSessions.done', { name: target.username, n: result.ended }),
				[]
			);
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

		{#if directory.error}
			<Alert variant="error">{directory.error}</Alert>
		{/if}
		<OutcomeAlerts outcome={directory.outcome} />

		{#if directory.loading || groups.loading}
			<!-- The first load (ADR-0430): the users' table, the groups and the card under them. -->
			<div class="space-y-6" aria-busy="true">
				<Skeleton shape="table" columns={6} count={3} />
				<Skeleton shape="card" count={3} />
				<Skeleton shape="card" count={3} />
			</div>
		{:else}
			<UsersCard
				users={directory.users}
				currentUserId={$currentUser?.id}
				onedit={(user) => editDialog?.open(user)}
				onendsessions={endUserSessions}
				ondelete={openDelete}
			/>
			<GroupsCard users={directory.users} directory={groups} />
			<UsersAboutCard />
		{/if}
	</div>

	<CreateUserDialog bind:this={createDialog} {directory} />
	<EditUserDialog bind:this={editDialog} {directory} currentUserId={$currentUser?.id} />
	<ConfirmDialog
		open={userToDelete !== null}
		title={$t('users.delete.title')}
		danger
		busy={deleting}
		before={$t('users.delete.before')}
		name={userToDelete?.username ?? null}
		after={$t('users.delete.after')}
		submit={$t('users.delete.submit')}
		error={deleteError}
		errorTitle={$t('users.delete.failed')}
		onclose={closeDelete}
		onconfirm={confirmDelete}
	/>
{/if}
