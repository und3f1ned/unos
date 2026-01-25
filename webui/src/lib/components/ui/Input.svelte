<script lang="ts">
	export let id: string = '';
	export let type: 'text' | 'password' | 'email' | 'number' | 'search' = 'text';
	export let value: string = '';
	export let placeholder: string = '';
	export let label: string = '';
	export let hint: string = '';
	export let error: string = '';
	export let required: boolean = false;
	export let disabled: boolean = false;
	export let autocomplete: string = '';
	export let maxlength: number | undefined = undefined;

	$: inputId = id || `input-${Math.random().toString(36).slice(2, 9)}`;
</script>

<div class={$$props.class || ''}>
	{#if label}
		<label for={inputId} class="form-label">
			{label}
			{#if required}<span class="text-error">*</span>{/if}
		</label>
	{/if}

	<input
		id={inputId}
		{type}
		bind:value
		{placeholder}
		{required}
		{disabled}
		{autocomplete}
		{maxlength}
		class="form-input"
		class:border-red-500={error}
		on:input
		on:change
		on:focus
		on:blur
	/>

	{#if error}
		<p class="form-error">{error}</p>
	{:else if hint}
		<p class="form-hint">{hint}</p>
	{/if}
</div>
