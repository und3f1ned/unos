<script lang="ts">
	import {
		Archive,
		Container,
		FolderOpen,
		HardDrive,
		LayoutDashboard,
		Package,
		ScrollText,
		Share2,
		UserCog,
		Users,
		Wrench
	} from 'lucide-svelte';
	import { t } from '$lib/i18n';
	import { isActive } from './routes';

	// The links of the sidebar: an administrator's pages, or a regular account's (ADR-0030). The
	// command palette offers the same pages, in the same order (palette/choices.ts).

	type Props = {
		/** Whose links: none while the account is unknown or can only set up two-factor authentication. */
		role: 'admin' | 'user' | null;
		/** The path of the page shown: its link is marked. */
		pathname: string;
		/** A link was clicked. */
		onnavigate: () => void;
	};

	let { role, pathname, onnavigate }: Props = $props();

	// Reactive: the labels follow the language of the page.
	const adminNavItems = $derived([
		{ href: '/', icon: LayoutDashboard, label: $t('layout.nav.dashboard') },
		{ href: '/storage', icon: HardDrive, label: $t('layout.nav.storage') },
		{ href: '/snapshots', icon: Archive, label: $t('layout.nav.snapshots') },
		{ href: '/shares', icon: Share2, label: $t('layout.nav.shares') },
		{ href: '/files', icon: FolderOpen, label: $t('layout.nav.files') },
		{ href: '/containers', icon: Container, label: $t('layout.nav.containers') },
		{ href: '/apps', icon: Package, label: $t('layout.nav.apps') },
		{ href: '/users', icon: Users, label: $t('layout.nav.users') },
		{ href: '/audit', icon: ScrollText, label: $t('layout.nav.audit') },
		{ href: '/system', icon: Wrench, label: $t('layout.nav.system') }
	]);

	// Regular accounts only get live system stats and their own account settings;
	// every other API is administrator-only.
	const userNavItems = $derived([
		{ href: '/', icon: LayoutDashboard, label: $t('layout.nav.dashboard') },
		{ href: '/system#security', icon: UserCog, label: $t('layout.nav.account') }
	]);

	const navItems = $derived(role === null ? [] : role === 'admin' ? adminNavItems : userNavItems);
</script>

<nav class="flex-1 p-3 overflow-y-auto scrollbar-thin">
	<ul class="space-y-1">
		{#each navItems as item (item.href)}
			<li>
				<a
					href={item.href}
					onclick={onnavigate}
					class="nav-item {isActive(item.href, pathname) ? 'nav-item-active' : 'nav-item-inactive'}"
				>
					<item.icon class="w-5 h-5" />
					{item.label}
				</a>
			</li>
		{/each}
	</ul>
</nav>
