<script module lang="ts">
	import type { IconComponent } from './types';

	/** An item of a menu: an action (`onclick`) or a link (`href`). */
	export type MenuItem = {
		label: string;
		icon?: IconComponent;
		onclick?: () => void;
		href?: string;
		/** It deletes, removes or stops something: drawn in the error colour, after the others. */
		danger?: boolean;
		disabled?: boolean;
		/** Why it is disabled, shown under its label. */
		disabledReason?: string;
	};

	let menuCount = 0;
</script>

<script lang="ts">
	import { tick } from 'svelte';
	import { on } from 'svelte/events';
	import Button from './Button.svelte';
	import { placeMenu } from './float';

	// A menu button (ADR-0432, WAI-ARIA's menu button): its button opens a list of actions, which
	// the arrow keys, Home, End and the first letter walk; Enter or Space runs one, Escape closes the
	// menu and gives the focus back to the button, Tab closes it and goes on. The actions that
	// delete or remove come last, after a separator. The list floats in the page's top layer, so
	// that a card or a table frame that clips its content does not cut it.
	type Props = {
		items: MenuItem[];
		/** The button's text, or with `iconOnly` its name for screen readers and its tooltip. */
		label: string;
		icon?: IconComponent | null;
		/** Only the icon shows: the ⋯ of a row (ActionMenu). */
		iconOnly?: boolean;
		variant?: 'secondary' | 'ghost';
		size?: 'sm' | 'md' | 'icon' | 'icon-sm';
		/** Which edge of the button the list lines up with. */
		align?: 'start' | 'end';
		disabled?: boolean;
		class?: string;
		trailing?: IconComponent | null;
	};

	let {
		items,
		label,
		icon = null,
		iconOnly = false,
		variant = 'secondary',
		size = 'sm',
		align = 'end',
		disabled = false,
		class: className = '',
		trailing = null
	}: Props = $props();

	const id = `menu-${++menuCount}`;
	const buttonId = `${id}-button`;
	let open = $state(false);
	let button: HTMLButtonElement | null = $state(null);
	let list: HTMLDivElement | null = $state(null);

	// The safe actions first, then the destructive ones after a separator.
	const ordered = $derived([...items.filter((item) => !item.danger), ...items.filter((item) => item.danger)]);
	const firstDanger = $derived(ordered.findIndex((item) => item.danger));

	function itemsOf(): HTMLElement[] {
		return list ? [...list.querySelectorAll<HTMLElement>('[role="menuitem"]')] : [];
	}

	async function show(focus: 'first' | 'last' | null) {
		open = true;
		await tick();
		if (!list || !button) return;
		// The top layer, where the browser has it: over dialogs and outside every clipping box.
		const popover = list as HTMLDivElement & { showPopover?: () => void };
		try {
			popover.showPopover?.();
		} catch {
			// Shown already, or no top layer: the fixed position and z-index place it.
		}
		placeMenu(list, button.getBoundingClientRect(), align);
		const entries = itemsOf().filter((entry) => entry.getAttribute('aria-disabled') !== 'true');
		const target = focus === 'last' ? entries.at(-1) : entries[0];
		// Without scrolling: a scroll would move the list away from its button and close it.
		(target ?? list).focus({ preventScroll: true });
	}

	function close(refocus: boolean) {
		if (!open) return;
		open = false;
		if (refocus) button?.focus();
	}

	function toggle() {
		if (open) close(false);
		else void show('first');
	}

	function buttonKeydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault();
			void show(event.key === 'ArrowUp' ? 'last' : 'first');
		}
	}

	function move(entries: HTMLElement[], from: number, step: number) {
		if (!entries.length) return;
		entries[(from + step + entries.length) % entries.length].focus({ preventScroll: true });
	}

	function listKeydown(event: KeyboardEvent) {
		const entries = itemsOf();
		const at = entries.indexOf(document.activeElement as HTMLElement);
		switch (event.key) {
			case 'ArrowDown':
				event.preventDefault();
				move(entries, at, 1);
				break;
			case 'ArrowUp':
				event.preventDefault();
				move(entries, at === -1 ? 0 : at, -1);
				break;
			case 'Home':
				event.preventDefault();
				entries[0]?.focus();
				break;
			case 'End':
				event.preventDefault();
				entries.at(-1)?.focus();
				break;
			case 'Escape':
				// Only the menu closes, not the dialog it may be in (Modal skips a used Escape).
				event.preventDefault();
				event.stopPropagation();
				close(true);
				break;
			case 'Tab':
				close(false);
				break;
			default:
				if (event.key.length === 1 && /\S/.test(event.key)) {
					const letter = event.key.toLocaleLowerCase();
					const next = entries.findIndex(
						(entry, index) => index > at && entry.textContent?.trim().toLocaleLowerCase().startsWith(letter)
					);
					const wrap = entries.findIndex((entry) => entry.textContent?.trim().toLocaleLowerCase().startsWith(letter));
					const target = next !== -1 ? next : wrap;
					if (target !== -1) entries[target].focus();
				}
		}
	}

	function choose(item: MenuItem) {
		if (item.disabled) return;
		close(true);
		item.onclick?.();
	}

	// While open: a press outside closes it. A scroll or a resize moves the list with its button
	// (a scroll the page makes as the menu opens, after its button was scrolled into view, included),
	// and closes it once the button has left the window.
	function watchOutside(node: HTMLElement) {
		const away = (event: Event) => {
			const target = event.target as Node | null;
			if (target && (node.contains(target) || button?.contains(target))) return;
			close(false);
		};
		const moved = (event: Event) => {
			if (event.target instanceof Node && node.contains(event.target)) return;
			if (!button) return;
			const rect = button.getBoundingClientRect();
			const gone = rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth;
			if (gone) close(false);
			else placeMenu(node, rect, align);
		};
		const offs = [
			on(document, 'pointerdown', away, { capture: true }),
			on(window, 'scroll', moved, { capture: true }),
			on(window, 'resize', moved)
		];
		return () => offs.forEach((off) => off());
	}
</script>

<span class="relative inline-flex {className}">
	<Button
		bind:el={button}
		{variant}
		size={iconOnly ? (size === 'sm' || size === 'md' ? 'icon' : size) : size}
		{icon}
		{disabled}
		id={buttonId}
		aria-haspopup="menu"
		aria-expanded={open}
		aria-controls={open ? id : undefined}
		aria-label={iconOnly ? label : undefined}
		title={iconOnly ? label : undefined}
		onclick={toggle}
		onkeydown={buttonKeydown}
	>
		{#if !iconOnly}
			{label}
			{#if trailing}
				{@const Trailing = trailing}
				<Trailing class="w-4 h-4 -mr-1 shrink-0 text-content-secondary" aria-hidden="true" />
			{/if}
		{/if}
	</Button>
	{#if open}
		<div
			bind:this={list}
			{id}
			role="menu"
			tabindex="-1"
			aria-labelledby={buttonId}
			popover="manual"
			class="menu"
			onkeydown={listKeydown}
			{@attach watchOutside}
		>
			{#each ordered as item, index (index)}
				{@const ItemIcon = item.icon}
				{#if index === firstDanger && firstDanger > 0}
					<div class="menu-separator" role="separator"></div>
				{/if}
				{#if item.href && !item.disabled}
					<a
						role="menuitem"
						tabindex="-1"
						href={item.href}
						class="menu-item {item.danger ? 'menu-item-danger' : ''}"
						onclick={() => close(false)}
					>
						{#if ItemIcon}<ItemIcon class="w-4 h-4 shrink-0" aria-hidden="true" />{/if}
						<span class="min-w-0">{item.label}</span>
					</a>
				{:else}
					<button
						type="button"
						role="menuitem"
						tabindex="-1"
						aria-disabled={item.disabled ? 'true' : undefined}
						class="menu-item {item.danger ? 'menu-item-danger' : ''}"
						onclick={() => choose(item)}
					>
						{#if ItemIcon}<ItemIcon class="w-4 h-4 shrink-0" aria-hidden="true" />{/if}
						<span class="min-w-0 text-left">
							<span class="block">{item.label}</span>
							{#if item.disabled && item.disabledReason}
								<span class="block text-xs text-content-muted">{item.disabledReason}</span>
							{/if}
						</span>
					</button>
				{/if}
			{/each}
		</div>
	{/if}
</span>
