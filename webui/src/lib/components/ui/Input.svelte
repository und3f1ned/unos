<script lang="ts">
	export let id: string = "";
	export let type: "text" | "password" | "email" | "number" | "search" | "date" = "text";
	export let value: string = "";
	export let placeholder: string = "";
	export let label: string = "";
	export let hint: string = "";
	export let error: string = "";
	export let required: boolean = false;
	export let disabled: boolean = false;
	export let autocomplete: string = "";
	export let maxlength: number | undefined = undefined;
	// The field's name for screen readers where no label is shown, such as a field in a list row.
	export let ariaLabel: string = "";
	export let spellcheck: boolean | undefined = undefined;
	// "sm" fits the field into a list row.
	export let size: "md" | "sm" = "md";
	// Take the keyboard focus with the whole text selected when the field appears: typing replaces it.
	export let selectOnMount: boolean = false;
	// A text field that narrows a list shown under it, as the command palette's (ADR-0393): a
	// combobox whose list has the id `id`, shown with options while `expanded`, and whose chosen
	// option has the id `activeId`. Only a field of type text takes it.
	export let listbox: { id: string; activeId: string | null; expanded: boolean } | null = null;

	$: inputId = id || `input-${Math.random().toString(36).slice(2, 9)}`;
	$: sizeClass = size === "sm" ? "px-2 py-1" : "";

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

<div class={$$props.class || ""}>
	{#if label}
		<label for={inputId} class="form-label">
			{label}
			{#if required}<span class="text-error">*</span>{/if}
		</label>
	{/if}

	{#if type === "text"}
		<input
			id={inputId}
			type="text"
			bind:value
			{placeholder}
			{required}
			{disabled}
			{autocomplete}
			{spellcheck}
			aria-label={ariaLabel || undefined}
			role={listbox ? "combobox" : undefined}
			aria-autocomplete={listbox ? "list" : undefined}
			aria-expanded={listbox ? listbox.expanded : undefined}
			aria-controls={listbox?.id}
			aria-activedescendant={listbox?.activeId ?? undefined}
			use:focusAndSelect={selectOnMount}
			{maxlength}
			class="form-input {sizeClass} disabled:cursor-not-allowed disabled:opacity-50"
			class:border-status-error={error}
			on:input
			on:change
			on:focus
			on:blur
			on:keydown
			on:click
			on:dblclick
		/>
	{:else if type === "password"}
		<input
			id={inputId}
			type="password"
			bind:value
			{placeholder}
			{required}
			{disabled}
			{autocomplete}
			{spellcheck}
			aria-label={ariaLabel || undefined}
			use:focusAndSelect={selectOnMount}
			{maxlength}
			class="form-input {sizeClass} disabled:cursor-not-allowed disabled:opacity-50"
			class:border-status-error={error}
			on:input
			on:change
			on:focus
			on:blur
			on:keydown
			on:click
			on:dblclick
		/>
	{:else if type === "email"}
		<input
			id={inputId}
			type="email"
			bind:value
			{placeholder}
			{required}
			{disabled}
			{autocomplete}
			{spellcheck}
			aria-label={ariaLabel || undefined}
			use:focusAndSelect={selectOnMount}
			{maxlength}
			class="form-input {sizeClass} disabled:cursor-not-allowed disabled:opacity-50"
			class:border-status-error={error}
			on:input
			on:change
			on:focus
			on:blur
			on:keydown
			on:click
			on:dblclick
		/>
	{:else if type === "number"}
		<input
			id={inputId}
			type="number"
			{value}
			on:input={handleInput}
			{placeholder}
			{required}
			{disabled}
			{autocomplete}
			{spellcheck}
			aria-label={ariaLabel || undefined}
			use:focusAndSelect={selectOnMount}
			class="form-input {sizeClass} disabled:cursor-not-allowed disabled:opacity-50"
			class:border-status-error={error}
			on:change
			on:focus
			on:blur
			on:keydown
			on:click
			on:dblclick
		/>
	{:else if type === "search"}
		<input
			id={inputId}
			type="search"
			bind:value
			{placeholder}
			{required}
			{disabled}
			{autocomplete}
			{spellcheck}
			aria-label={ariaLabel || undefined}
			use:focusAndSelect={selectOnMount}
			{maxlength}
			class="form-input {sizeClass} disabled:cursor-not-allowed disabled:opacity-50"
			class:border-status-error={error}
			on:input
			on:change
			on:focus
			on:blur
			on:keydown
			on:click
			on:dblclick
		/>
	{:else if type === "date"}
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
			on:input
			on:change
			on:focus
			on:blur
		/>
	{/if}

	{#if error}
		<p class="form-error">{error}</p>
	{:else if hint}
		<p class="form-hint">{hint}</p>
	{/if}
</div>
