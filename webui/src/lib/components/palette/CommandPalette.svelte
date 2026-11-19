<script lang="ts">
	// The command palette (ADR-0393): Ctrl+K, or Cmd+K, opens it over a signed-in page; its field
	// narrows the pages and actions of the user's role, and Enter opens the chosen page or starts
	// the chosen action. It acts itself only where nothing on the NAS changes (the language of this
	// browser, Logout); every other action opens its page's own dialog, which asks first. It asks
	// the server for nothing: its choices come from the role the layout has already loaded and from
	// the catalogs.
	import { tick, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { afterNavigate, goto } from '$app/navigation';
	import {
		Archive,
		ArrowUpCircle,
		Bell,
		Calendar,
		Container,
		Database,
		FileText,
		FolderOpen,
		Gauge,
		Globe,
		HardDrive,
		Heart,
		Languages,
		LayoutDashboard,
		LogOut,
		Moon,
		Network,
		Package,
		Power,
		RotateCcw,
		ScrollText,
		Settings,
		Share2,
		Shield,
		Trash2,
		UserCog,
		UserPlus,
		Users,
		Wrench
	} from 'lucide-svelte';
	import { Input, Modal, type IconComponent } from '$lib/components/ui';
	import { LANGUAGES, locale, t, translateIn, type Locale } from '$lib/i18n';
	import { paletteRequest, type PaletteDialog } from '$lib/stores/palette';
	import { narrow, type Findable } from './match';

	type Props = {
		/** Whether the palette may open: a signed-in page of an account that has its rights. */
		enabled?: boolean;
		/** An administrator gets every page and action; a regular user the Dashboard and Account (ADR-0030). */
		admin?: boolean;
		/** Logout was chosen: the layout signs out. */
		onlogout?: () => void;
	};

	let { enabled = false, admin = false, onlogout }: Props = $props();

	/** What a choice does: open a page, open a page's dialog, set the language, or log out. */
	type Run =
		| { go: string; dialog?: PaletteDialog }
		| { language: Locale }
		| { logout: true };

	interface Choice {
		id: string;
		nameKey: string;
		/** The other words the choice is known by, in the catalog of the palette. */
		keywordsKey?: string;
		kindKey: string;
		icon: IconComponent;
		run: Run;
	}

	const PAGE = 'palette.kind.page';
	const SYSTEM = 'layout.nav.system';
	const ACTION = 'palette.kind.action';

	// The pages of the sidebar of +layout.svelte, with the trash of Files, and the tabs of the System
	// page (src/routes/system/+page.svelte), in their order: webui/tests/command-palette.spec.ts
	// compares them.
	const ADMIN_PAGES: Choice[] = [
		{ id: 'dashboard', nameKey: 'layout.nav.dashboard', kindKey: PAGE, icon: LayoutDashboard, run: { go: '/' } },
		{
			id: 'storage',
			nameKey: 'layout.nav.storage',
			keywordsKey: 'palette.keywords.storage',
			kindKey: PAGE,
			icon: HardDrive,
			run: { go: '/storage' }
		},
		{ id: 'snapshots', nameKey: 'layout.nav.snapshots', kindKey: PAGE, icon: Archive, run: { go: '/snapshots' } },
		{
			id: 'shares',
			nameKey: 'layout.nav.shares',
			keywordsKey: 'palette.keywords.shares',
			kindKey: PAGE,
			icon: Share2,
			run: { go: '/shares' }
		},
		{ id: 'files', nameKey: 'layout.nav.files', kindKey: PAGE, icon: FolderOpen, run: { go: '/files' } },
		{ id: 'trash', nameKey: 'files.trash.title', kindKey: PAGE, icon: Trash2, run: { go: '/files/trash' } },
		{
			id: 'containers',
			nameKey: 'layout.nav.containers',
			keywordsKey: 'palette.keywords.containers',
			kindKey: PAGE,
			icon: Container,
			run: { go: '/containers' }
		},
		{
			id: 'apps',
			nameKey: 'layout.nav.apps',
			keywordsKey: 'palette.keywords.apps',
			kindKey: PAGE,
			icon: Package,
			run: { go: '/apps' }
		},
		{
			id: 'users',
			nameKey: 'layout.nav.users',
			keywordsKey: 'palette.keywords.users',
			kindKey: PAGE,
			icon: Users,
			run: { go: '/users' }
		},
		{ id: 'audit', nameKey: 'layout.nav.audit', kindKey: PAGE, icon: ScrollText, run: { go: '/audit' } },
		{ id: 'system', nameKey: 'layout.nav.system', kindKey: PAGE, icon: Wrench, run: { go: '/system' } },
		{
			id: 'general',
			nameKey: 'system.tabs.general',
			keywordsKey: 'palette.keywords.general',
			kindKey: SYSTEM,
			icon: Settings,
			run: { go: '/system#general' }
		},
		{
			id: 'updates',
			nameKey: 'system.tabs.updates',
			keywordsKey: 'palette.keywords.updates',
			kindKey: SYSTEM,
			icon: ArrowUpCircle,
			run: { go: '/system#updates' }
		},
		{
			id: 'security',
			nameKey: 'system.tabs.security',
			keywordsKey: 'palette.keywords.security',
			kindKey: SYSTEM,
			icon: Shield,
			run: { go: '/system#security' }
		},
		{
			id: 'monitor',
			nameKey: 'system.tabs.monitor',
			keywordsKey: 'palette.keywords.monitor',
			kindKey: SYSTEM,
			icon: Gauge,
			run: { go: '/system#monitor' }
		},
		{ id: 'logs', nameKey: 'system.tabs.logs', kindKey: SYSTEM, icon: FileText, run: { go: '/system#logs' } },
		{
			id: 'health',
			nameKey: 'system.tabs.health',
			keywordsKey: 'palette.keywords.health',
			kindKey: SYSTEM,
			icon: Heart,
			run: { go: '/system#health' }
		},
		{ id: 'backup', nameKey: 'system.tabs.backup', kindKey: SYSTEM, icon: Database, run: { go: '/system#backup' } },
		{
			id: 'scheduler',
			nameKey: 'system.tabs.scheduler',
			keywordsKey: 'palette.keywords.scheduler',
			kindKey: SYSTEM,
			icon: Calendar,
			run: { go: '/system#scheduler' }
		},
		{
			id: 'notifications',
			nameKey: 'system.tabs.notifications',
			keywordsKey: 'palette.keywords.notifications',
			kindKey: SYSTEM,
			icon: Bell,
			run: { go: '/system#notifications' }
		},
		{
			id: 'network',
			nameKey: 'system.tabs.network',
			keywordsKey: 'palette.keywords.network',
			kindKey: SYSTEM,
			icon: Network,
			run: { go: '/system#network' }
		},
		{
			id: 'remote',
			nameKey: 'remote.tab',
			keywordsKey: 'palette.keywords.remote',
			kindKey: SYSTEM,
			icon: Globe,
			run: { go: '/system#remote' }
		},
		{
			id: 'power',
			nameKey: 'system.tabs.power',
			keywordsKey: 'palette.keywords.power',
			kindKey: SYSTEM,
			icon: Power,
			run: { go: '/system#power' }
		}
	];

	// A regular user's pages: those of the sidebar (ADR-0030).
	const USER_PAGES: Choice[] = [
		{ id: 'dashboard', nameKey: 'layout.nav.dashboard', kindKey: PAGE, icon: LayoutDashboard, run: { go: '/' } },
		{
			id: 'account',
			nameKey: 'layout.nav.account',
			keywordsKey: 'palette.keywords.account',
			kindKey: PAGE,
			icon: UserCog,
			run: { go: '/system#security' }
		}
	];

	// What changes the NAS is never done here: the palette opens the page's own dialog, which asks
	// and acts only once the user confirms it there.
	const ADMIN_ACTIONS: Choice[] = [
		{
			id: 'add-user',
			nameKey: 'palette.action.addUser',
			keywordsKey: 'palette.keywords.addUser',
			kindKey: ACTION,
			icon: UserPlus,
			run: { go: '/users', dialog: 'add-user' }
		},
		{
			id: 'reboot',
			nameKey: 'palette.action.reboot',
			keywordsKey: 'palette.keywords.reboot',
			kindKey: ACTION,
			icon: RotateCcw,
			run: { go: '/system#power', dialog: 'reboot' }
		},
		{
			id: 'shutdown',
			nameKey: 'palette.action.shutdown',
			keywordsKey: 'palette.keywords.shutdown',
			kindKey: ACTION,
			icon: Power,
			run: { go: '/system#power', dialog: 'shutdown' }
		},
		{
			id: 'suspend',
			nameKey: 'palette.action.suspend',
			keywordsKey: 'palette.keywords.suspend',
			kindKey: ACTION,
			icon: Moon,
			run: { go: '/system#power', dialog: 'suspend' }
		}
	];

	// The actions that the palette runs itself: they change nothing on the NAS. The language offered
	// is the one the page is not in.
	const TO_LANGUAGE: Record<Locale, string> = {
		en: 'palette.action.toEnglish',
		ru: 'palette.action.toRussian'
	};

	function ownActions(current: Locale): Choice[] {
		return [
			...LANGUAGES.filter((language) => language.code !== current).map((language) => ({
				id: `language-${language.code}`,
				nameKey: TO_LANGUAGE[language.code],
				kindKey: ACTION,
				icon: Languages,
				run: { language: language.code }
			})),
			{ id: 'logout', nameKey: 'layout.logout', kindKey: ACTION, icon: LogOut, run: { logout: true } }
		];
	}

	interface Shown extends Findable {
		id: string;
		name: string;
		kind: string;
		icon: IconComponent;
		run: Run;
	}

	/** The text of `key` in every language of the web UI. */
	function inEveryLanguage(key: string | undefined): string[] {
		return key ? LANGUAGES.map((language) => translateIn(language.code, key)) : [];
	}

	const choices = $derived([...(admin ? [...ADMIN_PAGES, ...ADMIN_ACTIONS] : USER_PAGES), ...ownActions($locale)]);
	// Reactive: the names follow the language of the page; the search also finds the other's.
	const shown = $derived(
		choices.map(
			(choice): Shown => ({
				id: choice.id,
				name: $t(choice.nameKey),
				kind: $t(choice.kindKey),
				icon: choice.icon,
				run: choice.run,
				names: inEveryLanguage(choice.nameKey),
				keywords: inEveryLanguage(choice.keywordsKey)
			})
		)
	);

	let open = $state(false);
	let query = $state('');
	// The option that the arrow keys or the mouse chose: its index in matches. Typing chooses the
	// first one again (the field's oninput), and so does a list that has become shorter than it.
	let picked = $state(0);
	// The elements of the palette, for the keys' handlers.
	let body: HTMLDivElement | null = null;
	let list: HTMLUListElement | null = null;

	const LIST_ID = 'command-palette-list';
	const optionId = (entry: Shown) => `command-palette-option-${entry.id}`;

	const matches = $derived(narrow(shown, query));
	// The chosen option: its index in matches.
	const active = $derived(picked < matches.length ? picked : 0);
	const activeId = $derived(matches[active] ? optionId(matches[active]) : null);
	// A page that may no longer have the palette, such as after a logout or another account's sign-in
	// in another tab, closes it and forgets a request that no page has taken. Before the markup
	// updates, so that the palette goes in the same update; only `enabled` makes it run again.
	$effect.pre(() => {
		if (enabled) return;
		untrack(() => {
			open = false;
			paletteRequest.set(null);
		});
	});

	/** Open the palette with an empty field: Ctrl+K, or the button of a phone's header bar. */
	export function show() {
		if (!enabled || open) return;
		query = '';
		picked = 0;
		open = true;
	}

	function close() {
		open = false;
	}

	// Also after Back or Forward with the palette open. A request that no page took is dropped once
	// the browser is elsewhere than its page, so that a dialog never opens later by surprise.
	afterNavigate(({ to }) => {
		open = false;
		const request = get(paletteRequest);
		if (request && to && to.url.pathname + to.url.hash !== request.at) paletteRequest.set(null);
	});

	async function choose(entry: Shown) {
		const run = entry.run;
		open = false;
		// The palette has gone and the focus is back where it was before anything happens.
		await tick();
		if ('logout' in run) {
			onlogout?.();
		} else if ('language' in run) {
			locale.set(run.language);
		} else {
			if (run.dialog) paletteRequest.set({ dialog: run.dialog, at: run.go });
			if (location.pathname + location.hash !== run.go) await goto(run.go);
		}
	}

	function handleFieldKeydown(event: KeyboardEvent) {
		if (event.isComposing) return;
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault();
			const count = matches.length;
			if (count === 0) return;
			picked = (active + (event.key === 'ArrowDown' ? 1 : -1) + count) % count;
			void tick().then(() => list?.children[active]?.scrollIntoView({ block: 'nearest' }));
		} else if (event.key === 'Enter') {
			event.preventDefault();
			const entry = matches[active];
			if (entry) void choose(entry);
		}
	}

	// Ctrl+K or Cmd+K, without Shift or Alt, whatever the keyboard layout: the key K where the
	// layout has no Latin letters (Russian), else the letter K. Not a key of an input method.
	function isShortcut(event: KeyboardEvent): boolean {
		if (event.isComposing || event.altKey || event.shiftKey) return false;
		if (event.ctrlKey === event.metaKey) return false;
		// A keydown of the browser's autofill has no key.
		const key = typeof event.key === 'string' ? event.key.toLowerCase() : '';
		return key === 'k' || (!/^[a-z]$/.test(key) && event.code === 'KeyK');
	}

	// i18n-ignore: types of input that hold no text
	const NOT_TEXT = new Set(['button', 'checkbox', 'color', 'file', 'hidden', 'image', 'radio', 'range', 'reset', 'submit']);

	// On macOS Ctrl+K in a text field deletes to the end of its line: the field keeps it there.
	function keptByTheField(event: KeyboardEvent): boolean {
		if (!event.ctrlKey || !/Mac|iPhone|iPad|iPod/.test(navigator.platform)) return false;
		const target = event.target;
		return (
			target instanceof HTMLElement &&
			(target.isContentEditable ||
				target instanceof HTMLTextAreaElement ||
				(target instanceof HTMLInputElement && !NOT_TEXT.has(target.type)))
		);
	}

	/** A dialog of the page is open: a Modal, or one written by hand with its markers. */
	function dialogOpen(): boolean {
		return document.querySelector('[aria-modal="true"], .modal-overlay') !== null;
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (open) keepTabInside(event);
		if (!isShortcut(event) || keptByTheField(event)) return;
		// A key held down repeats: it neither closes nor opens the palette again, and while the
		// palette is open the browser does not get it either.
		if (event.repeat) {
			if (open) event.preventDefault();
			return;
		}
		if (open) {
			event.preventDefault();
			close();
			return;
		}
		// Not over a dialog, which the user finishes or cancels first: the key is then the browser's.
		if (!enabled || event.defaultPrevented || dialogOpen()) return;
		event.preventDefault();
		show();
	}

	// Tab and Shift+Tab go round the palette's own field and button: the page under it is out of
	// reach until it closes.
	function keepTabInside(event: KeyboardEvent) {
		if (event.key !== 'Tab' || event.ctrlKey || event.altKey || event.metaKey) return;
		const dialog = body?.closest<HTMLElement>('[role="dialog"]');
		// Only while the palette is the top dialog: one opened over it has the keys.
		if (!dialog || dialog.inert) return;
		const stops = [...dialog.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled])')];
		if (stops.length === 0) return;
		const first = stops[0];
		const last = stops[stops.length - 1];
		const focused = document.activeElement;
		const inside = focused instanceof HTMLElement && focused !== dialog && dialog.contains(focused);
		if (event.shiftKey ? !inside || focused === first : !inside || focused === last) {
			event.preventDefault();
			(event.shiftKey ? last : first).focus();
		}
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<Modal {open} title={$t('palette.title')} size="lg" onclose={close}>
	<div bind:this={body}>
		<Input
			id="command-palette-field"
			bind:value={query}
			ariaLabel={$t('palette.field')}
			placeholder={$t('palette.placeholder')}
			autocomplete="off"
			spellcheck={false}
			selectOnMount
			listbox={{ id: LIST_ID, activeId, expanded: matches.length > 0 }}
			oninput={() => (picked = 0)}
			onkeydown={handleFieldKeydown}
		/>
		<!-- How many choices are left, for screen readers; positioned, as the overlay scrolls. -->
		<div class="relative">
			<p class="sr-only" role="status">{$t('palette.count', { n: matches.length })}</p>
		</div>
		<ul
			bind:this={list}
			id={LIST_ID}
			role="listbox"
			aria-label={$t('palette.list')}
			class="mt-3 max-h-80 space-y-1 overflow-y-auto scrollbar-thin"
		>
			{#each matches as entry, index (entry.id)}
				<!-- The field keeps the keyboard focus and names the chosen option (aria-activedescendant). -->
				<li
					id={optionId(entry)}
					role="option"
					aria-selected={index === active}
					aria-describedby="{optionId(entry)}-kind"
					data-name={entry.name}
					class="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 {index === active
						? 'bg-surface-overlay text-content'
						: 'text-content-secondary'}"
					onclick={() => choose(entry)}
					onmousemove={() => (picked = index)}
				>
					<entry.icon class="h-4 w-4 shrink-0" aria-hidden="true" />
					<span class="min-w-0 flex-1 break-words">{entry.name}</span>
					<span id="{optionId(entry)}-kind" class="shrink-0 text-xs text-content-muted" aria-hidden="true"
						>{entry.kind}</span
					>
				</li>
			{/each}
		</ul>
		{#if matches.length === 0}
			<p class="mt-3 text-sm text-content-muted">{$t('palette.none')}</p>
		{/if}
		<p class="mt-3 hidden text-xs text-content-muted md:block">{$t('palette.hint')}</p>
	</div>
</Modal>
