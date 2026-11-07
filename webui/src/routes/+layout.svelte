<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { navigating, page } from '$app/stores';
	import {
		user,
		isAdminOnlyRoute,
		loadCurrentUser,
		clearCurrentUser
	} from '$lib/stores/auth';
	import { connectWebSocket, disconnectWebSocket } from '$lib/stores/stats';
	import {
		ApiError,
		getToken,
		isPublicRoute,
		onTokenChangedElsewhere,
		setUnauthorizedHandler
	} from '$lib/api/client';
	import { logout, type User } from '$lib/api/auth';
	import { Alert, Button } from '$lib/components/ui';
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
		Archive,
		UserCog,
		ScrollText
	} from 'lucide-svelte';

	const adminNavItems = [
		{ href: '/', icon: LayoutDashboard, label: 'Dashboard' },
		{ href: '/storage', icon: HardDrive, label: 'Storage' },
		{ href: '/snapshots', icon: Archive, label: 'Snapshots' },
		{ href: '/shares', icon: Share2, label: 'Shares' },
		{ href: '/files', icon: FolderOpen, label: 'Files' },
		{ href: '/containers', icon: Container, label: 'Containers' },
		{ href: '/apps', icon: Package, label: 'App Store' },
		{ href: '/users', icon: Users, label: 'Users' },
		{ href: '/audit', icon: ScrollText, label: 'Audit Log' },
		{ href: '/system', icon: Wrench, label: 'System' }
	];

	// Regular accounts only get live system stats and their own account settings;
	// every other API is administrator-only.
	const userNavItems = [
		{ href: '/', icon: LayoutDashboard, label: 'Dashboard' },
		{ href: '/system#security', icon: UserCog, label: 'Account' }
	];

	$: navItems = !$user ? [] : $user.is_admin ? adminNavItems : userNavItems;

	let mounted = false;
	let userLoadError = '';

	type Gate = 'public' | 'allowed' | 'blocked';

	$: pathname = $page.url.pathname;
	// A page renders only once we know who is asking and that they may see it,
	// so admin pages never fire requests on behalf of a regular account.
	$: gate = gateFor(pathname, $user);
	// SvelteKit creates the next page inside the slot shown at that moment and
	// updates $page only afterwards. While navigating to a page with another
	// gate (e.g. from /login to an admin page), show no slot, so the next page
	// is created only once the layout shows the branch and gate for it.
	$: nextPath =
		$navigating && !$navigating.willUnload ? ($navigating.to?.url.pathname ?? null) : null;
	$: showSlot = nextPath === null || gateFor(nextPath, $user) === gate;
	$: if (mounted) guardRoute(pathname, $user);

	function gateFor(path: string, currentUser: User | null): Gate {
		if (isPublicRoute(path)) return 'public';
		if (currentUser && (currentUser.is_admin || !isAdminOnlyRoute(path))) return 'allowed';
		return 'blocked';
	}

	function guardRoute(path: string, currentUser: User | null) {
		if (isPublicRoute(path)) return;
		if (!getToken()) {
			goto('/login', { replaceState: true });
			return;
		}
		if (!currentUser) {
			ensureUser();
			return;
		}
		if (!currentUser.is_admin && isAdminOnlyRoute(path)) {
			goto('/', { replaceState: true });
		}
	}

	async function ensureUser() {
		userLoadError = '';
		try {
			await loadCurrentUser();
			connectWebSocket();
		} catch (e) {
			// 401 already ended the session (see setUnauthorizedHandler below).
			if (e instanceof ApiError && e.status === 401) return;
			if (e instanceof ApiError && e.status === 403) {
				endSession();
				return;
			}
			userLoadError = e instanceof Error ? e.message : 'Failed to load your account';
		}
	}

	function endSession() {
		logout();
		disconnectWebSocket();
		clearCurrentUser();
		goto('/login');
	}

	// Another tab logged out, or logged in again (possibly as another account).
	function handleTokenChange() {
		disconnectWebSocket();
		// Without a user, a page that needs a session is unmounted. The guard
		// then sends this tab to /login, or loads the account of the new token,
		// so the menus and the page follow its role.
		clearCurrentUser();
		guardRoute(pathname, null);
	}

	onMount(() => {
		// Any request (or the stats socket) rejected with 401 lands here.
		setUnauthorizedHandler(endSession);
		const stopWatchingToken = onTokenChangedElsewhere(handleTokenChange);
		mounted = true;
		return () => {
			setUnauthorizedHandler(null);
			stopWatchingToken();
		};
	});

	function handleLogout() {
		endSession();
	}

	// Check if current path matches nav item
	function isActive(href: string, currentPath: string): boolean {
		const target = href.split('#')[0];
		if (target === '/') return currentPath === '/';
		return currentPath === target || currentPath.startsWith(target + '/');
	}
</script>

{#if gate === 'public'}
	{#if showSlot}
		<slot />
	{/if}
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
					{#each navItems as item (item.href)}
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
						<div class="flex items-center gap-2 min-w-0">
							<div
								class="w-8 h-8 rounded-full bg-surface-overlay flex items-center justify-center text-sm font-medium text-content"
							>
								{$user.username.charAt(0).toUpperCase()}
							</div>
							<div class="min-w-0">
								<p class="text-sm text-content-secondary truncate">{$user.username}</p>
								<p class="text-xs text-muted">{$user.is_admin ? 'Administrator' : 'User'}</p>
							</div>
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
			{#if gate === 'allowed' && showSlot}
				<slot />
			{:else if userLoadError}
				<Alert variant="error" title="Could not load your account">
					<p>{userLoadError}</p>
					<Button variant="secondary" size="sm" class="mt-3" on:click={ensureUser}>Retry</Button>
				</Alert>
			{:else}
				<p class="loading-text">Loading...</p>
			{/if}
		</main>
	</div>
{/if}
