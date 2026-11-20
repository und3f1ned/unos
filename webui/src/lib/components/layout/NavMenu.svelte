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

	type Link = { href: string; icon: typeof LayoutDashboard; label: string };
	type Group = { id: string; label: string | null; links: Link[] };

	// Reactive: the labels follow the language of the page. The links keep their order, which the
	// command palette lists (ADR-0393); Dashboard stands alone on top, the rest under three group
	// labels (ADR-0428).
	const adminGroups: Group[] = $derived([
		{ id: 'top', label: null, links: [{ href: '/', icon: LayoutDashboard, label: $t('layout.nav.dashboard') }] },
		{
			id: 'data',
			label: $t('layout.group.data'),
			links: [
				{ href: '/storage', icon: HardDrive, label: $t('layout.nav.storage') },
				{ href: '/snapshots', icon: Archive, label: $t('layout.nav.snapshots') },
				{ href: '/shares', icon: Share2, label: $t('layout.nav.shares') },
				{ href: '/files', icon: FolderOpen, label: $t('layout.nav.files') }
			]
		},
		{
			id: 'apps',
			label: $t('layout.group.apps'),
			links: [
				{ href: '/containers', icon: Container, label: $t('layout.nav.containers') },
				{ href: '/apps', icon: Package, label: $t('layout.nav.apps') }
			]
		},
		{
			id: 'admin',
			label: $t('layout.group.admin'),
			links: [
				{ href: '/users', icon: Users, label: $t('layout.nav.users') },
				{ href: '/audit', icon: ScrollText, label: $t('layout.nav.audit') },
				{ href: '/system', icon: Wrench, label: $t('layout.nav.system') }
			]
		}
	]);

	// Regular accounts only get live system stats and their own account settings (the Account
	// page, ADR-0430); every other API is administrator-only. Two links need no groups.
	const userGroups: Group[] = $derived([
		{
			id: 'top',
			label: null,
			links: [
				{ href: '/', icon: LayoutDashboard, label: $t('layout.nav.dashboard') },
				{ href: '/account', icon: UserCog, label: $t('layout.nav.account') }
			]
		}
	]);

	const groups = $derived(role === null ? [] : role === 'admin' ? adminGroups : userGroups);
</script>

<!-- A group label is a paragraph that names its list, not a heading and not a link: `aside nav a`
     is still exactly the pages. -->
<nav class="flex-1 min-h-0 px-3 pt-2 pb-3 overflow-y-auto scrollbar-thin" aria-label={$t('layout.nav.label')}>
	{#each groups as group (group.id)}
		{#if group.label}
			<p id="nav-group-{group.id}" class="eyebrow mx-2.5 mt-[18px] mb-1.5">{group.label}</p>
		{/if}
		<ul class="space-y-0.5" aria-labelledby={group.label ? `nav-group-${group.id}` : undefined}>
			{#each group.links as item (item.href)}
				<li>
					<a
						href={item.href}
						onclick={onnavigate}
						aria-current={isActive(item.href, pathname) ? 'page' : undefined}
						class="nav-item {isActive(item.href, pathname) ? 'nav-item-active' : 'nav-item-inactive'}"
					>
						<item.icon aria-hidden="true" />
						{item.label}
					</a>
				</li>
			{/each}
		</ul>
	{/each}
</nav>
