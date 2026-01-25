<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { user, isLoggedIn } from '$lib/stores/auth';
	import { connectWebSocket, disconnectWebSocket } from '$lib/stores/stats';
	import { getMe, isAuthenticated, logout } from '$lib/api/auth';
	import {
		LayoutDashboard,
		HardDrive,
		Share2,
		Container,
		LogOut,
		Server,
		Package,
		FolderOpen,
		Users,
		Wrench,
		Archive
	} from 'lucide-svelte';

	// Base navigation items (always visible)
	const baseNavItems = [
		{ href: '/', icon: LayoutDashboard, label: 'Dashboard' },
		{ href: '/storage', icon: HardDrive, label: 'Storage' },
		{ href: '/snapshots', icon: Archive, label: 'Snapshots' },
		{ href: '/shares', icon: Share2, label: 'Shares' },
		{ href: '/files', icon: FolderOpen, label: 'Files' },
		{ href: '/containers', icon: Container, label: 'Containers' },
		{ href: '/apps', icon: Package, label: 'App Store' },
		{ href: '/system', icon: Wrench, label: 'System' }
	];

	// Admin-only navigation items
	const adminNavItems = [{ href: '/users', icon: Users, label: 'Users' }];

	// Reactive nav items based on user role
	$: navItems = $user?.is_admin
		? [...baseNavItems.slice(0, 7), ...adminNavItems, baseNavItems[7]]
		: baseNavItems;

	onMount(async () => {
		if (!isAuthenticated()) {
			if (
				!$page.url.pathname.startsWith('/login') &&
				!$page.url.pathname.startsWith('/setup')
			) {
				goto('/login');
			}
			return;
		}

		try {
			const me = await getMe();
			user.set(me);
			isLoggedIn.set(true);
			connectWebSocket();
		} catch {
			logout();
			goto('/login');
		}
	});

	function handleLogout() {
		logout();
		disconnectWebSocket();
		user.set(null);
		isLoggedIn.set(false);
		goto('/login');
	}

	// Reactive pathname - this ensures menu updates on navigation
	$: pathname = $page.url.pathname;

	// Check if current path matches nav item
	function isActive(href: string, currentPath: string): boolean {
		if (href === '/') return currentPath === '/';
		return currentPath === href || currentPath.startsWith(href + '/');
	}
</script>

{#if $page.url.pathname.startsWith('/login') || $page.url.pathname.startsWith('/setup')}
	<slot />
{:else}
	<div class="flex h-screen bg-surface">
		<!-- Sidebar -->
		<aside class="w-56 bg-surface-elevated flex flex-col border-r border-border">
			<!-- Logo -->
			<div class="p-4 border-b border-border">
				<div class="flex items-center gap-2">
					<Server class="w-7 h-7 text-blue-500" />
					<span class="text-lg font-bold text-content">UNOS</span>
				</div>
			</div>

			<!-- Navigation -->
			<nav class="flex-1 p-3 overflow-y-auto scrollbar-thin">
				<ul class="space-y-1">
					{#each navItems as item}
						<li>
							<a
								href={item.href}
								class="nav-item {isActive(item.href, pathname)
									? 'nav-item-active'
									: 'nav-item-inactive'}"
							>
								<svelte:component this={item.icon} class="w-5 h-5" />
								{item.label}
							</a>
						</li>
					{/each}
				</ul>
			</nav>

			<!-- User section -->
			<div class="p-3 border-t border-border">
				{#if $user}
					<div class="flex items-center justify-between">
						<div class="flex items-center gap-2">
							<div
								class="w-8 h-8 rounded-full bg-surface-overlay flex items-center justify-center text-sm font-medium text-content"
							>
								{$user.username.charAt(0).toUpperCase()}
							</div>
							<span class="text-sm text-content-secondary">{$user.username}</span>
						</div>
						<button
							on:click={handleLogout}
							class="btn btn-ghost btn-icon-sm"
							title="Logout"
						>
							<LogOut class="w-4 h-4" />
						</button>
					</div>
				{/if}
			</div>
		</aside>

		<!-- Main content -->
		<main class="flex-1 overflow-auto bg-surface p-6 scrollbar-thin">
			<slot />
		</main>
	</div>
{/if}
