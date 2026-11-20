<script lang="ts">
	// The command palette (ADR-0393, ADR-0431): Ctrl+K, or Cmd+K, opens it over a signed-in page;
	// its field narrows the places of the user's role, and Enter opens the chosen page or tab, or
	// the page's own dialog of an action, which asks first. It changes nothing itself and asks the
	// server for nothing: its choices come from the role the layout has already loaded and from the
	// catalogs.
	import { tick, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { afterNavigate, goto } from '$app/navigation';
	import { Input, Modal, type IconComponent } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { paletteRequest } from '$lib/stores/palette';
	import { ADMIN_ACTIONS, ADMIN_PAGES, inEveryLanguage, USER_PAGES, type Run } from './choices';
	import { dialogOpen, isShortcut, keptByTheField } from './keys';
	import { narrow, type Findable } from './match';

	type Props = {
		/** Whether the palette may open: a signed-in page of an account that has its rights. */
		enabled?: boolean;
		/** An administrator gets every page and action; a regular user the Dashboard and Account (ADR-0030). */
		admin?: boolean;
	};

	let { enabled = false, admin = false }: Props = $props();

	interface Shown extends Findable {
		id: string;
		name: string;
		kind: string;
		icon: IconComponent;
		run: Run;
	}

	const choices = $derived(admin ? [...ADMIN_PAGES, ...ADMIN_ACTIONS] : USER_PAGES);
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
		if (run.dialog) paletteRequest.set({ dialog: run.dialog, at: run.go });
		if (location.pathname + location.hash !== run.go) await goto(run.go);
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
