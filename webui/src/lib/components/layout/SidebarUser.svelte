<script lang="ts">
	import { LogOut } from 'lucide-svelte';
	import type { User } from '$lib/api/auth';
	import { Button, Skeleton, StatusDot } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { wsConnected, wsForbidden, wsSettled } from '$lib/stores/stats';

	// The foot of the sidebar: whether the live connection is up, who is signed in, and Logout. For
	// an administrator the name is the link to the Account page, "Your account" (ADR-0431); a
	// regular account has that link in the navigation, so its name is no second one. It sits
	// outside the navigation, whose links are the pages.

	type Props = {
		user: User | null;
		onlogout: () => void;
		/** The account link was clicked: the drawer closes. */
		onnavigate: () => void;
	};

	let { user, onlogout, onnavigate }: Props = $props();

	// The events socket is up once the server has taken its credentials. Until its first attempt has
	// ended (up, refused, or closed before it was up: wsSettled, ADR-0430), the line says it connects
	// and is no live region: a screen reader hears of the connection only when it changes
	// afterwards, not at every page load. A socket that never comes up is then "Not connected", as
	// the dashboard says "Disconnected from the NAS".
	const connected = $derived($wsConnected);
	const settled = $derived($wsSettled || $wsConnected || $wsForbidden);
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
	{#snippet line(text: string, dot: 'success' | 'warning' | 'neutral')}
		<StatusDot status={dot} />
		{text}
	{/snippet}
	{#if !settled}
		<div class="flex items-center gap-2 px-[22px] py-2.5 border-t border-border text-xs text-content-secondary">
			{@render line($t('layout.connecting'), 'neutral')}
		</div>
	{:else}
		<!-- A polite live region that speaks when the state changes, and only then. No role status:
		     a page's own status (a saved setting, a count) stays the page's one status. -->
		<div
			class="flex items-center gap-2 px-[22px] py-2.5 border-t border-border text-xs text-content-secondary"
			aria-live="polite"
			aria-atomic="true"
		>
			{#if $wsForbidden}
				{@render line($t('layout.noLiveData'), 'neutral')}
			{:else}
				{@render line(
					connected ? $t('layout.connected') : $t('layout.notConnected'),
					connected ? 'success' : 'warning'
				)}
			{/if}
		</div>
	{/if}
	<div class="flex items-center gap-1 px-3 pt-1 pb-3">
		{#if user.is_admin}
			<a
				href="/account"
				title={$t('layout.yourAccount')}
				onclick={onnavigate}
				class="relative flex-1 min-w-0 flex items-center gap-2.5 p-1.5 rounded hover:bg-surface-overlay"
			>
				{@render who(user)}
				<!-- Where the link goes, for a screen reader: the title shows only under a mouse. -->
				<span class="sr-only">{`, ${$t('layout.yourAccount')}`}</span>
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
{:else}
	<!-- The account is not known yet (ADR-0430): the block's shape, as tall as it will be. -->
	<div class="flex items-center gap-2 px-[22px] py-2.5 border-t border-border">
		<Skeleton size="xs" class="w-24" />
	</div>
	<div class="flex items-center gap-2.5 px-3 pt-1 pb-3">
		<div class="flex-1 min-w-0 flex items-center gap-2.5 p-1.5">
			<Skeleton shape="circle" />
			<span class="flex-1 min-w-0">
				<Skeleton class="w-20" />
				<Skeleton size="xs" class="w-24" />
			</span>
		</div>
		<Skeleton shape="block" class="w-8 h-8" />
	</div>
{/if}
