<script lang="ts">
	import { X } from 'lucide-svelte';
	import { createEventDispatcher } from 'svelte';

	export let open: boolean = false;
	export let title: string = '';
	export let size: 'sm' | 'md' | 'lg' | 'xl' = 'md';
	export let closable: boolean = true;
	export let danger: boolean = false;

	const dispatch = createEventDispatcher();

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
		if (event.key === 'Escape' && closable) {
			handleClose();
		}
	}

	function handleBackdropClick(event: MouseEvent) {
		if (event.target === event.currentTarget && closable) {
			handleClose();
		}
	}
</script>

<svelte:window on:keydown={handleKeydown} />

{#if open}
	<div
		class="modal-overlay"
		on:click={handleBackdropClick}
		role="dialog"
		aria-modal="true"
		aria-labelledby="modal-title"
	>
		<div class="modal {sizeClasses[size]}">
			<div class="modal-header">
				<h2
					id="modal-title"
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
