<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { readable } from 'svelte/store';
	import { browser } from '$app/environment';
	import { afterNavigate, goto } from '$app/navigation';
	import { navigating, page } from '$app/stores';
	import {
		user,
		isAdmin,
		isAdminOnlyRoute,
		isSetupOnly,
		loadCurrentUser,
		clearCurrentUser,
		signOut,
		TWO_FACTOR_SETUP_PATH
	} from '$lib/stores/auth';
	import { connectWebSocket, disconnectWebSocket } from '$lib/stores/stats';
	import { applyTheme, loadAccountTheme } from '$lib/stores/theme';
	import ProblemsBanner from '$lib/components/ProblemsBanner.svelte';
	import CommandPalette from '$lib/components/palette/CommandPalette.svelte';
	import {
		ApiError,
		isPublicRoute,
		knownSignedOut,
		obtainSessionKey,
		onSessionChangedElsewhere,
		sessionStopped,
		setSetupRequiredHandler,
		setUnauthorizedHandler
	} from '$lib/api/client';
	import type { User } from '$lib/api/auth';
	import { Alert, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
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
		ScrollText,
		Menu,
		Search,
		X
	} from 'lucide-svelte';

	// Reactive: the labels follow the language of the page.
	$: adminNavItems = [
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
	];

	// Regular accounts only get live system stats and their own account settings;
	// every other API is administrator-only.
	$: userNavItems = [
		{ href: '/', icon: LayoutDashboard, label: $t('layout.nav.dashboard') },
		{ href: '/system#security', icon: UserCog, label: $t('layout.nav.account') }
	];

	// An account that can only set up two-factor authentication gets its setup page alone.
	$: setupOnly = isSetupOnly($user);
	// The theme that the signed-in user chose (ADR-0405), once per session; an account that can only
	// set up two-factor authentication cannot ask, and keeps the theme of this browser.
	$: if (browser && $user && !setupOnly) void loadAccountTheme();
	$: navItems = !$user || setupOnly ? [] : $user.is_admin ? adminNavItems : userNavItems;

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
	// The command palette (ADR-0393) is a signed-in page's, as the navigation is: not the sign-in
	// and first-run pages', nor that of an account that can only set up two-factor authentication.
	let commandPalette: CommandPalette;
	$: paletteEnabled = gate === 'allowed' && $user !== null && !setupOnly;

	function gateFor(path: string, currentUser: User | null): Gate {
		if (isPublicRoute(path)) return 'public';
		if (isSetupOnly(currentUser)) return path === TWO_FACTOR_SETUP_PATH ? 'allowed' : 'blocked';
		if (currentUser && (currentUser.is_admin || !isAdminOnlyRoute(path))) return 'allowed';
		return 'blocked';
	}

	function guardRoute(path: string, currentUser: User | null) {
		if (isPublicRoute(path)) return;
		// The page cannot read the session cookie (ADR-0397): the account comes from the server,
		// and a 401 leads to the sign-in page. Once the session has ended in this tab, nothing asks.
		if (!currentUser) {
			if (knownSignedOut()) goto('/login', { replaceState: true });
			else ensureUser();
			return;
		}
		if (isSetupOnly(currentUser)) {
			if (path !== TWO_FACTOR_SETUP_PATH) goto(TWO_FACTOR_SETUP_PATH, { replaceState: true });
			return;
		}
		if (!currentUser.is_admin && isAdminOnlyRoute(path)) {
			goto('/', { replaceState: true });
		}
	}

	async function ensureUser() {
		userLoadError = '';
		// A new tab asks the others for the session key; without one there is no session to go on
		// with (ADR-0397).
		if (!(await obtainSessionKey())) {
			endSession();
			return;
		}
		try {
			const me = await loadCurrentUser();
			// No socket serves an account that can only set up two-factor authentication.
			if (!isSetupOnly(me)) connectWebSocket();
		} catch (e) {
			// 401 already ended the session (see setUnauthorizedHandler below).
			if (e instanceof ApiError && e.status === 401) return;
			if (e instanceof ApiError && e.status === 403) {
				endSession();
				return;
			}
			userLoadError = e instanceof Error ? e.message : $t('layout.accountFailed');
		}
	}

	// The server ended the session (401) or refused the account (403): nothing is left to end.
	function endSession() {
		sessionStopped();
		disconnectWebSocket();
		clearCurrentUser();
		goto('/login');
	}

	// Another tab signed out, or signed in again (possibly as another account): the tabs share
	// the session cookie.
	function handleSessionChange() {
		disconnectWebSocket();
		// Without a user, a page that needs a session is unmounted. The guard
		// then loads the account of the new session, so the menus and the page
		// follow its role; after a sign-out it sends this tab to /login.
		clearCurrentUser();
		guardRoute(pathname, null);
	}

	// A request or the events socket refused because the grace period to set up two-factor
	// authentication has ended meanwhile: the account, loaded again, says so, and the guard leads to
	// the setup page. An account that has its rights after all (2FA set up in another tab, the
	// requirement turned off) gets its live data again.
	function handleSetupRequired() {
		disconnectWebSocket();
		loadCurrentUser()
			.then((me) => {
				if (!isSetupOnly(me)) connectWebSocket();
			})
			.catch(() => {
				// A 401 ended the session; anything else leaves the page as it is.
			});
	}

	// Every page, the sign-in and first-run pages too, shows the theme of the theme store.
	onMount(applyTheme);

	onMount(() => {
		// Any request (or the stats socket) rejected with 401 lands here.
		setUnauthorizedHandler(endSession);
		setSetupRequiredHandler(handleSetupRequired);
		const stopWatchingSession = onSessionChangedElsewhere(handleSessionChange);
		mounted = true;
		return () => {
			setUnauthorizedHandler(null);
			setSetupRequiredHandler(null);
			stopWatchingSession();
		};
	});

	function handleLogout() {
		disconnectWebSocket();
		void signOut();
		goto('/login');
	}

	// Check if current path matches nav item
	function isActive(href: string, currentPath: string): boolean {
		const target = href.split('#')[0];
		if (target === '/') return currentPath === '/';
		return currentPath === target || currentPath.startsWith(target + '/');
	}

	// Below Tailwind's md breakpoint the sidebar's logo row is the header bar, and the rest of the
	// sidebar is a drawer under it that the menu button opens (ADR-0302). Escape and a click
	// outside it close it and give the focus back to the button; a link closes it, and the
	// navigation moves the focus to the new page. Back and Forward close it too.
	// The button exists only below md, the same 768 px as the md: classes of the markup: from md
	// up the sidebar is the one it was, its first button Logout.
	const narrow = readable(false, (set) => {
		if (!browser) return;
		// i18n-ignore: a CSS media query, the same in every language
		const query = window.matchMedia('(max-width: 767.98px)');
		const update = () => set(query.matches);
		update();
		query.addEventListener('change', update);
		return () => query.removeEventListener('change', update);
	});
	let menuOpen = false;
	let menuButton: HTMLButtonElement | null = null;
	let sidebar: HTMLElement;

	// A window that grows past md leaves the drawer closed.
	$: if (!$narrow) menuOpen = false;

	function closeMenu(focusButton: boolean) {
		menuOpen = false;
		if (focusButton) menuButton?.focus();
	}

	function handleMenuKeydown(event: KeyboardEvent) {
		// A key that an open dialog has used (Modal marks its Escape) is not the menu's, nor is one
		// while a dialog is open over the drawer, such as the command palette (ADR-0393), whose
		// listener can come after this one.
		if (!menuOpen || event.key !== 'Escape' || event.defaultPrevented || overDialog()) return;
		event.preventDefault();
		closeMenu(true);
	}

	// A click beside the drawer lands on its backdrop, which closes the menu itself: iOS Safari
	// makes a tap a click only on an element that listens for clicks, and the window does not
	// count. The window still sees the clicks elsewhere outside the drawer, such as on the header
	// bar.
	function handleOutsideClick(event: MouseEvent) {
		if (!menuOpen) return;
		// The path as it was when the click happened: the button's icon that was clicked has been
		// replaced by the time the click reaches the window.
		const path = event.composedPath();
		if (path.includes(sidebar) || (menuButton && path.includes(menuButton))) return;
		// A click in a dialog over the drawer, the command palette's (ADR-0393), is the dialog's.
		if (path.some((target) => target instanceof Element && target.getAttribute('aria-modal') === 'true')) return;
		closeMenu(true);
	}

	// Tabbing out of the drawer closes it, so that it never covers the element with the focus. A
	// dialog that opens over it, the command palette's (ADR-0393), covers the drawer instead: the
	// drawer stays, and the focus comes back to it when the dialog closes.
	function handleSidebarFocusOut(event: FocusEvent) {
		const next = event.relatedTarget;
		if (!menuOpen || !(next instanceof Element)) return;
		if (sidebar.contains(next) || menuButton?.contains(next) || next.closest('[aria-modal="true"]')) return;
		closeMenu(false);
	}

	function overDialog(): boolean {
		return document.querySelector('[aria-modal="true"]') !== null;
	}

	// Also after Back and Forward, which change the page without a link of the menu.
	afterNavigate(() => {
		menuOpen = false;
	});

	// Back and Forward between two places of one page, such as System's #updates and #general,
	// change the URL without a navigation of SvelteKit, so afterNavigate misses them. A closed
	// drawer keeps no focus: it goes back to the button.
	function handlePopState() {
		if (menuOpen) closeMenu(sidebar.contains(document.activeElement));
	}
</script>

<svelte:window on:keydown={handleMenuKeydown} on:click={handleOutsideClick} on:popstate={handlePopState} />

{#if gate === 'public'}
	{#if showSlot}
		<slot />
	{/if}
{:else}
	<div class="flex flex-col md:flex-row h-dvh bg-surface">
		<!-- Sidebar. Below md its logo row is the header bar, with the menu button, and the rest of
		     it is the drawer that the button opens over the page (ADR-0302). -->
		<aside class="relative shrink-0 md:w-56 bg-surface-elevated flex flex-col md:border-r border-border">
			<!-- Logo -->
			<div class="flex items-center gap-2 h-14 px-2 md:h-auto md:p-4 border-b border-border">
				{#if $narrow}
					<Button variant="ghost" size="icon"
						bind:el={menuButton}
						type="button"
						aria-label={$t('layout.menu')}
						aria-expanded={menuOpen}
						aria-controls="layout-sidebar"
						on:click={() => (menuOpen = !menuOpen)}
					>
						{#if menuOpen}
							<X class="w-5 h-5" />
						{:else}
							<Menu class="w-5 h-5" />
						{/if}
					</Button>
				{/if}
				<Server class="w-7 h-7 text-brand-ring" />
				<span class="text-lg font-bold text-content">{$t('common.product')}</span>
			</div>

			{#if menuOpen}
				<!-- Covers the page beside the drawer, which a click on it never reaches; it listens
				     for the click itself (see handleOutsideClick). -->
				<div
					class="md:hidden fixed inset-x-0 top-14 bottom-0 z-30 scrim"
					role="presentation"
					on:click={() => closeMenu(true)}
				></div>
			{/if}

			<div
				id="layout-sidebar"
				bind:this={sidebar}
				on:focusout={handleSidebarFocusOut}
				class="{menuOpen
					? 'flex'
					: 'hidden'} md:flex flex-1 min-h-0 flex-col fixed md:static top-14 bottom-0 left-0 z-40 w-64 md:w-auto bg-surface-elevated border-r md:border-r-0 border-border"
			>
				<!-- Navigation -->
				<nav class="flex-1 p-3 overflow-y-auto scrollbar-thin">
					<ul class="space-y-1">
						{#each navItems as item (item.href)}
							<li>
								<a
									href={item.href}
									on:click={() => closeMenu(false)}
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
									<p class="text-xs text-muted">{$user.is_admin ? $t('layout.role.admin') : $t('layout.role.user')}</p>
								</div>
							</div>
							<Button variant="ghost" size="icon-sm"
								on:click={handleLogout}
								title={$t('layout.logout')}
							>
								<LogOut class="w-4 h-4" />
							</Button>
						</div>
					{/if}
				</div>
			</div>

			<!-- A phone has no Ctrl+K: this button of the header bar opens the command palette there
			     (ADR-0393). It comes after the drawer, so that the links follow the menu button in the
			     tab order, and sits at the right end of the bar. -->
			{#if $narrow && paletteEnabled}
				<div class="absolute top-0 right-0 h-14 flex items-center px-2">
					<Button variant="ghost" size="icon"
						type="button"
						aria-label={$t('palette.open')}
						on:click={() => {
							// The drawer closes first: else its click outside would take the focus back.
							menuOpen = false;
							commandPalette.show();
						}}
					>
						<Search class="w-5 h-5" />
					</Button>
				</div>
			{/if}
		</aside>

		<!-- Main content -->
		<main class="flex-1 overflow-auto bg-surface p-4 md:p-6 scrollbar-thin">
			<!-- The problems of the NAS, above every page of an administrator (ADR-0332). -->
			{#if gate === 'allowed' && $isAdmin && !setupOnly}
				<ProblemsBanner />
			{/if}
			{#if gate === 'allowed' && showSlot}
				<slot />
			{:else if userLoadError}
				<Alert variant="error" title={$t('layout.accountError')}>
					<p>{userLoadError}</p>
					<Button variant="secondary" size="sm" class="mt-3" on:click={ensureUser}>{$t('common.retry')}</Button>
				</Alert>
			{:else}
				<p class="loading-text">{$t('common.loading')}</p>
			{/if}
		</main>
	</div>
	<CommandPalette
		bind:this={commandPalette}
		enabled={paletteEnabled}
		admin={$user?.is_admin === true}
		on:logout={handleLogout}
	/>
{/if}
