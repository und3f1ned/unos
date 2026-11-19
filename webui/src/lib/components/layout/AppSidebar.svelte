<script lang="ts">
	import { readable } from 'svelte/store';
	import { on } from 'svelte/events';
	import { Menu, Search, Server, X } from 'lucide-svelte';
	import { browser } from '$app/environment';
	import { afterNavigate } from '$app/navigation';
	import type { User } from '$lib/api/auth';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import NavMenu from './NavMenu.svelte';
	import SidebarUser from './SidebarUser.svelte';

	// The sidebar of a signed-in page. Below Tailwind's md breakpoint its logo row is the header
	// bar, and the rest of it is a drawer under it that the menu button opens (ADR-0302). Escape and
	// a click outside it close it and give the focus back to the button; a link closes it, and the
	// navigation moves the focus to the new page. Back and Forward close it too.
	// The button exists only below md, the same 768 px as the md: classes of the markup. From md
	// the sidebar's first button is Search, which opens the command palette (ADR-0428).

	type Props = {
		/** Whose links the navigation shows (NavMenu). */
		role: 'admin' | 'user' | null;
		/** The path of the page shown. */
		pathname: string;
		user: User | null;
		/** Whether the header bar of a phone offers the command palette (ADR-0393). */
		paletteEnabled: boolean;
		onlogout: () => void;
		/** The Search button, or the header bar's, asks for the command palette, once the drawer has closed. */
		onpalette: () => void;
	};

	// The keys that open the palette, as the system names them.
	const mac = browser && /Mac|iPhone|iPad|iPod/.test(navigator.platform);
	// i18n-ignore: the names of keys, the same in every language
	const keyHint = mac ? '⌘ K' : 'Ctrl K';
	// i18n-ignore: a key combination of ARIA, not a text
	const keyShortcut = mac ? 'Meta+K' : 'Control+K';

	let { role, pathname, user, paletteEnabled, onlogout, onpalette }: Props = $props();

	const narrow = readable(false, (set) => {
		if (!browser) return;
		// i18n-ignore: a CSS media query, the same in every language
		const query = window.matchMedia('(max-width: 767.98px)');
		const update = () => set(query.matches);
		update();
		query.addEventListener('change', update);
		return () => query.removeEventListener('change', update);
	});
	let menuOpen = $state(false);
	// The menu button and the drawer, for the handlers; state, as both come and go with a branch
	// of the markup.
	let menuButton = $state<HTMLButtonElement | null>(null);
	let sidebar = $state<HTMLElement>();

	// A window that grows past md leaves the drawer closed, in the same update.
	$effect.pre(() => {
		if (!$narrow) menuOpen = false;
	});

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
		if ((sidebar && path.includes(sidebar)) || (menuButton && path.includes(menuButton))) return;
		// A click in a dialog over the drawer, the command palette's (ADR-0393), is the dialog's.
		if (path.some((target) => target instanceof Element && target.getAttribute('aria-modal') === 'true')) return;
		closeMenu(true);
	}

	// The backdrop's own listener on the element (ADR-0427): Svelte 5 hands an onclick attribute to
	// a listener on the app's root, which a click that does not bubble (iOS Safari's tap on an
	// element that listens itself) never reaches.
	function closeOnClick(node: HTMLElement) {
		return on(node, 'click', () => closeMenu(true));
	}

	// Tabbing out of the drawer closes it, so that it never covers the element with the focus. A
	// dialog that opens over it, the command palette's (ADR-0393), covers the drawer instead: the
	// drawer stays, and the focus comes back to it when the dialog closes.
	function handleSidebarFocusOut(event: FocusEvent) {
		const next = event.relatedTarget;
		if (!menuOpen || !(next instanceof Element)) return;
		if (sidebar?.contains(next) || menuButton?.contains(next) || next.closest('[aria-modal="true"]')) return;
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
		if (menuOpen) closeMenu(sidebar?.contains(document.activeElement) ?? false);
	}
</script>

<svelte:window onkeydown={handleMenuKeydown} onclick={handleOutsideClick} onpopstate={handlePopState} />

<!-- Sidebar. Below md its logo row is the header bar, with the menu button, and the rest of
     it is the drawer that the button opens over the page (ADR-0302). From md it is 224 px wide,
     from lg 248 px (ADR-0428). -->
<aside
	class="relative shrink-0 md:w-56 lg:w-[248px] bg-surface-elevated flex flex-col border-b md:border-b-0 md:border-r border-border"
>
	<!-- Logo -->
	<div class="flex items-center gap-2.5 h-14 shrink-0 px-2 md:px-4">
		{#if $narrow}
			<Button
				variant="ghost"
				size="icon"
				bind:el={menuButton}
				type="button"
				aria-label={$t('layout.menu')}
				aria-expanded={menuOpen}
				aria-controls="layout-sidebar"
				onclick={() => (menuOpen = !menuOpen)}
			>
				{#if menuOpen}
					<X class="w-5 h-5" />
				{:else}
					<Menu class="w-5 h-5" />
				{/if}
			</Button>
		{/if}
		<span
			class="grid place-items-center w-7 h-7 shrink-0 rounded-[7px] bg-brand text-content-on-brand"
			aria-hidden="true"
		>
			<Server class="w-4 h-4" />
		</span>
		<span class="text-base font-bold tracking-[0.04em] text-content">{$t('common.product')}</span>
	</div>

	{#if menuOpen}
		<!-- Covers the page beside the drawer, which a click on it never reaches; it listens
		     for the click itself (closeOnClick, and handleOutsideClick for the rest). -->
		<div
			class="md:hidden fixed inset-x-0 top-14 bottom-0 z-30 scrim"
			role="presentation"
			{@attach closeOnClick}
		></div>
	{/if}

	<!-- Below md a drawer that slides in (reduced motion: at once); closed, it is invisible, so that
	     neither Tab nor a screen reader reaches it. It is visible as soon as it opens, and turns
	     invisible only once it has slid out. -->
	<div
		id="layout-sidebar"
		bind:this={sidebar}
		onfocusout={handleSidebarFocusOut}
		class="{menuOpen
			? 'visible translate-x-0 transition-transform'
			: 'invisible -translate-x-full transition-[transform,visibility]'} md:visible md:translate-x-0 flex flex-1 min-h-0 flex-col fixed md:static top-14 bottom-0 left-0 z-40 w-72 max-w-[85vw] md:w-auto md:max-w-none bg-surface-elevated border-r md:border-r-0 border-border duration-[180ms] ease-[cubic-bezier(0.2,0,0,1)] md:transition-none"
	>
		<!-- From md: the command palette's Search button, before the links (ADR-0428). Its name
		     starts with its visible word; the keys' hint is for the eye. -->
		{#if paletteEnabled}
			<div class="hidden md:block px-3 pt-1 pb-1">
				<Button
					variant="plain"
					type="button"
					aria-label={$t('palette.field')}
					aria-keyshortcuts={keyShortcut}
					onclick={onpalette}
					class="w-full h-9 flex items-center gap-2 pl-2.5 pr-1.5 rounded border border-border bg-surface text-[13px] text-content-muted hover:border-border-strong hover:text-content-secondary transition-colors"
				>
					<Search class="w-4 h-4 shrink-0" aria-hidden="true" />
					<span class="flex-1 min-w-0 truncate text-left">{$t('palette.trigger')}</span>
					<kbd aria-hidden="true">{keyHint}</kbd>
				</Button>
			</div>
		{/if}
		<NavMenu {role} {pathname} onnavigate={() => closeMenu(false)} />
		<SidebarUser {user} {onlogout} onnavigate={() => closeMenu(false)} />
	</div>

	<!-- A phone has no Ctrl+K: this button of the header bar opens the command palette there
	     (ADR-0393). It comes after the drawer, so that the links follow the menu button in the
	     tab order, and sits at the right end of the bar. -->
	{#if $narrow && paletteEnabled}
		<div class="absolute top-0 right-0 h-14 flex items-center px-2">
			<Button
				variant="ghost"
				size="icon"
				type="button"
				aria-label={$t('palette.open')}
				onclick={() => {
					// The drawer closes first: else its click outside would take the focus back.
					menuOpen = false;
					onpalette();
				}}
			>
				<Search class="w-5 h-5" />
			</Button>
		</div>
	{/if}
</aside>
