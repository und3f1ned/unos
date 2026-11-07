<script context="module" lang="ts">
	// Numbers the title ids: every dialog is labelled by its own title.
	let titleCount = 0;
</script>

<script lang="ts">
	import { X } from 'lucide-svelte';
	import { createEventDispatcher } from 'svelte';

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

	function handleKeydown(event: KeyboardEvent) {
		// One Escape closes one open dialog; a closed one stays out of it. Every dialog listens
		// on the window and the page updates between the listeners, so a dialog that this Escape
		// has just shown again (the one a confirmation was opened from) sees it after the
		// confirmation closed: the key marked as used leaves it open, whatever the markup order.
		if (!open || event.key !== 'Escape' || event.defaultPrevented || !closable) return;
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
		class="modal-overlay items-start overflow-y-auto p-4"
		on:click={handleBackdropClick}
		role="dialog"
		aria-modal="true"
		aria-labelledby={titleId}
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
						aria-label="Close modal"
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
