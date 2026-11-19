<script lang="ts">
	import { Edit, LogOut, Shield, ShieldOff, Trash2, Users } from 'lucide-svelte';
	import type { User } from '$lib/api/auth';
	import { Badge, Button, Card } from '$lib/components/ui';
	import { SmbStateBadge } from '$lib/components/shares';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';

	type Props = {
		users: User[];
		// The signed-in user, who can neither end their own sessions nor delete themselves here.
		currentUserId: number | undefined;
		onedit: (user: User) => void;
		onendsessions: (user: User) => void;
		ondelete: (user: User) => void;
	};

	let { users, currentUserId, onedit, onendsessions, ondelete }: Props = $props();

	// The texts that the functions below make follow a change of the language: the markup that
	// calls them reads $t and $intlLocale through them. Dates are in the language of the page
	// (ADR-0287).
	function formatDate(date: string | null): string {
		return date ? formatDateTime(date, $intlLocale) : $t('common.unknown');
	}

	function smbHint(user: User): string {
		if (user.smb?.state === 'password_needed') return $t('users.smbPasswordNeeded');
		if (user.smb?.state === 'unavailable') return user.smb.reason ?? '';
		return '';
	}
</script>

<!-- The users, each with their role, two-factor and SMB state, and what can be done to them. -->
<Card>
	<h2 class="section-title mb-4">
		<Users class="w-5 h-5" />
		{$t('users.count', { count: users.length })}
	</h2>

	{#if users.length === 0}
		<p class="text-muted">{$t('users.empty')}</p>
	{:else}
		<div class="table-frame">
			<table class="table">
				<thead class="table-header">
					<tr>
						<th>{$t('common.username')}</th>
						<th>{$t('users.role')}</th>
						<th>{$t('users.twoFactor')}</th>
						<th>{$t('users.smb')}</th>
						<th>{$t('users.created')}</th>
						<th class="text-right">{$t('users.actions')}</th>
					</tr>
				</thead>
				<tbody>
					{#each users as user (user.id)}
						<tr class="table-row">
							<td>
								<div class="flex items-center gap-2">
									<span class="font-medium">{user.username}</span>
									{#if currentUserId === user.id}
										<Badge variant="info">{$t('users.you')}</Badge>
									{/if}
								</div>
							</td>
							<td>
								{#if user.is_admin}
									<span class="flex items-center gap-1 text-warning">
										<Shield class="w-4 h-4" />
										{$t('users.admin')}
									</span>
								{:else}
									<span class="flex items-center gap-1 text-secondary">
										<ShieldOff class="w-4 h-4" />
										{$t('users.user')}
									</span>
								{/if}
							</td>
							<td>
								{#if user.totp_enabled}
									<span class="text-success">{$t('users.twoFactorOn')}</span>
								{:else}
									<span class="text-muted">{$t('users.twoFactorOff')}</span>
								{/if}
							</td>
							<td>
								{#if user.smb}
									<SmbStateBadge state={user.smb.state} hint={smbHint(user)} />
								{:else}
									<span class="text-muted">-</span>
								{/if}
							</td>
							<td class="text-secondary">{formatDate(user.created_at)}</td>
							<td>
								<div class="flex items-center justify-end gap-2">
									<Button variant="secondary" size="icon-sm" onclick={() => onedit(user)}>
										<Edit class="w-4 h-4" />
										<span class="sr-only">{$t('users.editUser', { name: user.username })}</span>
									</Button>
									{#if currentUserId !== user.id}
										<Button variant="ghost" size="icon-sm" onclick={() => onendsessions(user)}>
											<LogOut class="w-4 h-4" />
											<span class="sr-only">{$t('users.endSessionsOf', { name: user.username })}</span>
										</Button>
										<Button variant="ghost" size="icon-sm" onclick={() => ondelete(user)}>
											<Trash2 class="w-4 h-4 text-error" />
											<span class="sr-only">{$t('users.deleteUser', { name: user.username })}</span>
										</Button>
									{/if}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</Card>
