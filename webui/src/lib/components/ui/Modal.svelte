<script module lang="ts">
	import { get, writable } from 'svelte/store';

	// Numbers the title ids: every dialog is labelled by its own title.
	let titleCount = 0;

	// The stack of open dialogs, by title id in the order they opened, each with its depth: the
	// last one is on top, and only it answers Escape and takes the focus, whatever the order in
	// which the page declares them (ADR-0361). A store, so that every open dialog learns at once
	// when one opens or closes over it. Escape reads it when the key comes, not in a reactive
	// statement: the dialog that opens or closes changes it while the page updates.
	type OpenModal = { id: string; depth: number };
	const openModals = writable<OpenModal[]>([]);
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import { X } from 'lucide-svelte';
	import { onDestroy, untrack } from 'svelte';
	import { on } from 'svelte/events';
	import { t } from '$lib/i18n';

	type Props = {
		open?: boolean;
		title?: string;
		size?: 'sm' | 'md' | 'lg' | 'xl';
		closable?: boolean;
		danger?: boolean;
		// Escape, the backdrop or the close button asked to close a closable dialog.
		onclose?: () => void;
		// Drawn before the title, such as the icon of a dangerous action.
		titleIcon?: Snippet;
		footer?: Snippet;
		children?: Snippet;
	};

	let {
		open = false,
		title = '',
		size = 'md',
		closable = true,
		danger = false,
		onclose,
		titleIcon,
		footer,
		children
	}: Props = $props();

	const titleId = `modal-title-${++titleCount}`;

	const sizeClasses = {
		sm: 'modal-sm',
		md: 'modal-md',
		lg: 'modal-lg',
		xl: 'modal-xl'
	};

	function handleClose() {
		if (closable) {
			onclose?.();
		}
	}

	// The element that had the keyboard focus when the dialog opened, and the dialog's overlay.
	let opener: HTMLElement | null = null;
	let overlay: HTMLDivElement | null = $state(null);
	let stacked = false;
	// One more than the highest depth of the dialogs open when it opened: its overlay is drawn
	// over theirs (z-index 50, the z-50 of .modal-overlay, plus the depth), whatever the markup
	// order, also after a dialog under the top one closed.
	let depth = $state(0);

	// Runs before the dialog's markup is added or removed, while the focus is still where it was.
	// Only `open` makes it run again: what track reads and writes is its own business.
	$effect.pre(() => {
		const isOpen = open;
		untrack(() => track(isOpen));
	});

	function track(isOpen: boolean) {
		if (isOpen === stacked) return;
		stacked = isOpen;
		if (isOpen) {
			const active = document.activeElement;
			opener = active instanceof HTMLElement && active !== document.body ? active : null;
			const stack = get(openModals);
			const below = stack.reduce((highest, modal) => Math.max(highest, modal.depth + 1), 0);
			depth = below;
			openModals.set([...stack, { id: titleId, depth: below }]);
		} else {
			leave();
		}
	}

	// Off the stack. The focus goes back to the opener, such as a checkbox of the dialog under
	// this one, when it was in this dialog or got lost, and the opener is still on the page; a
	// page that puts the focus elsewhere after the dialog closed (after a tick) has the last word.
	// The dialog under this one is no longer inert by then: the store tells it at once.
	function leave() {
		openModals.update((stack) => stack.filter((modal) => modal.id !== titleId));
		const active = document.activeElement;
		const lost = !active || active === document.body || (overlay?.contains(active) ?? false);
		if (lost && opener?.isConnected) opener.focus();
		opener = null;
	}

	onDestroy(() => {
		if (stacked) leave();
	});

	// An open dialog under the top one is inert: Tab, clicks and screen readers skip it until the
	// dialogs over it have closed. Set on the element as soon as the stack changes, not through
	// the markup's next update, so that the focus can go back into it when the top one closes.
	// The keyboard focus moves into the dialog that opens, unless an element of it already took
	// it (an Input with selectOnMount), so that Tab and Space act in the top dialog, not under it.
	function inStack(dialog: HTMLElement) {
		const unsubscribe = openModals.subscribe((stack) => {
			const at = stack.findIndex((modal) => modal.id === titleId);
			dialog.inert = at !== -1 && at < stack.length - 1;
		});
		if (!dialog.inert && !dialog.contains(document.activeElement)) {
			dialog.focus({ preventScroll: true });
		}
		return { destroy: unsubscribe };
	}

	function handleKeydown(event: KeyboardEvent) {
		// Escape is the top dialog's alone: one under it stays open, and a busy (not closable)
		// top dialog keeps the key from the dialogs and the menu under it. Every dialog listens
		// on the window, and the page updates between the listeners: a dialog that is on top
		// once this Escape has closed the one over it sees the key marked as used.
		const stack = get(openModals);
		const onTop = stacked && stack[stack.length - 1]?.id === titleId;
		if (!onTop || event.key !== 'Escape' || event.defaultPrevented) return;
		event.preventDefault();
		handleClose();
	}

	function handleBackdropClick(event: MouseEvent) {
		if (event.target === event.currentTarget && closable) {
			handleClose();
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
	<!-- A dialog taller than the window scrolls with the overlay, so that its buttons stay
	     reachable; my-auto centers it while it fits (items-center would cut off its top). The
	     overlay listens for the click itself, not through Svelte's delegated listener at the
	     app's root, as the drawer's scrim of layout/AppSidebar.svelte does: iOS Safari makes a tap a click
	     only where an element listens, and a click that does not bubble closes it too. -->
	<div
		bind:this={overlay}
		use:inStack
		class="modal-overlay items-start overflow-y-auto p-4 outline-none"
		{@attach (node) => on(node, 'click', handleBackdropClick)}
		role="dialog"
		aria-modal="true"
		aria-labelledby={titleId}
		tabindex="-1"
		style:z-index={50 + depth}
	>
		<div class="modal my-auto {sizeClasses[size]}">
			<div class="modal-header">
				<h2
					id={titleId}
					class="modal-title {danger ? 'text-error flex items-center gap-2' : ''}"
				>
					{@render titleIcon?.()}
					{title}
				</h2>
				{#if closable}
					<button
						type="button"
						class="modal-close"
						onclick={handleClose}
						aria-label={$t('ui.modal.close')}
					>
						<X class="w-5 h-5" />
					</button>
				{/if}
			</div>

			<div class="modal-body">
				{@render children?.()}
			</div>

			{#if footer}
				<div class="modal-footer">
					{@render footer()}
				</div>
			{/if}
		</div>
	</div>
{/if}
