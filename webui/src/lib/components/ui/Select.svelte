<script lang="ts">
	import { ChevronDown, Check } from 'lucide-svelte';
	import { onMount, onDestroy } from 'svelte';
	import { fly } from 'svelte/transition';
	import { t } from '$lib/i18n';

	type Option = { value: string; label: string; disabled?: boolean };

	type Props = {
		id?: string;
		value?: string;
		label?: string;
		hint?: string;
		error?: string;
		required?: boolean;
		disabled?: boolean;
		// Without a placeholder: "Select an option" in the language of the page.
		placeholder?: string | undefined;
		options?: Option[];
		class?: string;
		// An option was chosen, after `value` took it.
		onchange?: (value: string) => void;
	};

	let {
		id = '',
		value = $bindable(''),
		label = '',
		hint = '',
		error = '',
		required = false,
		disabled = false,
		placeholder = undefined,
		options = [],
		class: className = '',
		onchange
	}: Props = $props();

	// The list drops in over --dur-base, at once under reduced motion (ADR-0428).
	// i18n-ignore: a CSS media query, the same in every language
	const reducedMotion = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

	let open = $state(false);
	let container: HTMLDivElement;
	let listbox: HTMLUListElement | undefined = $state();
	let highlightedIndex = $state(-1);

	const inputId = $derived(id || `select-${Math.random().toString(36).slice(2, 9)}`);
	const selectedOption = $derived(options.find((opt) => opt.value === value));
	const displayValue = $derived(selectedOption?.label || (placeholder ?? $t('ui.select.placeholder')));

	function toggle() {
		if (disabled) return;
		open = !open;
		if (open) {
			highlightedIndex = options.findIndex((opt) => opt.value === value);
			if (highlightedIndex === -1) highlightedIndex = 0;
		}
	}

	function selectOption(option: Option) {
		if (option.disabled) return;
		value = option.value;
		open = false;
		onchange?.(option.value);
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
			case 'PageDown':
			case 'PageUp':
				if (open) {
					e.preventDefault();
					const page = e.key === 'PageDown' ? 10 : -10;
					const target = Math.min(Math.max(highlightedIndex + page, 0), options.length - 1);
					highlightedIndex = findNextIndex(target - Math.sign(page), Math.sign(page));
					scrollToHighlighted();
				}
				break;
			case 'Tab':
				open = false;
				break;
			default:
				if (e.key.length === 1 && e.key !== ' ' && !e.ctrlKey && !e.metaKey && !e.altKey) {
					e.preventDefault();
					if (!open) toggle();
					typeAhead(e.key);
				}
		}
	}

	// Type-ahead (ADR-0197): the letters typed within a second pick the first option whose label
	// starts with them, from the highlighted one on, so that a long list (the host's ~600 time
	// zones) is reached in a few keys.
	let typed = '';
	let typedAt = 0;

	function typeAhead(key: string) {
		const now = Date.now();
		typed = now - typedAt > 1000 ? key : typed + key;
		typedAt = now;
		const prefix = typed.toLowerCase();
		const start = typed.length === 1 ? highlightedIndex + 1 : Math.max(highlightedIndex, 0);
		for (let step = 0; step < options.length; step++) {
			const index = (start + step) % options.length;
			const option = options[index];
			if (!option.disabled && option.label.toLowerCase().startsWith(prefix)) {
				highlightedIndex = index;
				scrollToHighlighted();
				return;
			}
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

<div class="relative {className || ''}" bind:this={container}>
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
		aria-activedescendant={open && highlightedIndex >= 0 ? `${inputId}-option-${highlightedIndex}` : undefined}
		aria-labelledby={label ? `${inputId}-label` : undefined}
		{disabled}
		class="select-trigger"
		class:select-trigger-error={error}
		class:select-trigger-open={open}
		class:select-trigger-disabled={disabled}
		onclick={toggle}
		onkeydown={handleKeydown}
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
			in:fly={{ y: -8, duration: reducedMotion ? 0 : 180 }}
		>
			{#each options as option, index}
				<li
					id="{inputId}-option-{index}"
					role="option"
					aria-selected={option.value === value}
					aria-disabled={option.disabled}
					class="select-option"
					class:select-option-selected={option.value === value}
					class:select-option-highlighted={index === highlightedIndex}
					class:select-option-disabled={option.disabled}
					onclick={() => selectOption(option)}
					onmouseenter={() => {
						if (!option.disabled) highlightedIndex = index;
					}}
				>
					<span class="select-option-label">{option.label}</span>
					{#if option.value === value}
						<Check class="w-4 h-4 text-brand-ring" aria-hidden="true" />
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
	/* The trigger looks like Input's field (ADR-0428); the list is a popover. */
	.select-trigger {
		@apply w-full min-h-9 px-3 py-1.5 bg-surface-overlay border border-border-strong rounded text-content
		       text-left flex items-center justify-between gap-2
		       hover:border-content-muted;
		transition: border-color var(--dur-fast), box-shadow var(--dur-fast);
	}

	.select-trigger:focus,
	.select-trigger-open {
		@apply outline-none border-brand-ring;
		box-shadow:
			0 0 0 1px rgb(var(--color-brand-ring)),
			0 0 0 4px rgb(var(--color-brand-ring) / 0.25);
	}

	.select-trigger-error {
		@apply border-status-error;
	}

	.select-trigger-disabled {
		@apply opacity-50 cursor-not-allowed hover:border-border-strong;
	}

	.select-value {
		@apply truncate;
	}

	.select-placeholder {
		@apply text-content-muted;
	}

	.select-icon {
		@apply w-4 h-4 text-content-secondary flex-shrink-0;
		transition: transform var(--dur-base) var(--ease);
	}

	.select-icon-open {
		@apply rotate-180;
	}

	.select-dropdown {
		@apply absolute z-50 w-full mt-1 p-1 bg-surface-elevated border border-border
		       rounded-card shadow-popover max-h-60 overflow-auto;
	}

	.select-option {
		@apply min-h-8 px-2.5 py-1.5 rounded flex items-center justify-between gap-2 cursor-pointer;
		transition: background-color var(--dur-fast);
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
