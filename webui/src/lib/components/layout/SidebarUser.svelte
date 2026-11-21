<script lang="ts">
	import { LogOut } from 'lucide-svelte';
	import type { User } from '$lib/api/auth';
	import { Button, Skeleton } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The foot of the sidebar: who is signed in, and Logout. For an administrator the name is the
	// link to the Account page, "Your account" (ADR-0431); a regular account has that link in the
	// navigation, so its name is no second one. It sits outside the navigation, whose links are the
	// pages. No line says whether the live connection is up (ADR-0438).

	type Props = {
		user: User | null;
		onlogout: () => void;
		/** The account link was clicked: the drawer closes. */
		onnavigate: () => void;
	};

	let { user, onlogout, onnavigate }: Props = $props();
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
	<div class="flex items-center gap-1 px-3 pt-3 pb-3 border-t border-border">
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
	<div class="flex items-center gap-2.5 px-3 pt-3 pb-3 border-t border-border">
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
