<script lang="ts">
	import { Edit, LogOut, Shield, Trash2, User as UserIcon, Users } from 'lucide-svelte';
	import type { User } from '$lib/api/auth';
	import { ActionMenu, Badge, Button, Card } from '$lib/components/ui';
	import { SmbStateBadge } from '$lib/components/shares';
	import { intlLocale, t } from '$lib/i18n';
	import { apiTime } from '$lib/time';

	type Props = {
		users: User[];
		// The signed-in user, who can neither end their own sessions nor delete themselves here.
		currentUserId: number | undefined;
		onedit: (user: User) => void;
		onendsessions: (user: User) => void;
		ondelete: (user: User) => void;
	};

	let { users, currentUserId, onedit, onendsessions, ondelete }: Props = $props();

	// The day a user was made, in the language of the page (ADR-0287), without the time: the
	// exact moment is the hover text.
	function formatDay(date: string | null): string {
		const moment = date ? apiTime(date) : null;
		return moment ? moment.toLocaleDateString($intlLocale, { dateStyle: 'medium' }) : $t('common.unknown');
	}

	function formatMoment(date: string | null): string | undefined {
		const moment = date ? apiTime(date) : null;
		return moment ? moment.toLocaleString($intlLocale, { dateStyle: 'long', timeStyle: 'short' }) : undefined;
	}

	function smbHint(user: User): string {
		if (user.smb?.state === 'password_needed') return $t('users.smbPasswordNeeded');
		if (user.smb?.state === 'unavailable') return user.smb.reason ?? '';
		return '';
	}
</script>

<!-- The users, each with their role, two-factor and SMB state, and what can be done to them. A
     flush card: the table reaches its edges (ADR-0428). On a phone each row stacks: the name and
     its actions, then the role and the states under it, so that nothing is off-screen. -->
<Card flush>
	<div class="card-header">
		<h2 class="section-title">
			<Users class="w-5 h-5 text-content-secondary" aria-hidden="true" />
			{$t('users.count', { count: users.length })}
		</h2>
	</div>

	<div class="table-frame">
		<table class="table table-stack">
			<thead class="table-header">
				<tr>
					<th>{$t('common.username')}</th>
					<th>{$t('users.role')}</th>
					<th>{$t('users.twoFactor')}</th>
					<th>{$t('users.smb')}</th>
					<th>{$t('users.created')}</th>
					<th class="text-right"><span class="sr-only">{$t('users.actions')}</span></th>
				</tr>
			</thead>
			<tbody>
				{#each users as user (user.id)}
					<tr class="table-row">
						<td>
							<div class="flex flex-wrap items-center gap-2">
								<span class="font-medium break-all">{user.username}</span>
								{#if currentUserId === user.id}
									<Badge variant="info">{$t('users.you')}</Badge>
								{/if}
							</div>
						</td>
						<td data-label={$t('users.role')}>
							<span class="inline-flex items-center gap-1.5 whitespace-nowrap {user.is_admin ? 'text-content' : 'text-secondary'}">
								{#if user.is_admin}
									<Shield class="w-4 h-4 text-content-secondary" aria-hidden="true" />
									{$t('users.admin')}
								{:else}
									<UserIcon class="w-4 h-4" aria-hidden="true" />
									{$t('users.user')}
								{/if}
							</span>
						</td>
						<td data-label={$t('users.twoFactor')}>
							{#if user.totp_enabled}
								<Badge variant="success" dot>{$t('users.twoFactorOn')}</Badge>
							{:else}
								<Badge variant="neutral">{$t('users.twoFactorOff')}</Badge>
							{/if}
						</td>
						<td data-label={$t('users.smb')}>
							{#if user.smb}
								<SmbStateBadge state={user.smb.state} hint={smbHint(user)} />
							{:else}
								<span class="text-muted">-</span>
							{/if}
						</td>
						<!-- users-created: the cell times.spec.ts reads the moment from. -->
						<td class="users-created stack-hide text-secondary whitespace-nowrap" title={formatMoment(user.created_at)}>
							{formatDay(user.created_at)}
						</td>
						<td class="users-actions">
							<!-- Edit and Delete as icons, the rest in the row's menu (ADR-0432). -->
							<div class="table-actions">
								<Button variant="ghost" size="icon-sm" title={$t('users.editShort')} onclick={() => onedit(user)}>
									<Edit class="w-4 h-4" />
									<span class="sr-only">{$t('users.editUser', { name: user.username })}</span>
								</Button>
								{#if currentUserId !== user.id}
									<Button
										variant="ghost-danger"
										size="icon-sm"
										title={$t('users.deleteShort')}
										onclick={() => ondelete(user)}
									>
										<Trash2 class="w-4 h-4" />
										<span class="sr-only">{$t('users.deleteUser', { name: user.username })}</span>
									</Button>
									<ActionMenu
										name={user.username}
										items={[{ label: $t('users.endSessionsShort'), icon: LogOut, onclick: () => onendsessions(user) }]}
									/>
								{/if}
							</div>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</Card>

<style>
	.users-actions {
		@apply w-px whitespace-nowrap;
	}
</style>
