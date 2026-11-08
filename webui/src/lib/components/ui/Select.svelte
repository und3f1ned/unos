<script lang="ts">
	import { ChevronDown, Check } from 'lucide-svelte';
	import { createEventDispatcher, onMount, onDestroy } from 'svelte';
	import { fly } from 'svelte/transition';
	import { t } from '$lib/i18n';

	export let id: string = '';
	export let value: string = '';
	export let label: string = '';
	export let hint: string = '';
	export let error: string = '';
	export let required: boolean = false;
	export let disabled: boolean = false;
	// Without a placeholder: "Select an option" in the language of the page.
	export let placeholder: string | undefined = undefined;
	export let options: Array<{ value: string; label: string; disabled?: boolean }> = [];

	const dispatch = createEventDispatcher();

	let open = false;
	let container: HTMLDivElement;
	let listbox: HTMLUListElement;
	let highlightedIndex = -1;

	$: inputId = id || `select-${Math.random().toString(36).slice(2, 9)}`;
	$: selectedOption = options.find((opt) => opt.value === value);
	$: displayValue = selectedOption?.label || (placeholder ?? $t('ui.select.placeholder'));

	function toggle() {
		if (disabled) return;
		open = !open;
		if (open) {
			highlightedIndex = options.findIndex((opt) => opt.value === value);
			if (highlightedIndex === -1) highlightedIndex = 0;
		}
	}

	function selectOption(option: { value: string; label: string; disabled?: boolean }) {
		if (option.disabled) return;
		value = option.value;
		open = false;
		dispatch('change', { value: option.value });
	}

	function handleKeydown(e: KeyboardEvent) {
		if (disabled) return;

		switch (e.key) {
			case 'Enter':
			case ' ':
				e.preventDefault();
				if (open && highlightedIndex >= 0) {
					const option = options[highlightedIndex];
					if (option && !option.disabled) {
						selectOption(option);
					}
				} else {
					toggle();
				}
				break;
			case 'Escape':
				// Closes only the list, not the dialog around it; a closed list
				// leaves Escape to the dialog.
				if (open) {
					e.preventDefault();
					e.stopPropagation();
					open = false;
				}
				break;
			case 'ArrowDown':
				e.preventDefault();
				if (!open) {
					open = true;
					highlightedIndex = options.findIndex((opt) => opt.value === value);
					if (highlightedIndex === -1) highlightedIndex = 0;
				} else {
					highlightedIndex = findNextIndex(highlightedIndex, 1);
				}
				scrollToHighlighted();
				break;
			case 'ArrowUp':
				e.preventDefault();
				if (!open) {
					open = true;
					highlightedIndex = options.findIndex((opt) => opt.value === value);
					if (highlightedIndex === -1) highlightedIndex = 0;
				} else {
					highlightedIndex = findNextIndex(highlightedIndex, -1);
				}
				scrollToHighlighted();
				break;
			case 'Home':
				if (open) {
					e.preventDefault();
					highlightedIndex = findNextIndex(-1, 1);
					scrollToHighlighted();
				}
				break;
			case 'End':
				if (open) {
					e.preventDefault();
					highlightedIndex = findNextIndex(options.length, -1);
					scrollToHighlighted();
				}
				break;
			case 'Tab':
				open = false;
				break;
		}
	}

	function findNextIndex(current: number, direction: number): number {
		let next = current + direction;
		while (next >= 0 && next < options.length) {
			if (!options[next].disabled) {
				return next;
			}
			next += direction;
		}
		return current;
	}

	function scrollToHighlighted() {
		if (listbox && highlightedIndex >= 0) {
			const item = listbox.children[highlightedIndex] as HTMLElement;
			if (item) {
				item.scrollIntoView({ block: 'nearest' });
			}
		}
	}

	function handleClickOutside(e: MouseEvent) {
		if (container && !container.contains(e.target as Node)) {
			open = false;
		}
	}

	onMount(() => {
		document.addEventListener('click', handleClickOutside);
	});

	onDestroy(() => {
		document.removeEventListener('click', handleClickOutside);
	});
</script>

<div class="relative {$$props.class || ''}" bind:this={container}>
	{#if label}
		<label id="{inputId}-label" for={inputId} class="form-label">
			{label}
			{#if required}<span class="text-error">*</span>{/if}
		</label>
	{/if}

	<button
		id={inputId}
		type="button"
		role="combobox"
		aria-haspopup="listbox"
		aria-expanded={open}
		aria-controls="{inputId}-listbox"
		aria-labelledby={label ? `${inputId}-label` : undefined}
		{disabled}
		class="select-trigger"
		class:select-trigger-error={error}
		class:select-trigger-open={open}
		class:select-trigger-disabled={disabled}
		on:click={toggle}
		on:keydown={handleKeydown}
	>
		<span class="select-value" class:select-placeholder={!selectedOption}>
			{displayValue}
		</span>
		<ChevronDown class="select-icon {open ? 'select-icon-open' : ''}" />
	</button>

	{#if open}
		<ul
			bind:this={listbox}
			id="{inputId}-listbox"
			role="listbox"
			aria-labelledby={label ? `${inputId}-label` : undefined}
			class="select-dropdown scrollbar-thin"
			in:fly={{ y: -8, duration: 150 }}
		>
			{#each options as option, index}
				<li
					role="option"
					aria-selected={option.value === value}
					aria-disabled={option.disabled}
					class="select-option"
					class:select-option-selected={option.value === value}
					class:select-option-highlighted={index === highlightedIndex}
					class:select-option-disabled={option.disabled}
					on:click={() => selectOption(option)}
					on:mouseenter={() => {
						if (!option.disabled) highlightedIndex = index;
					}}
				>
					<span class="select-option-label">{option.label}</span>
					{#if option.value === value}
						<Check class="w-4 h-4 text-info" />
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	{#if error}
		<p class="form-error">{error}</p>
	{:else if hint}
		<p class="form-hint">{hint}</p>
	{/if}
</div>

<style>
	.select-trigger {
		@apply w-full px-3 py-2 bg-surface-overlay border border-border rounded-lg text-content
		       text-left flex items-center justify-between gap-2
		       transition-all duration-150 ease-in-out
		       hover:border-slate-500
		       focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500;
	}

	.select-trigger-open {
		@apply border-blue-500 ring-1 ring-blue-500;
	}

	.select-trigger-error {
		@apply border-red-500;
	}

	.select-trigger-disabled {
		@apply opacity-50 cursor-not-allowed hover:border-border;
	}

	.select-value {
		@apply truncate;
	}

	.select-placeholder {
		@apply text-content-muted;
	}

	.select-icon {
		@apply w-4 h-4 text-content-secondary flex-shrink-0 transition-transform duration-150;
	}

	.select-icon-open {
		@apply rotate-180;
	}

	.select-dropdown {
		@apply absolute z-50 w-full mt-1 py-1 bg-surface-elevated border border-border
		       rounded-lg shadow-lg max-h-60 overflow-auto;
	}

	.select-option {
		@apply px-3 py-2 flex items-center justify-between gap-2 cursor-pointer
		       transition-colors duration-75;
	}

	.select-option-highlighted {
		@apply bg-surface-overlay;
	}

	.select-option-selected {
		@apply text-content font-medium;
	}

	.select-option-disabled {
		@apply opacity-50 cursor-not-allowed;
	}

	.select-option-label {
		@apply truncate;
	}
</style>
