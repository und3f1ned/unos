<script context="module" lang="ts">
	// Numbers the title ids: every dialog is labelled by its own title.
	let titleCount = 0;

	// The stack of open dialogs, by title id in the order they opened: the last one is on top,
	// and only it answers Escape, whatever the order in which the page declares them (ADR-0360).
	// Read when a key comes, not in a reactive statement: the dialog that opens or closes changes
	// it while the page updates.
	const openModals: string[] = [];
</script>

<script lang="ts">
	import { X } from 'lucide-svelte';
	import { createEventDispatcher, onDestroy } from 'svelte';
	import { t } from '$lib/i18n';

	export let open: boolean = false;
	export let title: string = '';
	export let size: 'sm' | 'md' | 'lg' | 'xl' = 'md';
	export let closable: boolean = true;
	export let danger: boolean = false;

	const dispatch = createEventDispatcher();
	const titleId = `modal-title-${++titleCount}`;

	const sizeClasses = {
		sm: 'modal-sm',
		md: 'modal-md',
		lg: 'modal-lg',
		xl: 'modal-xl'
	};

	function handleClose() {
		if (closable) {
			dispatch('close');
		}
	}

	// The element that had the keyboard focus when the dialog opened, and the dialog's overlay.
	let opener: HTMLElement | null = null;
	let overlay: HTMLDivElement | null = null;
	let stacked = false;
	// How many dialogs were open under it: its overlay is drawn over theirs (z-index 50, the z-50
	// of .modal-overlay, and one more for each), whatever the markup order.
	let depth = 0;

	// Runs before the dialog's markup is added or removed, while the focus is still where it was.
	$: track(open);

	function track(isOpen: boolean) {
		if (isOpen === stacked) return;
		stacked = isOpen;
		if (isOpen) {
			const active = document.activeElement;
			opener = active instanceof HTMLElement && active !== document.body ? active : null;
			depth = openModals.push(titleId) - 1;
		} else {
			leave();
		}
	}

	// Off the stack. The focus goes back to the opener, such as a button of the dialog under
	// this one, when it was in this dialog or got lost, and the opener is still on the page; a
	// page that puts the focus elsewhere after the dialog closed (after a tick) has the last word.
	function leave() {
		openModals.splice(openModals.indexOf(titleId), 1);
		const active = document.activeElement;
		const lost = !active || active === document.body || (overlay?.contains(active) ?? false);
		if (lost && opener?.isConnected) opener.focus();
		opener = null;
	}

	onDestroy(() => {
		if (stacked) leave();
	});

	// The keyboard focus moves into the dialog that opens, unless an element of it already took
	// it (an Input with selectOnMount), so that Tab and Space act in the top dialog, not under it.
	function takeFocus(dialog: HTMLElement) {
		if (!dialog.contains(document.activeElement)) dialog.focus({ preventScroll: true });
	}

	function handleKeydown(event: KeyboardEvent) {
		// Escape is the top dialog's alone: one under it stays open, and a busy (not closable)
		// top dialog keeps the key from the dialogs and the menu under it. Every dialog listens
		// on the window, and the page updates between the listeners: a dialog that is on top
		// once this Escape has closed the one over it sees the key marked as used.
		const onTop = stacked && openModals[openModals.length - 1] === titleId;
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

<svelte:window on:keydown={handleKeydown} />

{#if open}
	<!-- A dialog taller than the window scrolls with the overlay, so that its buttons stay
	     reachable; my-auto centers it while it fits (items-center would cut off its top). -->
	<div
		bind:this={overlay}
		use:takeFocus
		class="modal-overlay items-start overflow-y-auto p-4 outline-none"
		on:click={handleBackdropClick}
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
					<slot name="title-icon" />
					{title}
				</h2>
				{#if closable}
					<button
						type="button"
						class="modal-close"
						on:click={handleClose}
						aria-label={$t('ui.modal.close')}
					>
						<X class="w-5 h-5" />
					</button>
				{/if}
			</div>

			<div class="modal-body">
				<slot />
			</div>

			{#if $$slots.footer}
				<div class="modal-footer">
					<slot name="footer" />
				</div>
			{/if}
		</div>
	</div>
{/if}
