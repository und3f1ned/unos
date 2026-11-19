<script lang="ts">
	import '../app.css';
	import { onMount, untrack, type Snippet } from 'svelte';
	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { navigating, page } from '$app/state';
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
	import AppSidebar from '$lib/components/layout/AppSidebar.svelte';
	import { gateFor } from '$lib/components/layout/routes';
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

	type Props = {
		children?: Snippet;
	};

	let { children }: Props = $props();

	// An account that can only set up two-factor authentication gets its setup page alone.
	const setupOnly = $derived(isSetupOnly($user));
	// The theme that the signed-in user chose (ADR-0405), once per session; an account that can only
	// set up two-factor authentication cannot ask, and keeps the theme of this browser. Whoever
	// signs in (this layout, the sign-in page, another tab), $user tells it.
	$effect(() => {
		if (browser && $user && !setupOnly) untrack(() => void loadAccountTheme());
	});
	// Whose links the sidebar shows.
	const navRole = $derived(!$user || setupOnly ? null : $user.is_admin ? 'admin' : 'user');

	let mounted = $state(false);
	let userLoadError = $state('');

	const pathname = $derived(page.url.pathname);
	// A page renders only once we know who is asking and that they may see it,
	// so admin pages never fire requests on behalf of a regular account.
	const gate = $derived(gateFor(pathname, $user));
	// SvelteKit gives the layout the next page and updates page.url in the same update, once the
	// page has loaded. While navigating to a page with another gate (e.g. from /login to an admin
	// page), show no page, so that the old one goes and the next one is created only once the
	// layout shows the branch and gate for it.
	const nextPath = $derived(navigating.to && !navigating.willUnload ? navigating.to.url.pathname : null);
	const showSlot = $derived(nextPath === null || gateFor(nextPath, $user) === gate);
	// Once mounted, on every change of the path or of the account: the handlers of the session are
	// set by then. Only those make it run again: what guardRoute reads and writes is its own business.
	$effect(() => {
		if (!mounted) return;
		const path = pathname;
		const currentUser = $user;
		untrack(() => guardRoute(path, currentUser));
	});
	// The command palette (ADR-0393) is a signed-in page's, as the navigation is: not the sign-in
	// and first-run pages', nor that of an account that can only set up two-factor authentication.
	// Its exports (show), kept as they come.
	let commandPalette = $state.raw<CommandPalette>();
	const paletteEnabled = $derived(gate === 'allowed' && $user !== null && !setupOnly);

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
</script>

{#if gate === 'public'}
	{#if showSlot}
		{@render children?.()}
	{/if}
{:else}
	<div class="flex flex-col md:flex-row h-dvh bg-surface">
		<AppSidebar
			role={navRole}
			{pathname}
			user={$user}
			{paletteEnabled}
			onlogout={handleLogout}
			onpalette={() => commandPalette?.show()}
		/>

		<!-- Main content -->
		<main class="flex-1 overflow-auto bg-surface p-4 md:p-6 scrollbar-thin">
			<!-- The problems of the NAS, above every page of an administrator (ADR-0332). -->
			{#if gate === 'allowed' && $isAdmin && !setupOnly}
				<ProblemsBanner />
			{/if}
			{#if gate === 'allowed' && showSlot}
				{@render children?.()}
			{:else if userLoadError}
				<Alert variant="error" title={$t('layout.accountError')}>
					<p>{userLoadError}</p>
					<Button variant="secondary" size="sm" class="mt-3" onclick={ensureUser}>{$t('common.retry')}</Button>
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
		onlogout={handleLogout}
	/>
{/if}
