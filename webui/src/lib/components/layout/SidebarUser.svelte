<script lang="ts">
	import { LogOut } from 'lucide-svelte';
	import type { User } from '$lib/api/auth';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { wsConnected } from '$lib/stores/stats';

	// The foot of the sidebar: whether the live connection is up, who is signed in, and Logout. An
	// administrator's own password, two-factor authentication and theme are in System > Security:
	// the name is a link there, "Your account" (ADR-0428). It sits outside the navigation, whose
	// links are the pages.

	type Props = {
		user: User | null;
		onlogout: () => void;
		/** The account link was clicked: the drawer closes. */
		onnavigate: () => void;
	};

	let { user, onlogout, onnavigate }: Props = $props();

	// The events socket is up once the server has taken its credentials.
	const connected = $derived($wsConnected);
</script>

{#snippet who(user: User)}
	<span
		class="w-8 h-8 shrink-0 rounded-full bg-brand/20 grid place-items-center text-sm font-semibold text-content"
		aria-hidden="true"
	>
		{user.username.charAt(0).toUpperCase()}
	</span>
	<span class="min-w-0">
		<span class="block text-sm font-medium text-content truncate">{user.username}</span>
		<span class="block text-xs text-muted">{user.is_admin ? $t('layout.role.admin') : $t('layout.role.user')}</span>
	</span>
{/snippet}

{#if user}
	<!-- A polite status: it speaks when the state changes, and only then. -->
	<div
		class="flex items-center gap-2 px-[22px] py-2.5 border-t border-border text-xs text-content-secondary"
		role="status"
	>
		<span
			class="status-dot {connected ? 'status-dot-success' : 'status-dot-neutral'}"
			aria-hidden="true"
		></span>
		{connected ? $t('layout.connected') : $t('layout.notConnected')}
	</div>
	<div class="flex items-center gap-1 px-3 pt-1 pb-3">
		{#if user.is_admin}
			<a
				href="/system#security"
				title={$t('layout.yourAccount')}
				onclick={onnavigate}
				class="flex-1 min-w-0 flex items-center gap-2.5 p-1.5 rounded hover:bg-surface-overlay"
			>
				{@render who(user)}
			</a>
		{:else}
			<div class="flex-1 min-w-0 flex items-center gap-2.5 p-1.5">
				{@render who(user)}
			</div>
		{/if}
		<Button variant="ghost" size="icon-sm" onclick={onlogout} title={$t('layout.logout')}>
			<LogOut class="w-4 h-4" />
		</Button>
	</div>
{/if}
