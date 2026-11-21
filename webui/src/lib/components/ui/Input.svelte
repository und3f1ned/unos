<script lang="ts">
	import { AlertCircle, ChevronDown, ChevronUp } from 'lucide-svelte';
	import { t } from '$lib/i18n';
	import type { IconComponent } from './types';

	type Props = {
		id?: string;
		type?: 'text' | 'password' | 'email' | 'number' | 'search' | 'date';
		value?: string;
		placeholder?: string;
		label?: string;
		hint?: string;
		error?: string;
		required?: boolean;
		disabled?: boolean;
		autocomplete?: HTMLInputElement['autocomplete'];
		maxlength?: number | undefined;
		// The field's name for screen readers where no label is shown, such as a field in a list row.
		ariaLabel?: string;
		spellcheck?: boolean | undefined;
		// "sm" fits the field into a list row.
		size?: 'md' | 'sm';
		// Take the keyboard focus with the whole text selected when the field appears: typing replaces it.
		selectOnMount?: boolean;
		// A text field that narrows a list shown under it, as the command palette's (ADR-0393): a
		// combobox whose list has the id `id`, shown with options while `expanded`, and whose chosen
		// option has the id `activeId`. Only a field of type text takes it.
		listbox?: { id: string; activeId: string | null; expanded: boolean } | null;
		// A leading icon, such as the magnifier of a search field (ADR-0428).
		icon?: IconComponent | null;
		// A sign before the text and a unit after it, inside the field's frame (ADR-0432): "%",
		// "KiB/s", "min". The label then names the quantity alone.
		prefix?: string;
		suffix?: string;
		// The field's width: xs for a number of a few digits, sm for a short code, md for a name;
		// full (the default) takes its column.
		width?: 'xs' | 'sm' | 'md' | 'full';
		// A number field's bounds and step; the bounds are its hint unless it has one.
		min?: number | undefined;
		max?: number | undefined;
		step?: number | undefined;
		// A number field's buttons that step its value down and up.
		steppers?: boolean;
		// The bounds said in the hint; false where the group's description says them once.
		boundsHint?: boolean;
		class?: string;
		// The field's events. A number field reports no input of its own: it sets `value`.
		oninput?: (event: Event & { currentTarget: HTMLInputElement }) => void;
		onchange?: (event: Event & { currentTarget: HTMLInputElement }) => void;
		onfocus?: (event: FocusEvent & { currentTarget: HTMLInputElement }) => void;
		onblur?: (event: FocusEvent & { currentTarget: HTMLInputElement }) => void;
		onkeydown?: (event: KeyboardEvent & { currentTarget: HTMLInputElement }) => void;
		onclick?: (event: MouseEvent & { currentTarget: HTMLInputElement }) => void;
		ondblclick?: (event: MouseEvent & { currentTarget: HTMLInputElement }) => void;
	};

	let {
		id = '',
		type = 'text',
		value = $bindable(''),
		placeholder = '',
		label = '',
		hint = '',
		error = '',
		required = false,
		disabled = false,
		autocomplete = '',
		maxlength = undefined,
		ariaLabel = '',
		spellcheck = undefined,
		size = 'md',
		selectOnMount = false,
		listbox = null,
		icon = null,
		prefix = '',
		suffix = '',
		width = 'full',
		min = undefined,
		max = undefined,
		step = undefined,
		steppers = false,
		boundsHint = true,
		class: className = '',
		oninput,
		onchange,
		onfocus,
		onblur,
		onkeydown,
		onclick,
		ondblclick
	}: Props = $props();

	const inputId = $derived(id || `input-${Math.random().toString(36).slice(2, 9)}`);
	const sizeClass = $derived(
		`${size === 'sm' ? 'px-2 py-1 min-h-[30px]' : ''} ${icon ? (size === 'sm' ? 'pl-8' : 'pl-9') : ''}`
	);
	// A number field's bounds, said in its hint when it has no other.
	const rangeHint = $derived(
		type !== 'number' || !boundsHint
			? ''
			: min !== undefined && max !== undefined
				? $t('ui.input.range', { min, max })
				: min !== undefined
					? $t('ui.input.atLeast', { min })
					: max !== undefined
						? $t('ui.input.atMost', { max })
						: ''
	);
	const shownHint = $derived(hint || rangeHint);
	// The hint or the error names the field for screen readers (ADR-0197).
	// The unit too, which the frame shows beside the text.
	const describedBy = $derived(
		[suffix ? `${inputId}-suffix` : '', error ? `${inputId}-error` : shownHint ? `${inputId}-hint` : '']
			.filter(Boolean)
			.join(' ') || undefined
	);
	const framed = $derived(prefix !== '' || suffix !== '' || (steppers && type === 'number'));
	const widthClass = $derived(width === 'full' ? '' : `field-w-${width}`);

	// A stepper's press: the value one step down or up, within the bounds.
	function stepBy(direction: 1 | -1) {
		const current = Number(value);
		const by = step ?? 1;
		let next = (Number.isFinite(current) && value !== '' ? current : (min ?? 0)) + direction * by;
		if (min !== undefined) next = Math.max(min, next);
		if (max !== undefined) next = Math.min(max, next);
		// No float noise: 0.1 + 0.2 shows as 0.3.
		value = String(Number(next.toFixed(6)));
	}
	const LeadingIcon = $derived(icon);
	// Only a text field is a combobox.
	const combobox = $derived(type === 'text' ? listbox : null);

	function focusAndSelect(field: HTMLInputElement, enabled: boolean) {
		if (!enabled) return;
		field.focus();
		field.select();
	}

	function handleInput(e: Event) {
		const target = e.target as HTMLInputElement;
		value = target.value;
	}
</script>

<div class="{widthClass} {className || ''}">
	{#if label}
		<label for={inputId} class="form-label">
			{label}
			{#if required}<span class="text-error">*</span>{/if}
		</label>
	{/if}

	{#snippet leading()}
		{#if LeadingIcon}
			<LeadingIcon
				class="pointer-events-none absolute top-1/2 -translate-y-1/2 {size === 'sm'
					? 'left-2.5'
					: 'left-3'} w-4 h-4 text-content-muted"
				aria-hidden="true"
			/>
		{/if}
	{/snippet}

	<!-- The field, in a positioned box only with a leading icon: else the label and the field stay
	     siblings, as before. -->
	{#snippet field()}
		{#if type === 'number'}
			<!-- The value stays the text that was typed, as in the other fields. -->
			<input
				id={inputId}
				type="number"
				{value}
				oninput={handleInput}
				{min}
				{max}
				{step}
				{placeholder}
				{required}
				{disabled}
				{autocomplete}
				{spellcheck}
				aria-label={ariaLabel || undefined}
				use:focusAndSelect={selectOnMount}
				class="form-input {sizeClass} disabled:cursor-not-allowed disabled:opacity-50"
				class:border-status-error={error && !framed}
				aria-invalid={error ? 'true' : undefined}
				aria-describedby={describedBy}
				{onchange}
				{onfocus}
				{onblur}
				{onkeydown}
				{onclick}
				{ondblclick}
			/>
		{:else if type === 'date'}
			<!-- The value is "" or YYYY-MM-DD, whatever the browser shows; the dark scheme keeps
		     the browser's calendar button visible on the dark field. -->
			<input
				id={inputId}
				type="date"
				bind:value
				{required}
				{disabled}
				{autocomplete}
				class="form-input disabled:cursor-not-allowed disabled:opacity-50"
				class:border-status-error={error}
				aria-invalid={error ? 'true' : undefined}
				aria-describedby={describedBy}
				{oninput}
				{onchange}
				{onfocus}
				{onblur}
			/>
		{:else}
			<input
				id={inputId}
				{type}
				bind:value
				{placeholder}
				{required}
				{disabled}
				{autocomplete}
				{spellcheck}
				aria-label={ariaLabel || undefined}
				role={combobox ? 'combobox' : undefined}
				aria-autocomplete={combobox ? 'list' : undefined}
				aria-expanded={combobox ? combobox.expanded : undefined}
				aria-controls={combobox?.id}
				aria-activedescendant={combobox?.activeId ?? undefined}
				use:focusAndSelect={selectOnMount}
				{maxlength}
				class="form-input {sizeClass} disabled:cursor-not-allowed disabled:opacity-50"
				class:border-status-error={error && !framed}
				aria-invalid={error ? 'true' : undefined}
				aria-describedby={describedBy}
				{oninput}
				{onchange}
				{onfocus}
				{onblur}
				{onkeydown}
				{onclick}
				{ondblclick}
			/>
		{/if}
	{/snippet}

	{#if icon}
		<div class="relative">
			{@render leading()}
			{@render field()}
		</div>
	{:else if framed}
		<!-- The unit and the steppers inside the field's frame; the input has none of its own. -->
		<div class="form-affix" class:form-affix-invalid={error}>
			{#if prefix}<span class="form-affix-text" aria-hidden="true">{prefix}</span>{/if}
			{@render field()}
			{#if suffix}<span id="{inputId}-suffix" class="form-affix-text" aria-hidden="true">{suffix}</span>{/if}
			{#if steppers && type === 'number'}
				<!-- Out of the Tab order: the field's own arrow keys step it. -->
				<span class="form-stepper">
					<button
						type="button"
						tabindex="-1"
						aria-label={$t('ui.input.increase', { label: label || ariaLabel || $t('ui.input.value') })}
						disabled={disabled || (max !== undefined && Number(value) >= max)}
						onclick={() => stepBy(1)}><ChevronUp class="w-3.5 h-3.5" aria-hidden="true" /></button
					>
					<button
						type="button"
						tabindex="-1"
						aria-label={$t('ui.input.decrease', { label: label || ariaLabel || $t('ui.input.value') })}
						disabled={disabled || (min !== undefined && value !== '' && Number(value) <= min)}
						onclick={() => stepBy(-1)}><ChevronDown class="w-3.5 h-3.5" aria-hidden="true" /></button
					>
				</span>
			{/if}
		</div>
	{:else}
		{@render field()}
	{/if}

	{#if error}
		<p id="{inputId}-error" class="form-error">
			<AlertCircle class="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden="true" />{error}
		</p>
	{:else if shownHint}
		<p id="{inputId}-hint" class="form-hint">{shownHint}</p>
	{/if}
</div>
