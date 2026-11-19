<script lang="ts">
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
	const sizeClass = $derived(size === 'sm' ? 'px-2 py-1' : '');
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

<div class={className || ''}>
	{#if label}
		<label for={inputId} class="form-label">
			{label}
			{#if required}<span class="text-error">*</span>{/if}
		</label>
	{/if}

	{#if type === 'number'}
		<!-- The value stays the text that was typed, as in the other fields. -->
		<input
			id={inputId}
			type="number"
			{value}
			oninput={handleInput}
			{placeholder}
			{required}
			{disabled}
			{autocomplete}
			{spellcheck}
			aria-label={ariaLabel || undefined}
			use:focusAndSelect={selectOnMount}
			class="form-input {sizeClass} disabled:cursor-not-allowed disabled:opacity-50"
			class:border-status-error={error}
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
			class:border-status-error={error}
			{oninput}
			{onchange}
			{onfocus}
			{onblur}
			{onkeydown}
			{onclick}
			{ondblclick}
		/>
	{/if}

	{#if error}
		<p class="form-error">{error}</p>
	{:else if hint}
		<p class="form-hint">{hint}</p>
	{/if}
</div>
