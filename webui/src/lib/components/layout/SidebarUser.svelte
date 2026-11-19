<script lang="ts">
	import { LogOut } from 'lucide-svelte';
	import type { User } from '$lib/api/auth';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The foot of the sidebar: who is signed in, and Logout.

	type Props = {
		user: User | null;
		onlogout: () => void;
	};

	let { user, onlogout }: Props = $props();
</script>

<div class="p-3 border-t border-border">
	{#if user}
		<div class="flex items-center justify-between">
			<div class="flex items-center gap-2 min-w-0">
				<div
					class="w-8 h-8 rounded-full bg-surface-overlay flex items-center justify-center text-sm font-medium text-content"
				>
					{user.username.charAt(0).toUpperCase()}
				</div>
				<div class="min-w-0">
					<p class="text-sm text-content-secondary truncate">{user.username}</p>
					<p class="text-xs text-muted">{user.is_admin ? $t('layout.role.admin') : $t('layout.role.user')}</p>
				</div>
			</div>
			<Button variant="ghost" size="icon-sm" onclick={onlogout} title={$t('layout.logout')}>
				<LogOut class="w-4 h-4" />
			</Button>
		</div>
	{/if}
</div>
